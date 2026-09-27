"""真机诊断：探测 DeepSeek 网页版当前 DOM，验证 WebBridge 选择器/交互是否仍有效。

阶段 0：仅 DOM 探测 + 登录态（不发消息，零风险）。
用法：python scripts/dbg_web_llm_live.py [--ask "prompt"]   （加 --ask 才发一条真实消息）
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from winapp_migrator.core import agent_web_llm as W
from winapp_migrator.core import agent_browser

OUT = Path.home() / ".winapp_migrator" / "web_credentials"
OUT.mkdir(parents=True, exist_ok=True)


def ev(ctrl, js):
    try:
        o = ctrl.eval(js)
        return o.get("text", "") if o.get("ok") else "ERR:" + (o.get("text") or "")
    except Exception as e:
        return "EXC:" + str(e)[:200]


_PROBE_MAIN = r"""
(() => {
  const txt = (el) => (el ? ((el.value !== undefined ? el.value : el.innerText) || '') : '');
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const box = document.querySelector('textarea,[contenteditable="true"]');
  const send = document.querySelector('[class*="ds-button--primary"]');
  const newChat = [...document.querySelectorAll('button,[role="button"],[tabindex],a,span,div')]
    .filter(el => {
      const t = (((el.getAttribute('aria-label')||'') + ' ' + (el.textContent||'')).trim());
      return /新对话|开启新对话|new ?chat/i.test(t) && (el.textContent||'').trim().length <= 20 && visible(el);
    }).slice(0, 5).map(el => el.tagName + ':' + ((el.textContent||'').trim()||'').slice(0, 20));
  const tabs = [...document.querySelectorAll('button,[role="button"],[tabindex],a,span,div')]
    .filter(el => {
      const t = ((el.textContent||'').trim() || (el.getAttribute('aria-label')||''));
      return t && t.length <= 8 && visible(el);
    }).map(el => ((el.textContent||'').trim()||'').slice(0,8))
    .filter(t => /快速|专家|识图|视图|试图|深度思考|智能搜索/.test(t));
  const body = document.body.innerText || '';
  return JSON.stringify({
    href: location.href.slice(0, 120), title: (document.title||'').slice(0, 60),
    ready: document.readyState, vis: document.visibilityState,
    textarea: !!document.querySelector('textarea'), boxLen: txt(box).length,
    editable: !!document.querySelector('[contenteditable="true"]'),
    nBtn: document.querySelectorAll('button').length,
    nRoleBtn: document.querySelectorAll('[role="button"]').length,
    nTabIndex: document.querySelectorAll('[tabindex]').length,
    sendVisible: !!(send && visible(send)),
    sendCls: (send ? (send.className||'').toString().slice(0,80) : ''),
    newChat: newChat.length ? newChat : 'NONE',
    modeTabs: [...new Set(tabs)].slice(0, 12),
    nAssistMsg: document.querySelectorAll('[class*="ds-assistant-message"]').length,
    nMarkdown: document.querySelectorAll('[class*=ds-markdown]').length,
    nStop: document.querySelectorAll('[class*="stop"],[class*="Stop"],[aria-label*="停止"]').length,
    nHeader: document.querySelectorAll('[class*="the-header"]').length,
    bodyLen: body.length,
  });
})()
"""


def shot(ctrl, name):
    try:
        data = ctrl.screenshot()
        raw = data.split(",", 1)[1] if "," in data else data
        p = OUT / name
        p.write_bytes(base64.b64decode(raw))
        print("截图:", p)
    except Exception as e:
        print("截图失败:", e)


def main():
    ask_only = False
    prompt = "只回复四个字：链路OK"
    for a in sys.argv[1:]:
        if a == "--ask":
            ask_only = True
        elif a.startswith("--prompt="):
            prompt = a[len("--prompt="):]
    print("== 阶段0: DOM 探测 ==", flush=True)
    b = W.bridge()
    ok, msg = b.ensure_ready(wait_login=120.0)
    print("ensure_ready:", ok, "|", msg, flush=True)
    if not ok:
        return 1
    ctrl = b._ctrl
    # 等页面完全加载
    time.sleep(3)
    s = ev(ctrl, _PROBE_MAIN)
    print("PROBE:", s[:2000], flush=True)
    shot(ctrl, "dbg_dom.png")
    try:
        d = json.loads(s)
    except Exception:
        d = {}
    # 已登录判定：输入框存在
    login_ok = d.get("textarea") or d.get("editable")
    print("登录态(输入框存在):", bool(login_ok), flush=True)
    if not login_ok:
        print("!! 未登录/页面结构不可识别，跳过消息测试", flush=True)
        b.close()
        return 1
    if not ask_only:
        print("== 仅探测，不发消息 ==", flush=True)
        b.close()
        return 0
    # ---- 阶段1: 最小 ask ----
    print("== 阶段1: 最小 ask ==", flush=True)
    deltas = []

    def on_delta(t):
        deltas.append(t)

    t0 = time.time()
    try:
        r = b.ask(prompt, on_delta=on_delta, wait=150.0, mode="deepseek-chat-quick")
        print("耗时(s):", round(time.time() - t0, 1), flush=True)
        print("增量段数:", len(deltas), flush=True)
        print("增量总字符:", sum(len(x) for x in deltas), flush=True)
        print("回复文本:", repr(r[:1200]), flush=True)
        (OUT / "dbg_reply.txt").write_text(r, encoding="utf-8")
        shot(ctrl, "dbg_reply.png")
    except Exception as e:
        import traceback
        traceback.print_exc()
    b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
