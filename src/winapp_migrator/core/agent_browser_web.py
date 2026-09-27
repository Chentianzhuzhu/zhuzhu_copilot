"""内置 WebView 浏览器桥 + 自动化控制器。

背景
----
应用内置的多标签浏览器（CodePreviewWindow 的 QWebEngineView）建成后，AI 的
browser_* 工具应一律在应用内置浏览器内操作，而不再拉起独立外部浏览器。

线程模型
--------
QWebEngineView 必须在 GUI 线程操作，而 browser_* 工具在引擎任务线程执行。
本模块用「信号 → 槽（QueuedConnection）」把操作从工作线程封送到 GUI 线程同步执行：
  - BuiltinBrowser   GUI 线程侧桥（信号槽 + 事件等待超时）
  - BuiltinBrowserController  与 agent_browser 外部控制器对齐的接口，
                              供 agent_tools 的 browser_* 分支直接调用

自动化语义（简化版、够用）：
  - navigate   setUrl + 等待 loadFinished
  - snapshot   JS 枚举可交互元素并打 data-copilot-id 标签 + 页面截图 dataURL
  - click      按编号 / 文字 / selector 定位并 click()
  - type       按编号 / target(placeholder|label|name) / selector 填入并派发 input/change
  - eval / html / scroll  JS 驱动
  - tabs / switch_tab  操作内置多标签页

所有方法尽力而为、失败返回可读错误，绝不抛异常中断主引擎。
"""

from __future__ import annotations

import base64
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QEventLoop, Qt

# ---------- 静态 JS 片段（保持纯函数、可 JSON 化输入，避免注入风险） ----------

_JS_ENUMERATE = """(function(){
  var sels=['a','button','input','select','textarea','[role="button"]',
            '[role="menuitem"]','[role="checkbox"]','[role="radio"]',
            '[onclick]','[tabindex="0"]'];
  var nodes=document.querySelectorAll(sels.join(','));
  var parts=[], seen=new Set();
  [].forEach.call(nodes,function(el){
    if(seen.has(el))return; seen.add(el);
    var id=parts.length+1;
    el.setAttribute('data-copilot-id', id);
    var t=(el.innerText||el.value||el.placeholder||el.title||
           el.getAttribute('aria-label')||'').toString().trim()
           .replace(/\\s+/g,' ').slice(0,80)||'<'+el.tagName.toLowerCase()+'>';
    var kind=el.tagName.toLowerCase()+(el.type&&el.tagName.toLowerCase()!=='a'?' '+el.type:'');
    parts.push('['+id+'] ('+kind+') '+t);
  });
  return parts.join('\\n')||'(页面无可交互元素)';
})()"""

_JS_CLICK = """(function(mode,val){
  function byId(){return document.querySelector('[data-copilot-id="'+val+'"]');}
  function bySel(){return document.querySelector(val);}
  function byText(){
    var sels=document.querySelectorAll('a,button,[role="button"],input,select,textarea,[onclick]');
    var q=val.toLowerCase();
    for(var i=0;i<sels.length;i++){var e=sels[i];
      var t=(e.innerText||e.value||e.placeholder||e.title||e.getAttribute('aria-label')||'')
             .toString().trim().toLowerCase();
      if(t.indexOf(q)>=0)return e;}
    return null;
  }
  var el = mode==='id'?byId():(mode==='selector'?bySel():byText());
  if(!el)return 'NOT_FOUND';
  el.scrollIntoView({block:'center'});
  el.click();
  return 'OK';
})"""

_JS_TYPE = """(function(mode,val,text){
  function byId(){return document.querySelector('[data-copilot-id="'+val+'"]');}
  function bySel(){return document.querySelector(val);}
  function byTarget(){
    var sels=document.querySelectorAll('input,textarea,select,[contenteditable="true"]');
    var q=val.toLowerCase();
    for(var i=0;i<sels.length;i++){var e=sels[i];
      var t=(e.placeholder||e.title||e.name||e.getAttribute('aria-label')||'')
             .toString().trim().toLowerCase();
      if(t.indexOf(q)>=0)return e;
      var l=e.closest('label'); if(l&&(l.innerText||'').toLowerCase().indexOf(q)>=0)return e;}
    return null;
  }
  var el = mode==='id'?byId():(mode==='selector'?bySel():byTarget());
  if(!el)return 'NOT_FOUND';
  el.focus();
  var tag=el.tagName.toLowerCase();
  if(tag!=='select'){
    try{var setter=Object.getOwnPropertyDescriptor(el.constructor.prototype,'value');
      setter&&setter.set?setter.set.call(el,text):(el.value=text);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return 'SET';
    }catch(e){ return 'EXEC_ERROR: '+e; }
  }
  return 'TARGET_NOT_INPUT';
})"""

_JS_EVAL = """(function(js){
  try{ var r=eval(js);
    if(r===undefined)return 'undefined';
    if(typeof r==='object'){ try{return JSON.stringify(r);}catch(e){return String(r);} }
    return String(r);
  }catch(e){ return 'EXEC_ERROR: '+e; }
})"""

_JS_HTML = """(function(sel){
  if(!sel)return document.documentElement.outerHTML;
  var el=document.querySelector(sel);
  return el?el.outerHTML:'NOT_FOUND';
})"""

_JS_SCROLL = """(function(mode,amount){
  if(mode==='top'){window.scrollTo(0,0);return 'TOP';}
  if(mode==='bottom'){window.scrollTo(0,document.body.scrollHeight);return 'BOTTOM';}
  var d=('down'===mode?amount:-amount);
  window.scrollBy(0,d); return 'SCROLLED '+(mode==='down'?'down':'up');
})"""

_JS_PAGE_TEXT = """(function(){
  return (document.title||'').trim()+'\\n'+(document.location.href||'');
})()"""


class BuiltinBrowser(QObject):
    """GUI 线程浏览器桥：工作线程通过 request() 同步执行，结果等事件超时。"""

    _requested = pyqtSignal(object)   # payload dict

    def __init__(self):
        super().__init__()
        self._view_provider = None       # callable() -> 当前活动 QWebEngineView（GUI 线程）
        self._ensure_visible = None      # callable() -> 前置：显示内置浏览器/切 web 模式
        # AutoConnection：从工作线程 emit → 槽在 GUI 线程（本对象 affinity）执行
        self._requested.connect(self._on_request, Qt.ConnectionType.QueuedConnection)

    # ---------- GUI 侧装配 ----------
    def attach(self, view_provider, ensure_visible=None):
        """由 GUI（AgentPanel）调用，注入当前活动 WebView 提供者与显隐回调。"""
        self._view_provider = view_provider
        self._ensure_visible = ensure_visible

    # ---------- 工作线程入口 ----------
    def request(self, op: str, timeout: float = 60.0, **kwargs):
        """阻塞式派发到 GUI 线程执行，返回 dict 结果。GUI 线程内调用时直接执行。"""
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None or QThread.currentThread() is app.thread():
            return self._exec(op, kwargs)
        evt = threading.Event()
        payload = {"evt": evt, "op": op, "kw": kwargs,
                   "result": None, "error": None}
        self._requested.emit(payload)
        if not evt.wait(timeout):
            return {"error": f"内置浏览器操作超时（{op}，>{int(timeout)}s）"}
        if payload["error"]:
            return {"error": payload["error"]}
        return payload["result"]

    # ---------- GUI 线程执行 ----------
    def _on_request(self, payload):
        try:
            payload["result"] = self._exec(payload["op"], payload.get("kw") or {})
        except Exception as e:   # noqa: BLE001
            payload["error"] = f"{type(e).__name__}: {e}"
        finally:
            payload["evt"].set()

    def _view(self):
        if self._view_provider is None:
            return None
        return self._view_provider()

    def _exec(self, op: str, kw: dict) -> dict:
        view = self._view()
        if view is None:
            return {"error": "内置浏览器不可用（未安装 QtWebEngine 或预览面板未就绪）"}
        try:
            if self._ensure_visible is not None:
                try:
                    self._ensure_visible()
                except Exception:
                    pass
            return self._run(view, op, kw)
        except Exception as e:   # noqa: BLE001
            return {"error": f"[{op}] {type(e).__name__}: {e}"}

    def _run(self, view, op: str, kw: dict) -> dict:   # noqa: C901
        if op == "navigate":
            return self._navigate(view, kw)
        if op == "screenshot":
            return {"data_url": self._screenshot(view)}
        if op == "summarize":
            return {"text": self._js_result(view, _JS_ENUMERATE) or "(页面无可交互元素)"}
        if op == "eval":
            return {"text": self._js_result(view, _JS_EVAL, str(kw.get("js", "")))}
        if op == "html":
            txt = self._js_result(view, _JS_HTML, str(kw.get("selector", "")))
            return {"text": (txt[:20000] + "...(截断)" if len(txt) > 20000 else txt)}
        if op == "click":
            return self._click(view, kw)
        if op == "type":
            return self._type(view, kw)
        if op == "scroll":
            return self._scroll(view, kw)
        if op == "open":
            return {"ok": True, "msg": "已在内置浏览器打开，可直接操作", "shot": None}
        if op == "stop":
            return {"ok": True, "msg": "内置浏览器无需关闭（与预览面板共用）", "shot": None}
        if op == "list_pages":
            return {"pages": self._list_pages(view)}
        if op == "switch_tab":
            return self._switch_tab(view, kw)
        return {"error": f"未知内置浏览器操作: {op}"}

    # ---------- 具体操作（均在 GUI 线程） ----------
    def _navigate(self, view, kw) -> dict:
        url = str(kw.get("url", "")).strip()
        if not url:
            return {"error": "缺少 url"}
        if not url.lower().startswith(("http://", "https://", "file://")):
            url = "https://" + url
        from PyQt6.QtCore import QUrl
        page = view.page()
        done = threading.Event()
        # 断开旧的 loadFinished，避免上次回调残留
        try:
            page.loadFinished.disconnect()
        except TypeError:
            pass
        page.loadFinished.connect(lambda ok: done.set())
        view.setUrl(QUrl(url))
        _wait(done, 40)
        return {"ok": True, "msg": f"已在内置浏览器打开 {url}", "shot": self._screenshot(view)}

    def _screenshot(self, view) -> str:
        try:
            pix = view.grab()
            img = pix.toImage()
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
            buf.close()
            return "data:image/png;base64," + b64
        except Exception:
            return ""

    def _click(self, view, kw) -> dict:
        eid = kw.get("id")
        text = str(kw.get("text", "")).strip()
        selector = str(kw.get("selector", "")).strip()
        if eid is not None:
            mode, val = "id", str(int(eid) if str(eid).lstrip("+-").isdigit() else eid)
        elif text:
            mode, val = "text", text
        elif selector:
            mode, val = "selector", selector
        else:
            return {"ok": False, "msg": "缺少 id/text/selector 定位参数", "shot": None}
        res = self._js_result(view, _JS_CLICK, _js_lit(mode), _js_lit(val))
        ok = res == "OK"
        return {"ok": ok, "msg": ("已点击目标" if ok else f"未找到目标（{val}）"),
                "shot": self._screenshot(view)}

    def _type(self, view, kw) -> dict:
        text = str(kw.get("text", ""))
        eid = kw.get("id")
        target = str(kw.get("target", "")).strip()
        selector = str(kw.get("selector", "")).strip()
        if eid is not None:
            mode, val = "id", str(int(eid) if str(eid).lstrip("+-").isdigit() else eid)
        elif target:
            mode, val = "target", target
        elif selector:
            mode, val = "selector", selector
        else:
            mode, val = "target", "(none)"
        res = self._js_result(view, _JS_TYPE, _js_lit(mode), _js_lit(val), _js_lit(text))
        ok = res == "SET"
        return {"ok": ok, "msg": ("已输入文本" if ok else f"未找到输入框（{val}）：{res}"),
                "shot": self._screenshot(view)}

    def _scroll(self, view, kw) -> dict:
        mode = str(kw.get("direction", "down")).lower()
        amount = kw.get("amount")
        amt = int(amount) if amount and str(amount).lstrip("+-").isdigit() else 400
        self._js_result(view, _JS_SCROLL, _js_lit(mode), str(amt))
        return {"ok": True, "msg": f"已向下滚动 {amt}px" if mode == "down" else f"已向上滚动 {amt}px",
                "shot": self._screenshot(view)}

    def _list_pages(self, view) -> list:
        # view 所在容器（QTabWidget）位于 stack 上层；列出所有标签标题
        tabs = getattr(view.parent(), "parent", None)
        tw = getattr(view, "_owner_tabs", None)
        if tw is not None:
            out = []
            for i in range(tw.count()):
                v = tw.widget(i)
                title = tw.tabText(i)
                try:
                    u = v.url().toString() if v is not None else ""
                except Exception:
                    u = ""
                out.append({"id": i + 1, "title": title or "标签", "url": u})
            return out
        return [{"id": 1, "title": "标签", "url": view.url().toString()}]

    def _switch_tab(self, view, kw) -> dict:
        idx = kw.get("id", 0)
        try:
            target = int(idx) - 1
        except Exception:
            return {"ok": False, "msg": f"无效标签编号 {idx}"}
        tw = getattr(view, "_owner_tabs", None)
        if tw is None or not (0 <= target < tw.count()):
            return {"ok": False, "msg": f"标签编号超出范围（1-{tw.count() if tw else 0}）"}
        tw.setCurrentIndex(target)
        return {"ok": True, "msg": f"已切换到标签 {idx}"}

    # ---------- 工具 ----------
    def _js_result(self, view, fn_js, *lit_args) -> str:
        """在页面运行 fn_js(args)，等待回调并返回字符串结果。"""
        if len(lit_args) > 0:
            js = f"({fn_js})({', '.join(lit_args)})"
        else:
            js = f"({fn_js})()"
        holder = {}
        loop = QEventLoop()
        try:
            view.page().runJavaScript(js, lambda r: _cb(r, holder, loop))
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
        except Exception:
            return ""
        return holder.get("r", "") or ""


def _cb(result, holder, loop):
    holder["r"] = str(result if result is not None else "")
    loop.exit()


def _js_lit(s: str) -> str:
    """把 Python 字符串安全放进 JS 字符串字面量（JSON 再包引号）。"""
    import json
    return json.dumps(str(s))


def _wait(evt: threading.Event, seconds: float):
    """GUI 线程内等待事件（嵌套事件循环保持响应），超时返回。"""
    deadline = time.time() + seconds
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    while not evt.is_set() and time.time() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.02)


class BuiltinBrowserController:
    """与 agent_browser 外部控制器对齐的接口，供 agent_tools browser_* 分支使用。
    未装配（attach 未调用）或内置浏览器不可用时，get() 返回 None 由调用方回退外置。"""

    def __init__(self, bridge: BuiltinBrowser):
        self._b = bridge

    # ---- 从 agent_tools 调用的接口（对齐 external controller 返回形态） ----
    def get(self):
        return self if (self._b is not None and self._b._view_provider is not None) else None

    def start(self, engine: str = "", headless: bool = False):
        r = self._b.request("open")
        return (True, r.get("msg", "")) if r.get("ok") else (False, r.get("error", "内置浏览器不可用"))

    def stop(self):
        r = self._b.request("stop")
        return (True, r.get("msg", ""))

    def navigate(self, url: str):
        r = self._b.request("navigate", url=url, timeout=45)
        if r.get("error"):
            return False, r["error"]
        return True, r.get("msg", "已导航")

    def screenshot(self) -> str:
        r = self._b.request("screenshot", timeout=20)
        return r.get("data_url", "") or ""

    def summarize(self) -> str:
        r = self._b.request("summarize", timeout=20)
        return r.get("text", "") or ""

    def click(self, eid=None, text=None, selector=None, button="left"):
        kw = {}
        if eid is not None:
            kw["id"] = eid
        if text:
            kw["text"] = text
        if selector:
            kw["selector"] = selector
        r = self._b.request("click", timeout=20, **kw)
        if r.get("error"):
            return False, r["error"], None
        return r.get("ok", False), r.get("msg", ""), r.get("shot")

    def type_text(self, text="", eid=None, target=None, selector=None):
        kw = {"text": str(text)}
        if eid is not None:
            kw["id"] = eid
        if target:
            kw["target"] = target
        if selector:
            kw["selector"] = selector
        r = self._b.request("type", timeout=20, **kw)
        if r.get("error"):
            return False, r["error"], None
        return r.get("ok", False), r.get("msg", ""), r.get("shot")

    def eval(self, js: str):
        r = self._b.request("eval", timeout=20, js=str(js))
        return {"text": r.get("text", r.get("error", ""))}

    def html(self, selector: str = ""):
        r = self._b.request("html", timeout=20, selector=str(selector))
        return {"text": r.get("text", r.get("error", ""))}

    def scroll(self, direction="down", amount=None, eid=None, selector=None):
        kw = {"direction": str(direction)}
        if amount is not None:
            kw["amount"] = amount
        r = self._b.request("scroll", timeout=20, **kw)
        if r.get("error"):
            return False, r["error"], None
        return r.get("ok", False), r.get("msg", ""), r.get("shot")

    def list_pages(self):
        r = self._b.request("list_pages", timeout=10)
        return r.get("pages", [])

    def switch_tab(self, index: int):
        r = self._b.request("switch_tab", timeout=10, id=int(index or 0))
        if r.get("error"):
            return False, r["error"]
        return r.get("ok", False), r.get("msg", "")


# =========================================================================
# 内置浏览器下载支持
# =========================================================================
# 所有 Web 标签共用全局默认 profile（QWebEngineProfile.defaultProfile）。
# 下载信号在整个进程只挂接一次，转发给"当前"预览实例处理（主题重建会新建实例）。
_DL_HOOKED = False
_ACTIVE_PREVIEW = None
_UA_CONFIGURED = False

# 内置浏览器的访客身份：必须是 Windows 桌面版，而非移动/iOS 版（某些默认 UA 会被
# 站点判定为移动设备而返回手机版页面）。显式指定桌面 Chrome UA 可保证桌面识别。
_DESKTOP_WIN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def configure_desktop_identity() -> None:
    """让内置浏览器以 Windows 桌面版身份访问（覆盖默认 profile 的 UA）。
    进程级只执行一次。"""
    global _UA_CONFIGURED
    if _UA_CONFIGURED:
        return
    _UA_CONFIGURED = True
    try:
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        QWebEngineProfile.defaultProfile().setHttpUserAgent(_DESKTOP_WIN_UA)
    except Exception:
        _UA_CONFIGURED = False


def wire_downloads(preview) -> None:
    """让内置浏览器支持下载：挂接全局默认 profile 的 downloadRequested。
    preview：当前 CodePreviewWindow 实例（实现 _status(text) 与 _download_dir()）。
    幂等：整个进程只连接一次，此后仅更新转发目标实例。"""
    configure_desktop_identity()
    global _DL_HOOKED, _ACTIVE_PREVIEW
    _ACTIVE_PREVIEW = preview
    if _DL_HOOKED:
        return
    _DL_HOOKED = True
    try:
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        QWebEngineProfile.defaultProfile().downloadRequested.connect(
            _on_download_requested)
    except Exception:
        _DL_HOOKED = False


def _on_download_requested(item):
    p = _ACTIVE_PREVIEW
    if p is None:
        try:
            item.cancel()
        except Exception:
            pass
        return
    try:
        p._handle_download(item)
    except Exception:
        try:
            item.cancel()
        except Exception:
            pass