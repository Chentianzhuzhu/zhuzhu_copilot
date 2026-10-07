# -*- coding: utf-8 -*-
"""验证：切换支付方式时收款码（QR）必须同步更新。

做法：不构造重量级 AgentPanel，而是用一个只带 pay_qr_signal 与少量样式属性的 QDialog
桩对象，直接调用真实的 AgentPanel._memb_show_pay_dialog / _on_pay_qr（被测代码零改动）。
网络与图片下载全部 mock：支付宝收款码=红色 PNG（慢 700ms），微信=蓝色 PNG（快 50ms）——
"慢"是刻意的：用来验证"迟到的旧方式结果必须被丢弃"（否则会把新码覆盖成旧码）。

断言：
  1) 首次打开：显示支付宝收款码（红）+ 支付宝订单号
  2) 切到微信：收款码变为蓝色 + 订单号变为微信订单（bug 修复点）
  3) 切回支付宝：再次刷新为红色（可反复切换，不是一次性）
  4) 过期结果丢弃：先切支付宝（慢）再切微信（快）→ 迟到的红色不得覆盖蓝色
  5) 主线程落地：收款码只经信号在主线程写入，工作线程不再直接操作控件
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS, PASSES, SKIPS = [], [], []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


from PyQt6.QtCore import QBuffer, QIODevice, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog,  # noqa: E402
                             QMessageBox)
from zhuzhu_Copilot.core import auth_client  # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as _ap  # noqa: E402
from PyQt6.QtCore import pyqtSignal  # noqa: E402

app = QApplication.instance() or QApplication([])

# ---------- mock：两种支付方式返回不同的收款码图片 ----------
def _png(color: str) -> bytes:
    pm = QPixmap(120, 120)
    pm.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(buf.data())


QR_BYTES = {"alipay": _png("#FF0000"), "wechat": _png("#0000FF")}
# 刻意让「支付宝」链路变慢：用于验证"迟到的旧方式收款码必须被丢弃"。
# 只让下单慢、取图不慢，保证时序可精确推算（下单+取图=0.1s 固定开销）。
HTTP_DELAY = {"alipay": 1.0, "wechat": 0.05}
CALLS = []


def fake_post(url, payload, token=None, timeout=None):
    method = (payload or {}).get("pay_method")
    CALLS.append(("order", method))
    time.sleep(HTTP_DELAY.get(method, 0.1))
    return True, {"code": 0, "data": {
        "order_id": f"ORD-{method}-{len(CALLS)}",
        "amount": 9,
        "pay_method": method,
        "qrcode_url": f"/static/uploads/qrcodes/{method}.png"}}, ""


def fake_get(url, token=None, timeout=None):
    CALLS.append(("qrcodes", url))
    return True, {"code": 0, "data": {"alipay": "/static/uploads/qrcodes/alipay.png",
                                      "wechat": "/static/uploads/qrcodes/wechat.png"}}, ""


class _Resp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d


def fake_urlopen(req, timeout=None):
    url = getattr(req, "full_url", str(req))
    key = "wechat" if "wechat" in url else "alipay"
    return _Resp(QR_BYTES[key])       # 取图不额外延时，时序只由下单延时决定


auth_client._http_post_json = fake_post
auth_client._http_get_json = fake_get
import urllib.request  # noqa: E402
urllib.request.urlopen = fake_urlopen
QMessageBox.information = staticmethod(lambda *a, **k: None)


class _Stub(QDialog):
    """最小宿主：只提供被测方法需要的信号与样式属性。"""
    pay_qr_signal = pyqtSignal(int, object, str, str)
    _on_pay_qr = _ap._AgentSettingsDialog._on_pay_qr    # 复用真实实现
    _TEXT, _DIM = "#F5F5F5", "#8A8A8A"
    _PANEL, _BORDER, _ACCENT = "#101010", "#000000", "#1E40AF"

    def _dialog_qss(self):
        return ""


stub = _Stub()
results = {}
ARRIVALS = []
T0 = time.time()
stub.pay_qr_signal.connect(stub._on_pay_qr)         # 与生产接线一致（worker → 主线程）
received = []


def _tap(gen, pm, t, o):
    received.append((gen, t, o))
    color = None
    if pm is not None and not pm.isNull():
        img = pm.toImage()
        color = QColor(img.pixel(img.width() // 2, img.height() // 2)).name().upper()
    ARRIVALS.append((round(time.time() - T0, 2), gen, color))
    if color == "#FF0000":
        results["stale_arrived"] = True


stub.pay_qr_signal.connect(_tap)


def _center_color(label):
    pm = label.pixmap()
    if pm is None or pm.isNull():
        return None
    img = pm.toImage()
    return QColor(img.pixel(img.width() // 2, img.height() // 2)).name().upper()


def _order_text():
    lbl = getattr(stub, "_pay_order_label", None)
    return lbl.text() if lbl is not None else ""


def _combo():
    dlg = next((d for d in stub.findChildren(QDialog) if d is not stub), None)
    return (dlg, dlg.findChild(QComboBox)) if dlg else (None, None)


STEPS = []
results = {}


def step(delay, fn):
    STEPS.append((delay, fn))


def s1():   # 首次打开：支付宝（慢，约 1.1s 返回）
    results["c1"] = _center_color(stub._pay_qr_label)
    results["o1"] = _order_text()
    print("  [t=1500] 初始收款码颜色:", results["c1"], "| 订单:", results["o1"])


def s2():   # 切到微信（快）
    dlg, combo = _combo()
    results["combo_found"] = combo is not None
    combo.setCurrentIndex(1)
    print("  [t=1600] 已切换支付方式 → 微信")


def s3():
    results["c2"] = _center_color(stub._pay_qr_label)
    results["o2"] = _order_text()
    print("  [t=2300] 微信收款码颜色:", results["c2"], "| 订单:", results["o2"])


def s4():   # 切回支付宝（慢）
    _combo()[1].setCurrentIndex(0)
    print("  [t=2400] 已切回 → 支付宝")


def s5():
    results["c3"] = _center_color(stub._pay_qr_label)
    results["o3"] = _order_text()
    print("  [t=3700] 支付宝收款码颜色:", results["c3"], "| 订单:", results["o3"])


def s6():   # 切到微信（快）
    _combo()[1].setCurrentIndex(1)
    print("  [t=3800] 切到微信")


def s6b():
    results["c_wechat"] = _center_color(stub._pay_qr_label)
    print("  [t=4600] 微信收款码颜色:", results["c_wechat"])


def s7():   # 过期结果场景：切支付宝（慢，gen N，迟到）→ 立刻切微信（快，gen N+1）
    _combo()[1].setCurrentIndex(0)
    print("  [t=4700] 选支付宝（慢请求在飞，gen N）")


def s7b():
    _combo()[1].setCurrentIndex(1)
    print("  [t=4800] 紧接着切到微信（快请求，gen N+1）")


def s8():
    results["c4"] = _center_color(stub._pay_qr_label)
    results["o4"] = _order_text()
    print("  [t=6400] 迟到结果之后收款码颜色:", results["c4"], "| 订单:", results["o4"])
    print("          收款码到达序列（相对秒, 批次, 颜色）:", ARRIVALS)


def s9():
    dlg, _c = _combo()
    dlg.accept()
    print("  [t=6500] 关闭支付对话框")


for _d, _f in ((1500, s1), (1600, s2), (2300, s3), (2400, s4), (3700, s5),
               (3800, s6), (4600, s6b), (4700, s7), (4800, s7b),
               (6400, s8), (6500, s9)):
    step(_d, _f)

for delay, fn in STEPS:
    QTimer.singleShot(delay, fn)

print("--- 打开支付对话框（内部 exec()，断言在事件循环中逐条执行）---")
_ap._AgentSettingsDialog._memb_show_pay_dialog(
    stub, type("Auth", (), {"server": "https://example.test", "token": "t"})(),
    {"id": "pro_monthly", "name": "Pro Plan", "price": 9})

print("--- 断言 ---")
check("首次打开显示支付宝收款码（红色）", results.get("c1") == "#FF0000",
      f"{results.get('c1')}")
check("首次打开生成支付宝订单", "alipay" in results.get("o1", ""),
      results.get("o1", ""))
check("切换支付方式后收款码更新为微信（蓝色）", results.get("c2") == "#0000FF",
      f"切换前 {results.get('c1')} → 切换后 {results.get('c2')}")
check("切换支付方式后订单号同步更新为微信订单", "wechat" in results.get("o2", ""),
      results.get("o2", ""))
check("切回支付宝会再次刷新（可反复切换）", results.get("c3") == "#FF0000",
      f"{results.get('c2')} → {results.get('c3')}")
check("迟到（过期）的支付宝结果被丢弃，未被覆盖回红色",
      results.get("c4") == "#0000FF", f"最终颜色 {results.get('c4')}")
check("最终订单号仍为最后一次选择的微信",
      "wechat" in results.get("o4", ""), results.get("o4", ""))
check("支付方式下拉存在且已接线切换刷新", results.get("combo_found") is True)
_n_orders = sum(1 for c in CALLS if c[0] == "order")
check("每次切换都真的重新下单（而非复用旧订单）", _n_orders >= 6,
      f"下单次数={_n_orders}")
check("收款码结果经信号回传（主线程落地）", len(received) >= 6,
      f"收到 {len(received)} 次信号")
check("切回微信后收款码为蓝色", results.get("c_wechat") == "#0000FF",
      f"{results.get('c_wechat')}")
# 真正的过期场景：慢的支付宝批次在快的微信批次**之后**才返回 → 必须被丢弃
_last = [a for a in ARRIVALS if a[2] is not None][-1] if ARRIVALS else None
check("迟到的支付宝（红）确实比微信（蓝）更晚返回",
      _last is not None and _last[2] == "#FF0000",
      f"最后到达={_last} 到达序列={ARRIVALS}")
check("迟到的旧批次被丢弃：界面仍停留在微信收款码",
      results.get("c4") == "#0000FF", f"最终颜色 {results.get('c4')}")
check("最终订单号仍为最后一次选择的微信",
      "wechat" in results.get("o4", ""), results.get("o4", ""))

# 静态检查（AST，避免注释里的字样造成误判）：工作线程内不得出现 setPixmap/setText 调用
import ast  # noqa: E402
_src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                            "zhuzhu_Copilot", "ui", "agent_panel.py"),
               encoding="utf-8").read()
_tree = ast.parse(_src)
_worker_calls = []
_reload_calls = []
for node in ast.walk(_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_load_order":
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr in ("setPixmap", "setText", "clear"):
                    _worker_calls.append(sub.func.attr)
check("后台线程不再直接操作控件（AST：无 setPixmap/setText/clear 调用）",
      not _worker_calls, f"worker 内控件调用={_worker_calls or '无'}")
check("切换支付方式已接线到刷新逻辑",
      "pay_combo.currentIndexChanged.connect" in _src
      and "_reload_qr()" in _src)
check("有批次号防过期覆盖（gen 判定）",
      "_pay_qr_gen" in _src and "_on_pay_qr" in _src
      and re.search(r"if int\(gen\) != int\(getattr\(self, \"_pay_qr_gen\"", _src) is not None)
check("关闭对话框后标签引用被摘除（防写向已销毁控件）",
      "_pay_qr_label = None" in _src and "_pay_order_label = None" in _src)
check("收款码信号声明在拥有该对话框的设置对话框类上",
      re.search(r"class _AgentSettingsDialog\(QDialog\):(?:.|\n){0,4000}?pay_qr_signal = "
                r"pyqtSignal", _src) is not None)

print()
print(f"[SUMMARY] PASS={len(PASSES)} FAIL={len(FAILS)} SKIP={len(SKIPS)}")
if FAILS:
    print("[FAILURES]")
    for f in FAILS:
        print("  -", f)
    print("HAS FAILURES")
    # 离屏 Qt 销毁阶段偶发崩溃：用 _exit 直接给出退出码，避免解释器退出噪声影响判读
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
