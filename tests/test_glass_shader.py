"""液体玻璃窗口：GPU 着色器真实编译 + 离屏渲染验收（无 GL 环境自动跳过）。

验收的不是"有没有崩"，而是**像素级行为**：
  · 形状外（角落）必须全透明，形状内必须有不透明像素；
  · 边缘必须比中心亮（菲涅尔亮边）；
  · 折射必须真的改变了采样位置（把同一张背景按有无折射渲染，中心像素应不同）。
"""

import os
import sys
from pathlib import Path

import pytest

# conftest 默认强制离屏（离屏没有 GL）。要真跑 GPU 验收就显式打开：
#   GLASS_TEST_GL=1 python -m pytest tests/test_glass_shader.py
if os.environ.get("GLASS_TEST_GL") == "1":
    os.environ["QT_QPA_PLATFORM"] = "windows"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _checkerboard(w=420, h=300, cell=30) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#101828"))
    p = QPainter(img)
    for gy in range(0, h, cell):
        for gx in range(0, w, cell):
            if ((gx // cell) + (gy // cell)) % 2 == 0:
                p.fillRect(gx, gy, cell, cell, QColor("#F59E0B"))
            else:
                p.fillRect(gx, gy, cell, cell, QColor("#1E3A8A"))
    p.end()
    return img


def test_glass_shader_renders(app):
    from zhuzhu_Copilot.ui import glass_window as gw

    if not gw.gl_available():
        pytest.skip("当前环境无 OpenGL 2.1 上下文（远程桌面/离屏）")

    surface_cls = gw._make_surface_class()
    surf = surface_cls()
    surf.resize(420, 300)
    surf.show()
    app.processEvents()
    assert surf.gpu_ready(), "着色器未就绪：" + surf.failure()

    bg = _checkerboard()
    surf.set_background(bg, 20.0, surf.size())
    surf.set_time(1.7)
    app.processEvents()

    fbo = surf.grabFramebuffer()
    assert not fbo.isNull() and fbo.width() >= 420

    def a(x, y):
        return fbo.pixelColor(x, y).alpha()

    w, h = fbo.width(), fbo.height()
    assert a(2, 2) < 12, "形状外（左上角）必须透明"
    assert a(w // 2, h // 2) > 90, "形状中心必须有不透明玻璃"

    # 边缘亮边：上边缘一行的平均亮度应高于中心行
    def row_lum(y):
        vals = [fbo.pixelColor(x, y) for x in range(w // 3, w * 2 // 3, 7)]
        return sum(c.red() + c.green() + c.blue() for c in vals) / (3.0 * len(vals))

    edge = max(row_lum(28), row_lum(30), row_lum(h - 30))
    center = row_lum(h // 2)
    assert edge > center + 6, f"边缘高光不明显：edge={edge:.1f} center={center:.1f}"

    # 折射：透镜效应集中在**边缘**（中心 lens=1、偏移为 0 是正确物理），
    # 所以在边缘内侧取一排像素，比较「无折射 vs 强折射」是否有差异。
    def edge_band():
        im = surf.grabFramebuffer()
        return [im.pixelColor(x, 46) for x in range(w // 4, w * 3 // 4, 5)]

    surf.set_params(thickness=0.0)
    app.processEvents()
    flat = edge_band()
    surf.set_params(thickness=120.0)
    app.processEvents()
    bent = edge_band()
    surf.close()
    diff = sum(1 for a, b in zip(flat, bent) if a != b)
    assert diff >= len(flat) // 3, (
        "折射没有生效：边缘带只有 %d/%d 个像素变化" % (diff, len(flat)))


def test_glass_window_fallback_ok(app):
    """无 GL 也必须能用：降级面存在、窗口能显示、诊断可读。"""
    from zhuzhu_Copilot.ui.glass_window import GlassWindow

    win = GlassWindow(title="t", width=360, height=280)
    win.show()
    app.processEvents()
    d = win.diagnostics()
    assert "gpu" in d and "surface_error" in d
    assert win.surface() is not None
    win.close()
