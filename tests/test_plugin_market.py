# -*- coding: utf-8 -*-
"""插件市场客户端回归（真实 HTTP：本地起一个最小市场服务，不用 mock 替身）。

覆盖：
1. 上传前本地深度安全校验：危险包拦截、LLM 不可用拒绝、LLM 判危险拒绝；
2. 账号：注册（开发者默认用户名）/ 登录 / 修改开发者 / 退出；
3. 上传 → 列表 → 下载 → 导入本地 的完整往返。
"""
import http.server
import json
import os
import re
import sys
import threading
import urllib.parse
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                       # noqa: E402

from zhuzhu_Copilot.core import (                                   # noqa: E402
    agent_plugins, agent_skills, copilot_plugins, plugin_guard, plugin_market,
)

EXAMPLES = (Path(__file__).resolve().parents[1] / "src" / "zhuzhu_Copilot"
            / "copilot_plugins_examples")

#: 本地最小市场服务的状态（每个用例重建）
STATE = {"users": {}, "tokens": {}, "plugins": []}


def _multipart(body: bytes, ctype: str) -> dict:
    """极简 multipart 解析：字段名 → 值（file 字段即 zip 字节）。"""
    boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
    out = {}
    for chunk in body.split(("--" + boundary).encode("utf-8")):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--" or b"\r\n\r\n" not in chunk:
            continue
        head, val = chunk.split(b"\r\n\r\n", 1)
        m = re.search(rb'name="([^"]+)"', head)
        if m:
            out[m.group(1).decode("utf-8")] = val.rstrip(b"\r\n")
    return out


class _MarketHandler(http.server.BaseHTTPRequestHandler):
    """最小市场服务：注册/登录/改开发者/列表/上传/下载（与 market-server 接口一致）。"""

    def log_message(self, *args):
        pass

    # ---------------------------------------------------------------- 工具
    def _send(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _user(self) -> str:
        h = self.headers.get("Authorization") or ""
        token = h[7:] if h.startswith("Bearer ") else ""
        return STATE["tokens"].get(token, "")

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/plugins":
            q = (urllib.parse.parse_qs(u.query).get("q") or [""])[0].strip()
            items = [p["view"] for p in STATE["plugins"]
                     if not q or q in p["view"]["name"] or q in p["view"]["description"]]
            return self._send(200, {"items": items, "total": len(items),
                                    "page": 0, "size": len(items)})
        m = re.fullmatch(r"/api/plugins/([^/]+)/download", u.path)
        if m:
            for p in STATE["plugins"]:
                if p["view"]["slug"] == m.group(1):
                    return self._send_bytes(p["data"])
            return self._send(404, {"error": "插件不存在"})
        return self._send(404, {"error": "未实现"})

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ctype = self.headers.get("Content-Type") or ""
        if path == "/api/auth/register":
            return self._register(json.loads(body or b"{}"))
        if path == "/api/auth/login":
            return self._login(json.loads(body or b"{}"))
        if path == "/api/auth/logout":
            h = self.headers.get("Authorization") or ""
            STATE["tokens"].pop(h[7:] if h.startswith("Bearer ") else "", None)
            return self._send(200, {"ok": True})
        if path == "/api/auth/developer":
            return self._set_developer(json.loads(body or b"{}"))
        if path == "/api/plugins":
            return self._upload(body, ctype)
        return self._send(404, {"error": "未实现"})

    # ---------------------------------------------------------------- 账号
    def _register(self, obj: dict):
        username = str(obj.get("username") or "").lower()
        password = str(obj.get("password") or "")
        developer = str(obj.get("developer") or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{3,32}", username) or len(password) < 8:
            return self._send(400, {"error": "用户名 3-32 位，密码至少 8 位"})
        if username in STATE["users"]:
            return self._send(400, {"error": "用户名已存在"})
        STATE["users"][username] = {"password": password,
                                    "developer": developer or username}
        token = "tok-" + username
        STATE["tokens"][token] = username
        return self._send(200, {"token": token, "user": {
            "username": username, "developer": STATE["users"][username]["developer"]}})

    def _login(self, obj: dict):
        username = str(obj.get("username") or "").lower()
        password = str(obj.get("password") or "")
        u = STATE["users"].get(username)
        if not u or u["password"] != password:
            return self._send(401, {"error": "用户名或密码错误"})
        token = "tok-" + username
        STATE["tokens"][token] = username
        return self._send(200, {"token": token, "user": {
            "username": username, "developer": u["developer"]}})

    def _set_developer(self, obj: dict):
        username = self._user()
        if not username:
            return self._send(401, {"error": "需要登录"})
        dev = str(obj.get("developer") or "").strip()
        if not dev:
            return self._send(400, {"error": "开发者/公司名称不能为空"})
        STATE["users"][username]["developer"] = dev
        return self._send(200, {"username": username, "developer": dev})

    # ---------------------------------------------------------------- 上传
    def _upload(self, body: bytes, ctype: str):
        username = self._user()
        if not username:
            return self._send(401, {"error": "需要登录"})
        fields = _multipart(body, ctype)
        name = fields.get("name", b"").decode("utf-8")
        version = fields.get("version", b"1.0.0").decode("utf-8")
        if not name or "file" not in fields:
            return self._send(400, {"error": "缺少名称或文件"})
        if fields.get("guardVerdict", b"safe").decode("utf-8") == "dangerous":
            return self._send(400, {"error": "本地深度安全校验判定该插件危险，市场拒绝接收"})
        data = fields["file"]
        slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
        STATE["plugins"].append({"data": data, "view": {
            "slug": slug, "name": name, "version": version,
            "description": fields.get("description", b"").decode("utf-8"),
            "developer": fields.get("developer", username.encode()).decode("utf-8"),
            "size": len(data), "downloads": 0, "guardVerdict": "safe",
            "serverScan": "pass"}})
        return self._send(200, {"ok": True, "slug": slug, "version": version,
                                "serverScan": "pass", "warnings": []})


@pytest.fixture()
def market(tmp_path, monkeypatch):
    """隔离插件目录 + 本地市场服务；登录态重定向到临时数据目录。"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR",
                        tmp_path / "copilot_plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    agent_skills._MCP_CACHE["data"] = None
    STATE["users"], STATE["tokens"], STATE["plugins"] = {}, {}, []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MarketHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ZHZHU_PLUGIN_MARKET_API",
                       f"http://127.0.0.1:{srv.server_address[1]}")
    cfg = plugin_market._config_path()
    if cfg.exists():
        cfg.unlink()
    yield STATE
    srv.shutdown()


def _zip(tmp_path: Path, name: str = "demo", files: dict = None) -> Path:
    p = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, text in (files or {}).items():
            zf.writestr(rel, text)
    return p


def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    return dest


SAFE_FILES = {
    "plugin.json": json.dumps({"name": "demo", "kind": "agent",
                               "description": "示例工具插件"}),
    "tools.py": "def hello():\n    return 'hi'\n",
}
_safe_review = lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"}   # noqa: E731


# --------------------------------------------------------- 上传前深度校验
def test_precheck_blocks_dangerous_zip(tmp_path):
    """静态规则命中 eval → 直接判危险，无需 LLM。"""
    zp = _zip(tmp_path, "bad", dict(SAFE_FILES, **{"bad.py": "eval('1')\n"}))
    ok, msg, detail = plugin_market.precheck_zip(zp)
    assert not ok and "危险" in msg
    assert detail.get("dangerous_files")


def test_precheck_rejects_when_llm_unavailable(tmp_path):
    """LLM 不可用 → 拒绝上传（市场只收经过深度评审的包）。"""
    zp = _zip(tmp_path, "demo", SAFE_FILES)
    ok, msg, _ = plugin_market.precheck_zip(zp, llm_review=lambda blobs: {})
    assert not ok and "LLM" in msg


def test_precheck_rejects_when_llm_says_dangerous(tmp_path):
    zp = _zip(tmp_path, "demo", SAFE_FILES)
    ok, msg, detail = plugin_market.precheck_zip(
        zp, llm_review=lambda blobs: {"verdict": "dangerous", "reason": "外发隐私"})
    assert not ok and detail.get("llm_used")


def test_precheck_passes_with_llm(tmp_path):
    zp = _zip(tmp_path, "demo", SAFE_FILES)
    ok, msg, detail = plugin_market.precheck_zip(zp, llm_review=_safe_review)
    assert ok and detail.get("llm_used")
    assert plugin_market.plugin_kind(zp) == "agent"


def test_precheck_scans_source_ops_content(tmp_path):
    """源码级插件：source_ops 里的待写入代码同样送审（不能只看包内文件）。"""
    files = {
        "plugin.json": json.dumps({"name": "srcplug", "kind": "copilot"}),
        "source_ops.json": json.dumps([{
            "op": "create_file", "path": "zhuzhu_Copilot/core/x.py",
            "content": "import os\nos.system('whoami')\n"}]),
    }
    ok, msg, _ = plugin_market.precheck_zip(_zip(tmp_path, "srcplug", files))
    assert not ok and "危险" in msg


def test_precheck_rejects_traversal(tmp_path):
    zp = _zip(tmp_path, "evil", {"plugin.json": "{}", "../evil.py": "x = 1\n"})
    ok, msg, _ = plugin_market.precheck_zip(zp)
    assert not ok and "越界路径" in msg


# ------------------------------------------------------------- 账号
def test_register_defaults_developer_to_username(market):
    ok, msg = plugin_market.register("alice_1", "password123", "")
    assert ok, msg
    acc = plugin_market.account()
    assert acc["logged_in"] and acc["username"] == "alice_1"
    assert acc["developer"] == "alice_1"


def test_login_and_set_developer(market):
    plugin_market.register("bob_2", "password123", "鲍勃工作室")
    assert plugin_market.account()["developer"] == "鲍勃工作室"
    assert plugin_market.logout()[0]
    assert not plugin_market.account()["logged_in"]
    ok, msg = plugin_market.login("bob_2", "password123")
    assert ok, msg
    ok, msg = plugin_market.set_developer("新公司")
    assert ok, msg
    assert plugin_market.account()["developer"] == "新公司"


def test_login_rejects_bad_password(market):
    plugin_market.register("carol_3", "password123")
    plugin_market.logout()
    ok, msg = plugin_market.login("carol_3", "wrong-pass")
    assert not ok and "密码" in msg


# ------------------------------------------------- 上传 → 列表 → 下载 → 导入
def test_upload_list_download_import_roundtrip(market, tmp_path):
    plugin_market.register("dave_4", "password123", "戴夫软件")
    zp = _zip_dir(EXAMPLES / "custom-hello-tool", tmp_path / "hello.zip")
    ok, msg = plugin_market.upload_zip(str(zp), name="custom-hello-tool",
                                       description="打招呼工具",
                                       version="1.0.0", developer="",
                                       llm_review=_safe_review)
    assert ok, msg
    assert market["plugins"][0]["view"]["developer"] == "戴夫软件"   # 空开发者取账号名

    ok, data = plugin_market.list_plugins(q="hello")
    assert ok and data["items"] and data["items"][0]["slug"] == "custom-hello-tool"

    ok, raw = plugin_market.download_plugin("custom-hello-tool", "1.0.0")
    assert ok and isinstance(raw, bytes) and plugin_market.plugin_kind_bytes(raw) == "copilot"

    ok, msg = plugin_market.import_zip_bytes(raw, "custom-hello-tool.zip")
    assert ok, msg
    assert copilot_plugins.get_plugin("custom-hello-tool")["kind"] == "copilot"


def test_upload_requires_login(market, tmp_path):
    zp = _zip(tmp_path, "demo", SAFE_FILES)
    ok, msg = plugin_market.upload_zip(str(zp), llm_review=_safe_review)
    assert not ok and "登录" in msg


def test_guard_dangerous_verdict_rejected_by_market(market, tmp_path):
    """即使客户端声称安全，服务器侧一旦判危险也必须拒收。"""
    from zhuzhu_Copilot.core import plugin_market as pm
    plugin_market.register("erin_5", "password123")
    zp = _zip(tmp_path, "demo", SAFE_FILES)
    ok, _ = pm.upload_zip(str(zp), llm_review=_safe_review)
    assert ok
    # 手工构造 guardVerdict=dangerous 的上传请求
    body, ctype = pm._multipart({"name": "demo2", "version": "1.0.0",
                                 "guardVerdict": "dangerous"}, "demo2.zip",
                                zp.read_bytes())
    with pytest.raises(pm.MarketError):
        pm._json("/api/plugins", "POST", token=pm.account()["token"],
                 raw=body, content_type=ctype)


# ------------------------------------------------------------- UI 冒烟
def test_market_page_uses_card_board_and_buttons_below(market, tmp_path):
    """市场页：卡片看板（最多 10 张可视）+ 按钮在卡片下方；列表能拉到真实服务数据。"""
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui import agent_panel as ap

    app = QApplication.instance() or QApplication([])
    plugin_market.register("frank_6", "password123")
    zp = _zip_dir(EXAMPLES / "custom-hello-tool", tmp_path / "hello.zip")
    plugin_market.upload_zip(str(zp), name="custom-hello-tool",
                             llm_review=_safe_review)

    dlg = ap._AgentSettingsDialog()
    idx = [k for _, k in ap._NAV_ITEMS].index("market")
    dlg.nav.setCurrentRow(idx)
    for _ in range(200):          # 等后台拉取结果回主线程（本地服务，通常 <100ms）
        app.processEvents()
        if dlg._market_rows:
            break
        threading.Event().wait(0.01)
    assert dlg.market_list is not None
    assert dlg.market_list.MAX_VISIBLE == 10
    assert dlg._market_rows and dlg.market_list.count() == len(dlg._market_rows)
    page = dlg.stack.currentWidget()
    page = page.widget() if hasattr(page, "widget") else page   # 页面套了滚动容器
    lay = page.layout()

    def _row_of(widget) -> int:
        for i in range(lay.count()):
            it = lay.itemAt(i)
            if it.widget() is widget:
                return i
            sub = it.layout()
            if sub is not None and any(
                    sub.itemAt(j).widget() is widget for j in range(sub.count())):
                return i
        return -1

    assert 0 <= _row_of(dlg.market_list) < _row_of(dlg.market_upload_b), \
        "按钮行必须在卡片看板下方"
    dlg.close()
    dlg.deleteLater()
    app.processEvents()
