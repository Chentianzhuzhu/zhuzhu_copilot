"""PoC v8：浏览器 UI 桥 —— 输入+发送（双方式）+ 全量文本 diff 提取回复。

v7 教训：类名选择器不可靠；输入框真实键入成功但回复未提取到，可能未发送或
选择器不匹配。v8 用 main 容器 innerText diff + 发送按钮点击兜底 + 截屏留证。
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from winapp_migrator.core import agent_browser

LOGIN_WAIT = 180
_LG = ("(!!document.querySelector('[class*=textarea], [class*=input-area], "
       'textarea, [contenteditable="true"]\'))')

_MAIN_TEXT = ("(() => { const m = document.querySelector('main'); "
              "return m ? m.innerText : document.body.innerText; })()")

_INPUT_LEN = ("(() => { const b = document.querySelector('textarea,[contenteditable=\"true\"]');"
              " if(!b) return -1; return (b.value !== undefined ? b.value : b.innerText||'').length; })()")

_SEND_BTN = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const pick = btns.find(b => /发送|send|new-chat/i.test(b.getAttribute('aria-label')||'')
    || /send/i.test(b.className||''));
  if (pick) { pick.click(); return 'BTN:' + (pick.getAttribute('aria-label')||pick.className||'?'); }
  return 'NO_BTN';
})()
"""

_STOP_EXISTS = ("(!!document.querySelector('button[class*=stop],button[aria-label*=\"停止\"],"
                "[class*=stop-generate],[class*=generate-over]'))")


def ev(ctrl, js):
    out = ctrl.eval(js)
    return out.get("text", "") if out.get("ok") else "ERR " + out.get("text", "")


def main():
    print("== 启动浏览器 ==", flush=True)
    ctrl = agent_browser.controller()
    ok, msg = ctrl.start()
    if not ok:
        print("启动失败:", msg)
        return
    ctrl.navigate("https://chat.deepseek.com")
    deadline = time.time() + LOGIN_WAIT
    while time.time() < deadline:
        st = ctrl.eval(_LG)
        if st and st.get("ok") and st.get("text") == "true":
            break
        time.sleep(2)
    else:
        print("!! 等待登录超时")
        ctrl.stop()
        return
    print("已登录\n", flush=True)

    base_text = ev(ctrl, _MAIN_TEXT)
    print("main 基础文本长度:", len(base_text), flush=True)
    print("复制基础文本到哨兵文件", flush=True)
    seed = str(Path.home() / ".winapp_migrator" / "web_credentials")
    Path(seed).mkdir(parents=True, exist_ok=True)
    (Path(seed) / "poc_base.txt").write_text(base_text, encoding="utf-8")

    prompt = "只回复一句话：浏览器桥测试成功"
    print("[聚焦+键入]", flush=True)
    ev(ctrl, "(() => {const b=document.querySelector('textarea,[contenteditable=\"true\"]');"
             " if(!b)return'NO_BOX'; b.focus(); b.scrollIntoView({block:'center'}); return 'OK';})()")
    ctrl._call("Input.insertText", {"text": prompt}, session=ctrl._active_session)
    print("键入后输入框长度:", ev(ctrl, _INPUT_LEN), flush=True)

    sent = False
    # 方式 A：回车
    try:
        for t in ("keyDown", "keyUp"):
            ctrl._call("Input.dispatchKeyEvent", {
                "type": t, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                session=ctrl._active_session)
    except Exception as e:
        print("dispatchKey 失败:", e, flush=True)
    time.sleep(1.0)
    if ev(ctrl, _INPUT_LEN) == "0":
        sent = True
        print("回车后输入框已清空 → 发送成功(回车)", flush=True)
    # 方式 B：点发送按钮
    if not sent:
        print("回车未清空，尝试发送按钮:", ev(ctrl, _SEND_BTN), flush=True)
        time.sleep(1.0)
        if ev(ctrl, _INPUT_LEN) == "0":
            sent = True
            print("发送按钮点击后清空 → 发送成功(按钮)", flush=True)
    if not sent:
        print("!! 未能发送，保存截图后退出", flush=True)
        _shot(ctrl, seed + "/poc_v8_fail.png")
        ctrl.stop()
        return

    # 轮询 main 文本增量
    prev = base_text
    last_cur = base_text
    stable = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(0.6)
        cur = ev(ctrl, _MAIN_TEXT)
        stop_on = ev(ctrl, _STOP_EXISTS)
        growth = cur != last_cur
        last_cur = cur
        stop = stop_on == "true"
        if growth or stop:
            stable = 0
        elif len(cur) > len(base_text):
            stable += 1
        if stable >= 8 and not stop:
            break
    reply = last_cur[len(base_text):].strip()
    print("== 回复完成 ==", flush=True)
    print("回复长度:", len(reply), flush=True)
    print("回复内容:", reply[:900], flush=True)
    (Path(seed) / "poc_v8_reply.txt").write_text(reply, encoding="utf-8")
    _shot(ctrl, seed + "/poc_v8_done.png")
    print("== PoC v8 完成 ==", flush=True)
    ctrl.stop()


def _shot(ctrl, path):
    try:
        data_url = ctrl.screenshot()
        raw = data_url.split(",", 1)[1] if "," in data_url else data_url
        Path(path).write_bytes(base64.b64decode(raw))
        print("截图已保存:", path, flush=True)
    except Exception as e:
        print("截图失败:", e, flush=True)


if __name__ == "__main__":
    main()