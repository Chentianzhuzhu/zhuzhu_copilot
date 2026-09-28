# DeepSeek 网页版免费接入 Implementation Plan（浏览器桥版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以「本地 OpenAI 兼容代理 + CDP 浏览器桥」方式接入 chat.deepseek.com 网页版对话，使现有 agent 引擎（工具/skill/插件/MCP 路由框架）零改动免费使用 DeepSeek 模型。

**Architecture:** PoC 已实证（2026-08-30，`scripts/poc_deepseek_web.py` 迭代 8 版）：网页版接口 `/api/v0/chat/completion` 需要动态 PoW 挑战解 + `x-hif-*` 加密签名（页面 JS 生成、无法外部复刻），任何外部直连均被 WAF/服务端拒绝。唯一可行路径为「**浏览器 UI 桥**」：CDP 真实键入+回车触发页面自身发送流程（页面自动完成全部签名），`main` 容器 innerText diff 提取流式渲染的回复，清洗尾部 UI 杂音后组装为 OpenAI SSE 文本响应。对外接口不变：本地 ThreadingHTTPServer 提供 `/v1/chat/completions` 与 `/v1/models`，引擎零改动，经 `agent_llm.load_model_config()` 对 `kind=deepseek_web` 服务商幂等注入 base_url。

**Tech Stack:** Python 标准库（http.server / threading / json），复用现有 CDP 浏览器（`agent_browser.BrowserController` + `Input.insertText` / `Input.dispatchKeyEvent` / `Page.captureScreenshot`），零新增第三方依赖（兼容 Cython 打包）。

**Spec:** `docs/superpowers/specs/2026-08-30-deepseek-web-design.md`

---

## 文件结构

- Create: `src/zhuzhu_Copilot/core/agent_web_llm.py` — 浏览器桥（凭证检查/对话执行）+ 本地中转代理
- Create: `scripts/test_web_llm.py` — 单元测试（prompt 序列化 / 杂音清洗 / /v1/models）
- Modify: `src/zhuzhu_Copilot/core/agent_llm.py` — 预设服务商 + load_model_config 注入
- Modify: `src/zhuzhu_Copilot/ui/agent_panel.py` — ProviderDialog 网页版支持

## PoC 已确认事实（Task 1 已完成，commit 6933075）

- 登录态在 **localStorage**（`userToken` 为 `{"value":...}` JSON 包装）；Cookie 无认证信息
- 真实接口：`POST /api/v0/chat/completion`，需动态 `x-ds-pow-response` + `x-hif-dliq/x-hif-leim`（仅页面自身发送流程可生成）
- 页面全局 fetch 被 hook（防采集层），但不会自动补 `x-hif-*` 签名 → 外部调用恒 `40300 MISSING_HEADER`
- 浏览器桥可行：`Input.insertText` 键入（len 15 确认）+ Enter `dispatchKeyEvent` 发送 → 输入框清空即发送成功 → `main.innerText` diff 提取回复（实测 40 字符）
- 回复尾部杂音：「深度思考」「智能搜索」「内容由 AI 生成，请仔细甄别」等 UI 标签，需清洗
- 每轮对话前需新建会话（reload 首页默认新对话），保证上下文干净
- 浏览器 profile 持久化登录态（`~/.zhuzhu_Copilot/browser_profile`），重启免重复登录

---

## Task 2: agent_web_llm.py 基座（常量/凭证/桥骨架）

**Files:**
- Create: `src/zhuzhu_Copilot/core/agent_web_llm.py`（本任务实现常量 + 浏览器就绪 + 对话执行骨架）
- Test: `scripts/test_web_llm.py`（纯函数段：serialize_prompt / strip_ui_noise）

- [ ] **Step 1: 写失败测试**

`scripts/test_web_llm.py`:

```python
"""agent_web_llm 单元测试：prompt 序列化与 UI 杂音清洗（纯函数，无需浏览器）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from zhuzhu_Copilot.core import agent_web_llm as W


def test_serialize_prompt():
    msgs = [{"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"}]
    out = W.serialize_prompt(msgs)
    assert "[系统指令]" in out and "你是助手" in out
    assert "[用户]" in out and "你好" in out
    assert "[助手]" in out and "你好！" in out


def test_serialize_skips_tool_messages():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "查天气"}]},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "晴"}]
    out = W.serialize_prompt(msgs)
    assert "查天气" in out
    assert "get_weather" not in out          # 工具消息不注入
    assert "tool" not in out


def test_strip_ui_noise():
    raw = "成功\n\n浏览器桥测试成功。\n\n深度思考\n智能搜索\n内容由 AI 生成，请仔细甄别"
    out = W.strip_ui_noise(raw)
    assert "浏览器桥测试成功" in out
    assert "内容由 AI 生成" not in out
    assert not out.endswith("请仔细甄别")


def test_strip_no_noise_keeps_text():
    raw = "正常回复内容"
    assert W.strip_ui_noise(raw) == "正常回复内容"


for _n in ("test_serialize_prompt", "test_serialize_skips_tool_messages",
           "test_strip_ui_noise", "test_strip_no_noise_keeps_text"):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_web_llm.py`
Expected: FAIL（`No module named 'zhuzhu_Copilot.core.agent_web_llm'`）

- [ ] **Step 3: 实现基座**

`src/zhuzhu_Copilot/core/agent_web_llm.py`（Task 2 部分，Task 3 在此基础上追加代理）：

```python
"""DeepSeek 网页版接入：CDP 浏览器桥 + 本地 OpenAI 兼容代理。

PoC 实证（2026-08-30）：网页版接口需要页面 JS 动态生成的 PoW 解与 x-hif-* 签名，
外部直连恒被拒；唯一可行路径是驱动页面自身发送流程（真实键入+回车），从 DOM
读回流式渲染的回复文本。本模块把该流程封装为 OpenAI 兼容的本地代理，引擎零改动。

实现要点：
- 浏览器即凭证：登录态持久化在 ~/.zhuzhu_Copilot/browser_profile，无额外凭证文件
- 每次对话前 reload 首页新建会话，保证上下文干净（历史由上层拼接进 prompt）
- 全标准库实现，兼容 Cython 打包
"""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zhuzhu_Copilot.core import agent_browser

WEB_KIND = "deepseek_web"
MODELS = ["deepseek-chat"]          # 对外暴露的模型名（网页版无深度思考开关名）
MODEL_FREE_DESC = "DeepSeek 网页版（免费）"

# 回复尾部 UI 杂音标记（页面固定在回复区的标签/免责声明），清洗时按最先出现位置截断
UI_NOISE_MARKS = [
    "内容由 AI 生成，请仔细甄别",
    "内容由AI生成，请仔细甄别",
    "深度思考",
    "智能搜索",
]

_BRIDGE = None
_LOCK = threading.Lock()


# ---------- 纯函数 ----------
def serialize_prompt(messages: list) -> str:
    """把 OpenAI messages 序列化为单段 prompt（网页版无角色数组，只接受一个 prompt）。

    工具消息（role=tool / assistant.tool_calls）不注入 prompt；多模态 content 数组
    只取其中的文本段。"""
    lines = []
    for m in messages or []:
        role = str(m.get("role") or "").lower()
        content = m.get("content") or ""
        if isinstance(content, list):        # OpenAI 多模态 content 数组
            content = " ".join(
                str(x.get("text") or "") for x in content
                if isinstance(x, dict) and x.get("type") == "text")
        content = str(content).strip()
        if role == "tool" or (role == "assistant" and m.get("tool_calls")):
            continue                          # 工具回执不注入 prompt
        if role == "system":
            lines.append(f"[系统指令]\n{content}")
        elif role == "assistant":
            lines.append(f"[助手]\n{content}")
        else:
            lines.append(f"[用户]\n{content}")
    return "\n\n".join(lines)


def strip_ui_noise(text: str) -> str:
    """剔除回复尾部页面 UI 杂音（末尾纯噪声行 + 免责声明截断）。

    UI 标签固定在回复区末尾独立行，先按行从尾部剔除；免责声明可能嵌在末行，
    再用强标记截断。避免用「最先出现位置」误剪正文（如用户问什么是深度思考）。"""
    if not text:
        return ""
    noise = set(UI_NOISE_MARKS)
    lines = text.rstrip().splitlines()
    while lines and lines[-1].strip() in noise:
        lines.pop()
    out = "\n".join(lines).strip()
    for mark in ("内容由 AI 生成，请仔细甄别", "内容由AI生成，请仔细甄别"):
        i = out.find(mark)
        if i >= 0:
            out = out[:i].rstrip()
            break
    return out.strip()


# ---------- 浏览器桥 ----------
class WebBridge:
    """封装 CDP 页面驱动作业：ensure_ready / ask / close。"""

    HOME = "https://chat.deepseek.com"
    _LOGIN_PROBE = ("(!!document.querySelector('[class*=textarea], [class*=input-area], "
                    'textarea, [contenteditable="true"]\'))')
    _INPUT_LEN = ("(() => { const b = document.querySelector"
                  "('textarea,[contenteditable=\"true\"]'); if(!b) return -1;"
                  " return (b.value !== undefined ? b.value : b.innerText||'').length; })()")
    _MAIN_TEXT = ("(() => { const m = document.querySelector('main'); "
                  "return m ? m.innerText : document.body.innerText; })()")
    _STOP_EXISTS = ("(!!document.querySelector('button[class*=stop],"
                    "button[aria-label*=\"停止\"],[class*=stop-generate],"
                    "[class*=generate-over]'))")

    def __init__(self):
        self._ctrl = None

    def ensure_ready(self, wait_login: float = 180.0) -> tuple:
        """启动独立浏览器并导航首页；校验登录态（输入框出现）。返回 (ok, msg)。"""
        ctrl = agent_browser.controller()
        ok, msg = ctrl.start()
        if not ok:
            return False, msg
        ctrl.navigate(self.HOME)
        deadline = time.time() + wait_login
        while time.time() < deadline:
            st = ctrl.eval(self._LOGIN_PROBE)
            if st and st.get("ok") and st.get("text") == "true":
                break
            time.sleep(2)
        else:
            return False, "未检测到登录态（聊天输入框未出现），请在浏览器中完成一次登录后重试"
        self._ctrl = ctrl
        time.sleep(0.8)          # 等首屏稳定，供 base 基线采集
        return True, "浏览器桥已就绪"

    def ask(self, prompt: str, on_delta=None, stop=None, wait=150.0) -> str:
        """驱动一次对话：新建会话 → 键入 → 回车 → 轮询 DOM diff → 完整回复。

        on_delta(str)：流式渲染增量回调（页面逐段渲染期间以新增文本逐段回调，
        模拟流式输出）；stop() 返回 True 时中止等待并返回已收集文本。
        """
        assert self._ctrl is not None, "请先 ensure_ready()"
        ctrl = self._ctrl
        # 1) 新建会话：reload 首页（登录态在 localStorage，不丢失）
        ctrl.navigate(self.HOME)
        deadline = time.time() + 60
        while time.time() < deadline:
            st = ctrl.eval(self._LOGIN_PROBE)
            if st and st.get("ok") and st.get("text") == "true":
                break
            time.sleep(1)
        time.sleep(1.2)
        # 2) 采集基线
        base = self._main_text()
        # 3) 聚焦 + 真实键入
        ctrl.eval("(() => {const b=document.querySelector"
                  "('textarea,[contenteditable=\"true\"]'); if(!b)return'NO_BOX';"
                  " b.focus(); b.scrollIntoView({block:'center'}); return 'OK';})()")
        ctrl._call("Input.insertText", {"text": prompt},
                   session=ctrl._active_session)
        time.sleep(0.3)
        # 4) 回车发送
        for t in ("keyDown", "keyUp"):
            ctrl._call("Input.dispatchKeyEvent", {
                "type": t, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                session=ctrl._active_session)
        # 5) 轮询 main 文本增量（伪流式）
        last = base
        full = base
        stable = 0
        deadline = time.time() + wait
        while time.time() < deadline:
            if stop and stop():
                break
            time.sleep(0.6)
            try:
                cur = self._main_text()
            except Exception:
                continue
            stop_on = self._stop_exists()
            if cur != last:                    # 有增量 → 逐段回调
                delta = cur[len(last):] if cur.startswith(last) else cur
                full = cur
                last = cur
                stable = 0
                if on_delta and delta:
                    on_delta(delta)
            elif len(cur) > len(base) and stop_on is False:
                stable += 1
            if stable >= 8 and stop_on is False:
                break
        return strip_ui_noise(full[len(base):] if len(full) > len(base) else full)

    # ---------- 内部 ----------
    def _main_text(self) -> str:
        out = self._ctrl.eval(self._MAIN_TEXT)
        return out.get("text", "") if out.get("ok") else ""

    def _stop_exists(self) -> bool:
        out = self._ctrl.eval(self._STOP_EXISTS)
        return out.get("text") == "true"

    def close(self):
        try:
            if self._ctrl is not None:
                self._ctrl.stop()
        except Exception:
            pass
        self._ctrl = None


def bridge() -> WebBridge:
    global _BRIDGE
    if _BRIDGE is None:
        with _LOCK:
            if _BRIDGE is None:
                _BRIDGE = WebBridge()
    return _BRIDGE


def has_valid_credentials() -> bool:
    """轻量登录态探测：已曾启动过浏览器桥且登录过即视为有效（细粒度由 ensure_ready 判定）。"""
    return (Path.home() / ".zhuzhu_Copilot" / "browser_profile").exists()


def login_now() -> tuple:
    """供 UI 调用：后台线程启动浏览器桥并引导登录。返回 (ok, msg)。"""
    try:
        return bridge().ensure_ready(wait_login=240.0)
    except Exception as e:
        return False, f"引导登录失败: {e}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_web_llm.py`
Expected: PASS（4 项全过）

- [ ] **Step 5: Commit**

```bash
git add src/zhuzhu_Copilot/core/agent_web_llm.py scripts/test_web_llm.py
git commit -m "feat(web-llm): DeepSeek 网页版浏览器桥基座（序列化/清洗/键入发送）"
```

---

## Task 3: 本地中转代理（OpenAI 兼容 SSE 组装）

**Files:**
- Modify: `src/zhuzhu_Copilot/core/agent_web_llm.py`（追加 `start_proxy/stop_proxy/ensure_web_proxy/_Handler`）
- Modify: `scripts/test_web_llm.py`（追加 /v1/models 测试）

- [ ] **Step 1: 追加失败测试**

向 `scripts/test_web_llm.py` 追加：

```python
import json as _json
import urllib.request


def test_models_endpoint():
    W.ensure_web_proxy(kind_check=False)
    base = W._PROXY.base_url
    with urllib.request.urlopen(base + "/models", timeout=5) as r:
        d = _json.loads(r.read().decode("utf-8"))
    ids = [x["id"] for x in d.get("data", [])]
    assert "deepseek-chat" in ids
    W.stop_proxy()


for _n in ("test_models_endpoint",):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_web_llm.py`
Expected: FAIL（`ensure_web_proxy` 不存在 / `_PROXY is None` 的 AssertionError）

- [ ] **Step 3: 实现本地代理**

向 `agent_web_llm.py` 追加：

```python
# ---------- 本地中转代理 ----------
class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json(200, {"object": "list",
                             "data": [{"id": n, "object": "model"} for n in MODELS]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(ln).decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "invalid json body"})
            return
        try:
            prompt = serialize_prompt(payload.get("messages") or [])
        except Exception as e:
            self._json(400, {"error": f"prompt 序列化失败: {e}"})
            return
        # 先确保浏览器桥就绪（登录态校验），失败直接返回错误，不消耗后续流式响应
        try:
            okb, msgb = bridge().ensure_ready(wait_login=90.0)
        except Exception as e:
            okb, msgb = False, f"浏览器桥启动失败: {e}"
        if not okb:
            self._json(401, {"error": msgb, "code": "WEB_NOT_READY"})
            return
        stream = bool(payload.get("stream"))
        _req_id = uuid.uuid4().hex
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.flush()
            seq = 0

            def _delta(txt):
                nonlocal seq
                seq += 1
                chunk = {"id": _req_id, "object": "chat.completion.chunk",
                         "created": int(time.time()), "model": "deepseek-chat",
                         "choices": [{"index": 0,
                                      "delta": {"content": txt},
                                      "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            try:
                bridge().ask(prompt, on_delta=_delta)
            except Exception as e:
                _delta(f"\n[网页版调用失败: {e}]")
            end = {"id": _req_id, "object": "chat.completion.chunk",
                   "created": int(time.time()), "model": "deepseek-chat",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(end, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            parts = []
            try:
                bridge().ask(prompt, on_delta=parts.append)
            except Exception as e:
                parts.append(f"\n[网页版调用失败: {e}]")
            self._json(200, {"id": _req_id, "object": "chat.completion",
                             "created": int(time.time()), "model": "deepseek-chat",
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant",
                                                      "content": "".join(parts)},
                                          "finish_reason": "stop"}]})


def start_proxy() -> dict:
    """幂等启动本地代理（仅 WEB_KIND 服务商使用）。返回 {ok, base_url, msg}。"""
    if _PROXY is not None:
        return {"ok": True, "base_url": _PROXY.base_url, "msg": "代理运行中"}
    try:
        srv = _ProxyServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _PROXY = srv
        return {"ok": True, "base_url": srv.base_url, "msg": "代理已启动"}
    except Exception as e:
        return {"ok": False, "base_url": "", "msg": f"代理启动失败: {e}"}


def stop_proxy():
    global _PROXY
    if _PROXY is not None:
        try:
            _PROXY.shutdown()
            _PROXY.server_close()
        except Exception:
            pass
        _PROXY = None


def ensure_web_proxy(kind_check: bool = True) -> dict:
    """load_model_config 注入点调用：幂等启动本地代理（kind=deepseek_web 时走此路径）。
    kind_check=True 时仅会替代已经被点击的网页版服务商 base_url（恒 ok 启动代理）。"""
    r = start_proxy()
    return r


_PROXY = None
```

（同时把 `_PROXY = None` 前的模块级定义放文件顶部 `_PROXY = None` 处——注意 Task 2 基座已在 `_LOCK` 定义附近，追加 `_PROXY = None` 时删除本段末尾重复定义，保持单一定义。）

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_web_llm.py`
Expected: PASS（6 项全过）

- [ ] **Step 5: 人工复核浏览器桥行为**

Run: `python -c "import sys; sys.path.insert(0,'src'); from zhuzhu_Copilot.core import agent_web_llm as W; ok,msg=W.bridge().ensure_ready(); print(ok,msg); print(W.bridge().ask('只回复：桥测试OK', wait=90))"`
Expected: 输出 `桥测试OK` 相关回复（需浏览器可见，登录态已持久化）。

- [ ] **Step 6: Commit**

```bash
git add src/zhuzhu_Copilot/core/agent_web_llm.py scripts/test_web_llm.py
git commit -m "feat(web-llm): OpenAI 兼容本地代理 + SSE 流式组装"
```

---

## Task 4: 引擎接入（preset + load_model_config 注入）

**Files:**
- Modify: `src/zhuzhu_Copilot/core/agent_llm.py`
- Test: `scripts/test_web_llm.py`（追加断言）

- [ ] **Step 1: 追加失败测试**

向 `scripts/test_web_llm.py` 顶部 import 区改为 `from zhuzhu_Copilot.core import agent_web_llm as W, agent_skills as S`，并追加：

```python
def test_load_config_injects_proxy():
    """kind=deepseek_web 服务商被注入为本地代理 base_url（不依赖已登录，代理恒可启）。"""
    import zhuzhu_Copilot.core.agent_llm as L

    orig = S.load_settings

    def fake_load():
        return {"model": {"providers": [
            {"name": "DeepSeek 网页版（免费）", "base_url": "http://127.0.0.1:0/v1",
             "api_key": "", "models": ["deepseek-chat"], "protocol": "chat",
             "kind": "deepseek_web"}]}}

    S.load_settings = fake_load
    try:
        cfg = L.load_model_config()
        assert cfg["providers"][0]["base_url"].startswith("http://127.0.0.1:")
        assert "127.0.0.1:0" not in cfg["base_url"]
    finally:
        S.load_settings = orig
        W.stop_proxy()


for _n in ("test_load_config_injects_proxy",):
    if _n in globals():
        globals()[_n]()
        print(f"PASS {_n}")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_web_llm.py`
Expected: FAIL（`No name 'agent_web_llm'` 或断言失败）

- [ ] **Step 3: 预设表加「DeepSeek 网页版」**

在 `agent_llm.py` 的 `PRESET_PROVIDERS` 末尾追加：

```python
    {"name": "DeepSeek 网页版（免费）",
     "base_url": "http://127.0.0.1:0/v1",     # 占位，运行期由本地代理替换
     "models": ["deepseek-chat"],
     "multimodal_models": [],
     "protocol": "chat",
     "kind": "deepseek_web",                  # 供 load_model_config 注入代理 base_url
     "desc": "免费使用：复用 chat.deepseek.com 网页版登录态，经 CDP 浏览器桥接入；"
             "须先在设置页完成一次网页版登录；不支持工具调用（agent 降级为对话模式）"},
```

- [ ] **Step 4: load_model_config 规范化保留 kind + 注入代理**

在 `load_model_config()` 规范化循环（现有 293-301 行附近）加一行保留 `kind`：

```python
        for p in providers:
            p["name"] = str(p.get("name") or "服务商").strip() or "服务商"
            ...
            p["kind"] = str(p.get("kind") or "")   # 新增：保留 kind 标记
```

在 `out = {...}` 构造前（`all_models` 聚合之后）插入注入逻辑：

```python
        # kind=deepseek_web：把 base_url 替换为本地中转代理（幂等启动，不依赖是否已登录）
        for p in providers:
            if p.get("kind") == "deepseek_web":
                r = _ensure_web_proxy()
                if r.get("ok"):
                    p["base_url"] = r["base_url"]
        first = providers[0]
```

- [ ] **Step 5: 兜底 import 辅助函数**

在 `agent_llm.py` 顶部 import 区后追加：

```python
def _ensure_web_proxy():
    try:
        from zhuzhu_Copilot.core import agent_web_llm
        return agent_web_llm.ensure_web_proxy()
    except Exception as e:
        return {"ok": False, "base_url": "", "msg": str(e)}
```

- [ ] **Step 6: 运行测试确认通过 + 回归**

Run: `python scripts/test_web_llm.py` → 全 PASS
Run: `python -c "import sys; sys.path.insert(0,'src'); from zhuzhu_Copilot.core import agent_llm; c=agent_llm.load_model_config(); print(c['base_url'][:60])"` → 非网页版服务商时行为不变

- [ ] **Step 7: Commit**

```bash
git add src/zhuzhu_Copilot/core/agent_llm.py scripts/test_web_llm.py
git commit -m "feat(llm): DeepSeek 网页版预设与 load_model_config 代理注入"
```

---

## Task 5: UI（ProviderDialog 登录入口与状态）

**Files:**
- Modify: `src/zhuzhu_Copilot/ui/agent_panel.py`

- [ ] **Step 1: 预设填充携带 kind**

`_apply_preset`（现 4763 行）在最后追加：`self._preset_kind = p.get("kind") or ""`；在 `__init__` 中初始化 `self._preset_kind = ""`。
`_current_params`（现 4778 行）返回值新增：`"kind": getattr(self, "_preset_kind", ""),`

- [ ] **Step 2: 新增「网页版登录」按钮与状态行**

在 `__init__` 中 `preset_hint` 行之后插入：

```python
        self.web_login_btn = QPushButton("登录网页版 DeepSeek")
        self.web_login_btn.setToolTip("启动独立浏览器到 chat.deepseek.com，登录一次后凭证持久化，"
                                     "此后免费调用不再弹登录")
        self.web_login_btn.setStyleSheet(
            f"background: {self._PANEL}; color: {self._TEXT}; border: 1px solid {self._ACCENT};"
            "border-radius: 8px; padding: 6px 14px; font-weight: 600;")
        self.web_login_btn.setAutoDefault(False)
        self.web_login_btn.clicked.connect(self._on_web_login)
        self.web_status = QLabel("")
        self.web_status.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")
        wrow = QHBoxLayout()
        wrow.addWidget(self.web_login_btn)
        wrow.addWidget(self.web_status, 1)
        form.addRow("网页版登录", wrow)
        self._refresh_web_status()
```

- [ ] **Step 3: 可见性联动 + 登录动作**

新增方法（`_apply_preset` 之后）：

```python
    def _refresh_web_status(self):
        """kind=deepseek_web 时显示登录状态行，否则隐藏。"""
        vis = getattr(self, "_preset_kind", "") == "deepseek_web"
        self.web_login_btn.setVisible(vis)
        self.web_status.setVisible(vis)
        if not vis:
            return
        try:
            from zhuzhu_Copilot.core import agent_web_llm
            st = ("已登录" if agent_web_llm.has_valid_credentials() else "未登录")
            self.web_status.setText(st)
            self.web_status.setStyleSheet(
                f"color: {'#22C55E' if st == '已登录' else self._ERR}; font-size: 12px;")
        except Exception:
            self.web_status.setText("")
```

`_apply_preset` 末尾调用 `self._refresh_web_status()`；`_invalidate` 中追加 `self._refresh_web_status()`。

```python
    def _on_web_login(self, *_):
        self.web_login_btn.setEnabled(False)
        self.web_status.setText("正在打开浏览器，请在窗口中完成登录…")
        threading.Thread(target=self._web_login_worker, daemon=True).start()

    def _web_login_worker(self):
        try:
            from zhuzhu_Copilot.core import agent_web_llm
            ok, msg = agent_web_llm.login_now()
            self.ai_done.emit("web_login|" + ("1" if ok else "0") + "|" + msg)
        except Exception as e:
            self.ai_done.emit("web_login|0|" + str(e))

    def _on_ai_done_web_login(self, payload):
        _, ok, msg = payload.split("|", 2)
        self.web_login_btn.setEnabled(True)
        self._refresh_web_status()
        if ok == "1":
            QMessageBox.information(self, "网页版登录", msg)
        else:
            QMessageBox.warning(self, "网页版登录失败", msg)
```

接线 ai_done 转发：

```python
        self.ai_done.connect(self._on_ai_done_web_login_forward)

    def _on_ai_done_web_login_forward(self, payload):
        if str(payload).startswith("web_login|"):
            self._on_ai_done_web_login(str(payload))
```

（`threading`、`QMessageBox` 确认已在 agent_panel.py 导入。）

- [ ] **Step 4: 服务商卡片显示登录角标**

在 `_make_provider_card`（现 3984-3987 行）的 `name_text` 构造处追加：

```python
        name_text = str(p.get("name", ""))
        if agent_llm.is_default_provider(p):
            name_text += "（内置 · 锁定）"
        if p.get("kind") == "deepseek_web":
            try:
                from zhuzhu_Copilot.core import agent_web_llm
                name_text += (" · 已登录" if agent_web_llm.has_valid_credentials()
                              else " · 未登录")
            except Exception:
                pass
        name = QLabel(name_text)
```

- [ ] **Step 5: 语法自检 + 冒烟**

Run: `python -c "import ast; ast.parse(open('src/zhuzhu_Copilot/ui/agent_panel.py', encoding='utf-8').read()); print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add src/zhuzhu_Copilot/ui/agent_panel.py
git commit -m "feat(panel): ProviderDialog 网页版 DeepSeek 登录入口与状态"
```

---

## Task 6: 端到端验证与 Debug

**Files:** 运行验证（不新增文件）

- [ ] **Step 1: 手动端到端**

启动应用（`python src/main.py`）→ 设置 → 添加服务商 → 选预设「DeepSeek 网页版（免费）」→ 点「登录网页版 DeepSeek」→ 状态变「已登录」→ 保存 → 对话发送一条真实消息。Expected: 浏览器窗口出现打字过程并输出回复。

- [ ] **Step 2: 连续多轮**

连续发送 3 条消息（如「1+1=?」「用中文重复」「今天天气怎么样」→ 提示工具不可用降级）Expected: 每轮均正常回复且上下文按 prompt 拼接保持。

- [ ] **Step 3: 过期/未登录路径演练**

删除 `~/.zhuzhu_Copilot/browser_profile`（模拟未登录）→ 发消息 Expected: 回复含「未检测到登录态…请完成登录」提示；重登后恢复。

- [ ] **Step 4: 重启持久性**

重启应用 → 新会话直接选网页版服务商发消息 Expected: 代理自动重建、对话继续。

- [ ] **Step 5: 疑难 Debug 兜底**

任一步失败按此排查：a) `scripts/poc_deepseek_web.py` 复现页面行为；b) 检查代理线程异常日志；c) 若键入未生效改用发送按钮点击兜底（`button` aria-label 含 发送）；d) 针对修复写最小复现后修补。

- [ ] **Step 6: Commit（若产生代码改动）**

```bash
git add src/zhuzhu_Copilot/core/agent_web_llm.py src/zhuzhu_Copilot/ui/agent_panel.py
git commit -m "fix(web-llm): 端到端验证修复"
```

---

## 任务依赖

Task 1（PoC）已完成且结论固化进本计划 → Task 2 基座（纯函数+桥骨架）→ Task 3 代理（依赖桥）→ Task 4 注入（依赖 Task 3 `ensure_web_proxy`）→ Task 5 UI（依赖 Task 2 `login_now/has_valid_credentials`）→ Task 6 总体验收。