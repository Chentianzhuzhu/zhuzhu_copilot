# -*- coding: utf-8 -*-
"""验证：客户端支付对话框只加载 Stripe 收款码，不再加载个人收款码。

做法：不构造重量级 AgentPanel，而是用一个只带 stripe_pay_signal 与少量样式属性的
QDialog 桩对象，直接调用真实的 AgentPanel._memb_show_pay_dialog / _on_stripe_state
（被测代码零改动）。网络与浏览器全部 mock。

断言：
  1) 下单只走 payapp/api/pay/checkout，携带用户 token；
  2) 客户端不再请求 /api/shop/order 与 /api/shop/qrcodes（个人收款码链路已移除）；
  3) 二维码区域显示由 Stripe 收银台 URL 本地生成的二维码位图；
  4) 「在浏览器中打开」按钮拿到 URL 后可用，点击可打开收银台；
  5) 轮询到 paid + granted 后自动关窗并提示支付成功；
  6) 静态检查：客户端源码不再引用个人收款码接口。
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS, PASSES = [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


from PyQt6.QtCore import QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

from zhuzhu_Copilot.core import auth_client  # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as _ap  # noqa: E402

app = QApplication.instance() or QApplication([])

# ---------- mock：Checkout 下单 + 订单状态轮询 ----------
CALLS = []
OPENED = []
MSGBOX = []
STATUS_SEQ = ["pending", "paid"]
CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_live_test_abcdef"


def fake_post(url, payload, token=None, timeout=None):
    CALLS.append(("POST", url, dict(payload or {}), token))
    return True, {"code": 0, "data": {
        "order_id": "PAY-TEST-1", "checkout_url": CHECKOUT_URL}}, ""


def fake_get(url, token=None, timeout=None):
    CALLS.append(("GET", url, {}, token))
    status = STATUS_SEQ.pop(0) if STATUS_SEQ else "paid"
    return True, {"code": 0, "data": {
        "order_id": "PAY-TEST-1", "status": status,
        "grant_status": "granted" if status == "paid" else "pending"}}, ""


auth_client._http_post_json = fake_post
auth_client._http_get_json = fake_get
QMessageBox.information = staticmethod(lambda *a, **k: MSGBOX.append(a[1:3]))


class _Stub(QDialog):
    """最小宿主：只提供被测方法需要的信号与样式属性。"""
    stripe_pay_signal = pyqtSignal(int, str, str, str)
    _on_stripe_state = _ap._AgentSettingsDialog._on_stripe_state
    _qr_pixmap = staticmethod(_ap._AgentSettingsDialog._qr_pixmap)
    _memb_refresh_status = lambda self, *a: None
    _memb_load_products = lambda self: None
    _TEXT, _DIM = "#F5F5F5", "#8A8A8A"
    _PANEL, _BORDER, _ACCENT = "#101010", "#000000", "#1E40AF"

    def _dialog_qss(self):
        return ""

    def _open_checkout_url(self, url):
        OPENED.append(url)


stub = _Stub()
stub.stripe_pay_signal.connect(stub._on_stripe_state)   # 与生产接线一致


def _dialog():
    return next((d for d in stub.findChildren(QDialog) if d is not stub), None)


STATE = {}


def step_qr_ready():
    dlg = _dialog()
    lbl = getattr(stub, "_pay_qr_label", None)
    STATE["qr_ok"] = bool(lbl is not None and lbl.pixmap() is not None
                          and not lbl.pixmap().isNull())
    STATE["qr_size"] = None if not STATE["qr_ok"] else (
        lbl.pixmap().width(), lbl.pixmap().height())
    btn = getattr(stub, "_pay_open_btn", None)
    STATE["btn_enabled"] = bool(btn is not None and btn.isEnabled())
    STATE["url"] = getattr(stub, "_pay_checkout_url", "")
    if btn is not None and btn.isEnabled():
        btn.click()                      # 模拟点击「在浏览器中打开」
    STATE["order_text"] = (getattr(stub, "_pay_order_label", None).text()
                           if getattr(stub, "_pay_order_label", None) else "")
    if dlg is not None:
        STATE["dlg_ref"] = dlg


def step_fallback_close():
    dlg = _dialog()
    if dlg is not None and dlg.isVisible():
        dlg.reject()


QTimer.singleShot(700, step_qr_ready)
QTimer.singleShot(25000, step_fallback_close)

print("--- 打开支付对话框（只有 Stripe 一种方式）---")
t0 = time.time()
_ap._AgentSettingsDialog._memb_show_pay_dialog(
    stub, type("Auth", (), {"server": "https://chentian.dpdns.org/authapp",
                            "token": "user-token"})(),
    {"id": "pro_monthly", "name": "Pro", "price": 7})
elapsed = time.time() - t0
# 方法返回后再取对话框判断可见性：若到账自动关窗，exec() 会提前返回（约 10s），
# 而不是等到 25s 的兜底关闭。
_dlg = _dialog()
try:
    STATE["closed"] = bool(_dlg is None or not _dlg.isVisible())
except RuntimeError:
    STATE["closed"] = True

print("--- 断言 ---")
check("首次请求即为 Stripe Checkout 下单",
      bool(CALLS) and CALLS[0][0] == "POST"
      and "/payapp/api/pay/checkout" in CALLS[0][1],
      CALLS[0][1] if CALLS else "no calls")
check("下单携带 product_id", any(c[2].get("product_id") == "pro_monthly" for c in CALLS))
check("下单携带用户 Bearer token", any(c[3] == "user-token" for c in CALLS))
check("不再请求个人收款码接口",
      not any("/api/shop/order" in c[1] or "/api/shop/qrcodes" in c[1] for c in CALLS),
      [c[1] for c in CALLS])
check("二维码区域显示 Stripe 收款码位图", STATE.get("qr_ok") is True,
      f"size={STATE.get('qr_size')}")
check("二维码尺寸符合展示区域", STATE.get("qr_size") == (210, 210),
      STATE.get("qr_size"))
check("订单号已展示", "PAY-TEST-1" in (STATE.get("order_text") or ""),
      STATE.get("order_text"))
check("「在浏览器中打开」按钮已启用", STATE.get("btn_enabled") is True)
check("点击按钮可打开 Stripe 收银台", OPENED == [CHECKOUT_URL], OPENED)
check("轮询订单状态", any("/payapp/api/pay/order/PAY-TEST-1" in c[1] for c in CALLS),
      [c[1] for c in CALLS])
check("到账后自动关窗（非 25s 兜底关闭）",
      STATE.get("closed") is True and elapsed < 20, f"elapsed={elapsed:.1f}s")
check("到账后提示支付成功", any("支付成功" in str(t) for t, _x in MSGBOX), MSGBOX)

# 静态检查：客户端不再引用个人收款码链路
_src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                            "zhuzhu_Copilot", "ui", "agent_panel.py"),
               encoding="utf-8").read()
check("客户端源码不再引用 /api/shop/order 与 /api/shop/qrcodes",
      "/api/shop/order" not in _src and "/api/shop/qrcodes" not in _src)
check("旧的个人收款码信号已移除", "pay_qr_signal" not in _src)
check("Stripe 收款码生成函数存在", "_qr_pixmap" in _src and "qrcode" in _src)

print()
print(f"[SUMMARY] PASS={len(PASSES)} FAIL={len(FAILS)}")
if FAILS:
    print("[FAILURES]")
    for f in FAILS:
        print("  -", f)
    print("HAS FAILURES")
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
