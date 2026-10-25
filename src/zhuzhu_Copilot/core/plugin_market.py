"""插件市场客户端（真实 HTTP，无 mock）。

后端是独立的 Spring Boot 服务（market-server/），默认 http://122.228.216.138:8081，
可用环境变量 ZHZHU_PLUGIN_MARKET_API 或数据目录下的 plugin_market.json 覆盖。

上传链路（强制、不可跳过）：
  1) 本地插件校验系统静态规则 + **本地 LLM 深度评审**（plugin_guard.guard_blobs）；
  2) LLM 不可用（未配置/网络失败/解析失败）直接拒绝上传——市场只收经过深度
     安全校验的包；
  3) 服务器侧再做一轮静态复核，命中即拒收并删除已落盘文件。
账号 token 与开发者/公司名称持久化在数据目录的 plugin_market.json。
"""
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core import plugin_guard

DEFAULT_API_BASE = "http://122.228.216.138:8081"
_ENV_API = "ZHZHU_PLUGIN_MARKET_API"
_TIMEOUT_S = 25.0
_MAX_ZIP_BYTES = 32 * 1024 * 1024      # 服务器上限 20MB，本地先卡
_MAX_ENTRIES = 500
_MAX_TEXT_BYTES = 512_000
_TEXT_EXTS = {".py", ".json", ".md", ".txt", ".js", ".css", ".html", ".sh",
              ".bat", ".ps1", ".yml", ".yaml", ".toml"}


class MarketError(RuntimeError):
    """市场服务不可用或接口报错（消息可直接展示给用户）。"""


# --------------------------------------------------------------- 本地配置
def _config_path() -> Path:
    return app_identity.data_root() / "plugin_market.json"


def _load() -> dict:
    try:
        obj = json.loads(_config_path().read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cfg: dict) -> None:
    p = _config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


def api_base() -> str:
    """市场服务地址（环境变量 > 配置文件 > 默认）。"""
    raw = (os.environ.get(_ENV_API) or _load().get("api_base")
           or DEFAULT_API_BASE)
    return str(raw).strip().rstrip("/")


def set_api_base(url: str) -> tuple:
    url = str(url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        return False, "服务地址需以 http:// 或 https:// 开头"
    cfg = _load()
    cfg["api_base"] = url
    _save(cfg)
    return True, f"插件市场地址已设为 {url}"


def captcha_url() -> str:
    """人机验证页地址（服务端静态页，由内置浏览器加载并渲染 Turnstile）。"""
    return api_base() + "/captcha.html"


def account() -> dict:
    """当前登录信息：{logged_in, username, developer, token}。"""
    cfg = _load()
    token = str(cfg.get("token") or "")
    username = str(cfg.get("username") or "")
    return {"logged_in": bool(token and username), "username": username,
            "developer": str(cfg.get("developer") or username or ""),
            "token": token, "api_base": api_base()}


# ------------------------------------------------------------------ HTTP
def _error_text(code: int, body: str) -> str:
    try:
        obj = json.loads(body or "{}")
        if isinstance(obj, dict) and obj.get("error"):
            return str(obj["error"])
    except ValueError:
        pass
    return f"服务器返回 HTTP {code}"


def _http(path: str, method: str = "GET", payload=None, token: str = "",
          raw: bytes = None, content_type: str = "", timeout: float = _TIMEOUT_S) -> bytes:
    """发请求并返回响应体；失败抛 MarketError（消息可直接展示）。"""
    url = api_base() + path
    data = raw
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise MarketError(_error_text(e.code, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        # 不展示 api_base：避免把源站地址暴露给终端用户
        raise MarketError(f"无法连接插件市场服务器：{e.reason}")
    except OSError as e:
        raise MarketError(f"连接插件市场服务器失败：{e}")


def _json(path: str, method: str = "GET", payload=None, token: str = "",
          timeout: float = _TIMEOUT_S, raw: bytes = None,
          content_type: str = "") -> dict:
    body = _http(path, method, payload=payload, token=token, timeout=timeout,
                 raw=raw, content_type=content_type)
    try:
        obj = json.loads(body.decode("utf-8"))
    except ValueError:
        raise MarketError("服务器响应不是合法 JSON")
    if not isinstance(obj, dict):
        raise MarketError("服务器响应格式异常")
    return obj


def _remember_user(obj: dict, token: str) -> None:
    user = obj.get("user") or {}
    cfg = _load()
    cfg["token"] = token
    cfg["username"] = str(user.get("username") or "")
    cfg["developer"] = str(user.get("developer") or cfg["username"])
    _save(cfg)


# ------------------------------------------------------------- 账号
def register(username: str, password: str, developer: str = "",
             turnstile_token: str = "") -> tuple:
    """注册并自动登录。

    ``turnstile_token`` 为可选：产品口径是「人机验证只用于应用账户登录」，
    市场侧默认不校验；服务端开启 ``market.turnstile.enabled`` 后，这里传入的
    一次性 token 才会被复核（空 token 会被服务端拒绝）。
    """
    payload = {"username": username, "password": password, "developer": developer}
    token_in = str(turnstile_token or "").strip()
    if token_in:
        payload["turnstileToken"] = token_in
    try:
        obj = _json("/api/auth/register", "POST", payload)
    except MarketError as e:
        return False, str(e)
    token = str(obj.get("token") or "")
    if not token:
        return False, "服务器未返回登录令牌"
    _remember_user(obj, token)
    return True, f"注册成功，已登录为 {account()['username']}"


def login(username: str, password: str, turnstile_token: str = "") -> tuple:
    """登录（``turnstile_token`` 同上，默认不需要）。"""
    payload = {"username": username, "password": password}
    token_in = str(turnstile_token or "").strip()
    if token_in:
        payload["turnstileToken"] = token_in
    try:
        obj = _json("/api/auth/login", "POST", payload)
    except MarketError as e:
        return False, str(e)
    token = str(obj.get("token") or "")
    if not token:
        return False, "服务器未返回登录令牌"
    _remember_user(obj, token)
    return True, f"已登录为 {account()['username']}（开发者：{account()['developer']}）"


def logout() -> tuple:
    cfg = _load()
    token = str(cfg.get("token") or "")
    if token:
        try:
            _json("/api/auth/logout", "POST", {}, token=token)
        except MarketError:
            pass
    cfg.pop("token", None)
    cfg.pop("username", None)
    _save(cfg)
    return True, "已退出插件市场账号"


def set_developer(developer: str) -> tuple:
    """修改开发者/公司名称（需登录，1-128 字）。"""
    developer = str(developer or "").strip()
    if not developer:
        return False, "开发者/公司名称不能为空"
    acc = account()
    if not acc["logged_in"]:
        return False, "请先登录插件市场账号"
    try:
        user = _json("/api/auth/developer", "POST",
                     {"developer": developer}, token=acc["token"])
    except MarketError as e:
        return False, str(e)
    cfg = _load()
    cfg["developer"] = str(user.get("developer") or developer)
    _save(cfg)
    return True, f"开发者/公司名称已更新为 {cfg['developer']}"


# ------------------------------------------------------------- 浏览 / 下载
def list_plugins(q: str = "", page: int = 0, size: int = 50) -> tuple:
    """市场插件列表；返回 (ok, {"items": [...], "total": n} 或错误消息)。"""
    query = f"?q={urllib.parse.quote(q or '')}&page={max(int(page), 0)}&size={max(int(size), 1)}"
    try:
        data = _json("/api/plugins" + query)
    except MarketError as e:
        return False, str(e)
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return True, {"items": items, "total": int(data.get("total") or len(items))}


def download_plugin(slug: str, version: str = "") -> tuple:
    """下载插件 zip 字节；返回 (ok, bytes 或错误消息)。"""
    slug = str(slug or "").strip()
    if not slug:
        return False, "缺少插件标识"
    path = f"/api/plugins/{urllib.parse.quote(slug)}/download"
    if version:
        path += f"?version={urllib.parse.quote(version)}"
    try:
        return True, _http(path, timeout=60.0)
    except MarketError as e:
        return False, str(e)


# ------------------------------------------------------------- zip 解析
def _safe_entries(zf: zipfile.ZipFile) -> list:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > _MAX_ENTRIES:
        raise MarketError("插件包条目过多，已拒绝")
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise MarketError(f"插件包内含越界路径：{name}")
    return infos


def _read_texts(zf: zipfile.ZipFile, infos: list) -> dict:
    """读取包内可扫描的文本（跳过二进制/超限条目）。"""
    out = {}
    for i in infos:
        if Path(i.filename).suffix.lower() not in _TEXT_EXTS:
            continue
        if i.file_size > _MAX_TEXT_BYTES:
            continue
        try:
            out[i.filename] = zf.read(i).decode("utf-8", "replace")
        except (OSError, RuntimeError, zipfile.BadZipFile):
            continue
    return out


def _kind_from_names(names) -> str:
    bases = {n.replace("\\", "/").rsplit("/", 1)[-1] for n in names}
    if "source_ops.json" in bases or "copilot.json" in bases:
        return "copilot"
    return "agent" if "plugin.json" in bases else ""


def plugin_kind(zip_path) -> str:
    """插件类型：含 source_ops.json / copilot.json → "copilot"，否则 "agent"。"""
    try:
        with zipfile.ZipFile(Path(zip_path)) as zf:
            return _kind_from_names(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return ""


def plugin_kind_bytes(data: bytes) -> str:
    """同 plugin_kind，入参为 zip 字节（下载后直接判定类型）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return _kind_from_names(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return ""


def _zip_blobs(zf: zipfile.ZipFile, infos: list, texts: dict) -> dict:
    """送审内容：source_ops 里的待写入源码 + 包内辅助文件（与 guard_source_package 一致）。"""
    blobs = {}
    ops_name = next((n for n in texts if n.replace("\\", "/").endswith("source_ops.json")), "")
    if ops_name:
        try:
            ops = json.loads(texts[ops_name])
        except ValueError:
            ops = []
        if isinstance(ops, list):
            for i, op in enumerate(ops):
                if not isinstance(op, dict):
                    continue
                content = str(op.get("content") or "")
                if content.strip():
                    blobs[f"source_ops[{i}].{op.get('op')}:{op.get('path') or ''}"] = content
    for name, text in texts.items():
        if name.rsplit("/", 1)[-1] in ("plugin.json", "source_ops.json"):
            continue
        blobs[name] = text
    return blobs


def precheck_zip(zip_path, llm_review=None) -> tuple:
    """上传前本地深度安全校验。

    返回 (ok, message, detail)；detail 含 llm_used / llm / dangerous_files。
    LLM 不可用时直接拒绝（市场只收经过本地 LLM 深度评审的插件）。
    llm_review 仅供测试注入替身，生产调用不传（走真实 LLM）。
    """
    p = Path(zip_path or "")
    if not p.is_file():
        return False, "插件包不存在", {}
    if p.stat().st_size > _MAX_ZIP_BYTES:
        return False, "插件包过大（>32MB），已拒绝", {}
    try:
        with zipfile.ZipFile(p) as zf:
            infos = _safe_entries(zf)
            texts = _read_texts(zf, infos)
            blobs = _zip_blobs(zf, infos, texts)
    except (OSError, zipfile.BadZipFile) as e:
        return False, f"插件包无法解析：{e}", {}
    except MarketError as e:
        return False, str(e), {}
    if not blobs:
        return False, "插件包内没有可评审的源码，已拒绝", {}
    try:
        meta = json.loads(texts.get("plugin.json") or "{}")
    except ValueError:
        meta = {}
    name = str((meta or {}).get("name") or p.stem) if isinstance(meta, dict) else p.stem
    ok, msg, detail = plugin_guard.guard_blobs(blobs, plugin=name,
                                               llm_review=llm_review)
    if not ok:
        return False, msg, detail
    if not detail.get("llm_used"):
        return False, ("本地 LLM 深度安全校验不可用（未配置模型或调用失败），"
                       "市场只接受通过深度评审的插件，已拒绝上传"), detail
    return True, f"本地深度安全校验通过（静态规则 + LLM 源码评审）：{name}", detail


def _multipart(fields: dict, filename: str, data: bytes) -> tuple:
    boundary = "----zhuzhu" + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n".encode("utf-8"))
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_zip(zip_path, name: str = "", description: str = "",
               version: str = "1.0.0", developer: str = "",
               llm_review=None) -> tuple:
    """上传插件到市场：本地深度校验 → multipart 上传（需登录）。"""
    acc = account()
    if not acc["logged_in"]:
        return False, "请先登录插件市场账号（上传需要登录）"
    p = Path(zip_path or "")
    ok, msg, detail = precheck_zip(p, llm_review=llm_review)
    if not ok:
        return False, msg
    try:
        meta = json.loads(zipfile.ZipFile(p).read("plugin.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        meta = {}
    if isinstance(meta, dict) and not str(meta.get("name") or "").strip():
        meta = {}
    name = str(name or (meta or {}).get("name") or p.stem).strip()
    description = str(description or (meta or {}).get("description") or "").strip()
    developer = str(developer or acc["developer"] or acc["username"]).strip()
    fields = {"name": name, "description": description, "version": version,
              "developer": developer, "guardVerdict": "safe",
              "guardDetail": str((detail.get("llm") or {}).get("reason") or "本地深度校验通过")}
    body, ctype = _multipart(fields, p.name, p.read_bytes())
    try:
        # 服务器会二次静态复核；命中即拒收并删除文件
        obj = _json("/api/plugins", "POST", token=acc["token"], timeout=120.0,
                    raw=body, content_type=ctype)
    except MarketError as e:
        return False, str(e)
    return _upload_result(obj, name)


def _upload_result(obj: dict, name: str) -> tuple:
    if obj.get("ok") is False:
        warn = obj.get("warnings") or []
        return False, "服务器复核对插件提出了警告：" + "；".join(map(str, warn))
    return True, f"插件「{name}」已上传到市场（{obj.get('slug')} v{obj.get('version')}）"


# ------------------------------------------------------------- 导入本地
def import_zip_bytes(data: bytes, filename: str = "plugin.zip") -> tuple:
    """把下载到的 zip 导入本地插件体系（自动识别 Copilot / Agent 插件）。"""
    import shutil
    import tempfile
    from zhuzhu_Copilot.core import agent_plugins, copilot_plugins
    d = Path(tempfile.mkdtemp(prefix="zhzhu_market_"))
    tmp = d / (filename or "plugin.zip")
    try:
        tmp.write_bytes(data)
        kind = plugin_kind(tmp)
        if kind == "copilot":
            return copilot_plugins.import_plugin_zip(str(tmp))
        if kind == "agent":
            return agent_plugins.import_plugin_zip(str(tmp))
        return False, "插件包缺少 plugin.json，无法识别类型"
    except OSError as e:
        return False, f"写入临时文件失败：{e}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def download_into_local(slug: str, version: str = "") -> tuple:
    """从市场下载并导入本地（Copilot / Agent 插件自动分流）。"""
    ok, data = download_plugin(slug, version)
    if not ok:
        return False, str(data)
    return import_zip_bytes(data, f"{slug or 'plugin'}.zip")
