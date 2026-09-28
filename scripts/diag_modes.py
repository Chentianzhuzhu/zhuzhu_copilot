"""探测 v2：真实会话下验证 model_type / thinking_enabled 模式行为。

步骤：创建真实会话 → 分别以不同 model_type+thinking 组合发消息，
观察 HTTP 状态与页面标题栏「快速模式」标签变化。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zhuzhu_Copilot.core import agent_browser

_LG = ("(!!document.querySelector('[class*=textarea], [class*=input-area], "
       'textarea, [contenteditable="true"]\'))')

_HEADER_TAG = r"""
(() => {
  const els = [...document.querySelectorAll('[class*=tag], [class*=badge], [class*=mode], h1, h2, h3, span')]
    .filter(e => /快速|专家|模式|思考/.test((e.textContent || '').trim()) && (e.textContent || '').trim().length < 10);
  return JSON.stringify(els.slice(0, 6).map(e => (e.textContent || '').trim()));
})()
"""

_FETCH = r"""
(() => {
  window.__p = {status: null, data: null, done: false, err: null};
  const ut = JSON.parse(localStorage.getItem('userToken') || '{\"value\":\"\"}').value || '';
  fetch('/api/v0/chat/completion', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'authorization': 'Bearer ' + ut,
      'x-client-bundle-id': 'com.deepseek.chat',
      'x-client-version': '2.4.0',
      'x-client-platform': 'web',
      'x-client-locale': 'zh_CN',
      'x-client-timezone-offset': '28800',
      'Referer': location.href
    },
    body: JSON.stringify(__BODY__),
    credentials: 'include'
  }).then(async r => {
    window.__p.status = r.status;
    window.__p.data = await r.text();
    window.__p.done = true;
  }).catch(e => { window.__p.err = String(e); window.__p.done = true; });
  return 'started';
})()
"""

_SNAP = ("JSON.stringify({status: window.__p && window.__p.status, "
         "err: window.__p && window.__p.err, "
         "data: (window.__p && window.__p.data || '').slice(0, 300)})")


def ev(ctrl, js):
    o = ctrl.eval(js)
    return o.get("text", "ERR") if o.get("ok") else "ERR " + o.get("text", "")


def wait(ctrl, label, budget=45):
    deadline = time.time() + budget
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            s = json.loads(ev(ctrl, _SNAP) or "{}")
        except Exception:
            s = {}
        if s.get("done") or s.get("err"):
            print(f"[{label}] {json.dumps(s, ensure_ascii=False)[:400]}", flush=True)
            return s
    print(f"[{label}] timeout", flush=True)
    return {}


def main():
    ctrl = agent_browser.controller()
    ok, msg = ctrl.start()
    print("start:", ok)
    ctrl.navigate("https://chat.deepseek.com")
    t = time.time()
    while time.time() - t < 60:
        if ev(ctrl, _LG) == "true":
            break
        time.sleep(1)
    time.sleep(1.5)
    # 创建真实会话
    body = {"name": "__probe__"}
    ev(ctrl, _FETCH.replace("__BODY__", json.dumps(body)))
    s = wait(ctrl, "session/create")
    sid = ""
    try:
        d = json.loads(s.get("data") or "{}")
        sid = (d.get("data") or {}).get("biz_data", {}).get("chat_session", {}).get("id") or ""
    except Exception:
        pass
    print("session:", sid, flush=True)
    if not sid:
        ctrl.stop()
        return
    combos = [
        ("default", False, "快速(default)"),
        ("expert", False, "专家(expert)"),
        ("default", True, "深度思考"),
    ]
    for mt, th, label in combos:
        print("----", label, "----", flush=True)
        b = {"chat_session_id": sid, "parent_message_id": None,
             "model_type": mt, "prompt": "只回复两个词：收到",
             "ref_file_ids": [], "thinking_enabled": th,
             "search_enabled": True, "action": None, "preempt": False}
        ev(ctrl, _FETCH.replace("__BODY__", json.dumps(b)))
        wait(ctrl, label, budget=60)
        print("标题标签:", ev(ctrl, _HEADER_TAG)[:400], flush=True)
    ctrl.stop()


if __name__ == "__main__":
    main()