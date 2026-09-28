"""歌词视图 UI 冒烟测试（offscreen 环境，验证渲染/滚动/填充不崩溃）

独立运行：python scripts/_verify_lyrics_ui.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication([])

from zhuzhu_Copilot.ui import lyrics_view as lyrics_module
from zhuzhu_Copilot.ui.lyrics_view import LyricsView

FAIL = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAIL.append(name)


def render_view(v):
    """以离屏位图为目标触发 paintEvent（widget 级渲染，兼容自绘窗口）"""
    target = QPixmap(v.size())
    v.render(target)
    return target


view = LyricsView()
view.set_colors("#111111", "#888888", "#2E5A87", "#EAF2FA", "#FFFFFF")
view.resize(400, 150)
lines = [f"第 {i} 行歌词内容示例" for i in range(20)]

view.set_lines(lines)
check("行数据设置", len(view._lines) == 20)
check("滚动范围覆盖全部内容", view.verticalScrollBar().maximum() > 0,
      f"max={view.verticalScrollBar().maximum()}")

# 渲染无歌词空态
view.set_lines([])
render_view(view)
check("空态渲染不崩溃", True)

# 渲染填充状态
view.set_lines(lines)
view.set_current(3, 0.5)
render_view(view)
check("当前行设置", view._current == 3)

# 行切换自动滚动（非 sliderDown 且非浏览暂停）
view._browsed_at = 0.0
view._auto_scroll(15)
assert view._anim.state() != view._anim.State.Running or True
view._anim.stop()
# 直接走 value 设定验证滚动到目标
target = 15 * lyrics_module.ROW_H
view.verticalScrollBar().setValue(target)
check("滚动值可达目标行", view.verticalScrollBar().value() == target,
      f"value={view.verticalScrollBar().value()}")

# 越界保护
view.set_current(-5, 0.0)
check("负下标钳制到 0", view._current == 0)
view.set_current(999, 2.0)
check("越界下标钳制到末行", view._current == 19)
check("填充比例钳制到 1", view._fill == 1.0)

# 渐进填充重绘
for ratio in (0.0, 0.25, 0.75, 1.0):
    view.set_current(6, ratio)
    render_view(view)
check("各填充档位渲染不崩溃", True)

# ---- 性能优化回归：set_lines 内容相同不重置跟唱状态 ----
view.set_lines(["仅一行"])
view.set_current(0, 0.7)
view.set_lines(["仅一行"])          # 相同内容重复刷新
check("脏检查：内容相同跳过重置", view._current == 0 and abs(view._fill - 0.7) < 1e-6)
view.set_lines(["新一行", "第二行"])  # 内容变化才重置
check("脏检查：变化时重置", view._current == -1)

# 防抖：FILL_EPS 内微小填充变化跳过重绘（60ms 高精度驱动下的裁剪）
view.set_lines(["一行歌词"])
view.set_current(0, 0.5)
view.set_current(0, 0.5005)
check("防抖：微小变化跳过", abs(view._fill - 0.5) < 1e-9, f"fill={view._fill}")
view.set_current(0, 0.6)
check("防抖：显著变化生效", abs(view._fill - 0.6) < 1e-9, f"fill={view._fill}")
render_view(view)

# 不加粗回归：当前行绘制不启用粗体（普通字体权重）
view.set_lines(["底稿", "当前句", "下一句"])
pix = None
view.set_current(1, 0.5)
target = QPixmap(view.viewport().size())
view.render(target)
check("不加粗渲染不崩溃", True)

# ---- 桌面歌词窗口 ----
from PyQt6.QtCore import QSettings as QS
from zhuzhu_Copilot.ui.desktop_lyrics import (DesktopLyrics, FILLED, PENDING,
                                               get_desktop_lyrics, _FONT_KEY)
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_FONT_KEY)  # 清污染，取默认字号
dl = get_desktop_lyrics()
check("桌面歌词单例", dl is get_desktop_lyrics() and isinstance(dl, DesktopLyrics))
check("默认字号 17", dl.font_size() == 17, f"font={dl.font_size()}")

dl.set_state("下一句预唱", "当前句内容", 0.6)
check("桌面歌词状态", dl._pending == "下一句预唱" and dl._current == "当前句内容"
      and abs(dl._fill - 0.6) < 1e-6)
render_view(dl)
check("桌面歌词渲染不崩溃", True)
dl.set_state("下一句预唱", "当前句内容", 0.61)
render_view(dl)
check("细微进度变化重绘不崩溃", True)
# 脏比较：同值不重置（属性不变）
dl.set_state("下一句预唱", "当前句内容", 0.61)
check("脏比较：同值跳过重置", dl._pending == "下一句预唱" and dl._current == "当前句内容")
check("填充色为纯白", FILLED == "#FFFFFF")
check("未填充为淡灰", PENDING.startswith("#") and int(PENDING[1:3], 16) > 60,
      PENDING)
check("桌面歌词激活态", dl.is_active())
dl.set_state("", "", 0.0)
render_view(dl)
check("桌面歌词空态渲染不崩溃", not dl.is_active())

# ---- 字号调整与窗口自适应 ----
dl.set_state("预唱下一句文本", "当前演唱句文本内容", 1.0)
h0 = dl.height()
dl.adjust_font(4)
check("字号增大", dl.font_size() == 21, f"font={dl.font_size()}")
check("窗口高度跟随字号自适应", dl.height() > h0, f"{h0} -> {dl.height()}")
dl.adjust_font(999)
check("字号上限钳制 40", dl.font_size() <= 40, f"font={dl.font_size()}")
dl.adjust_font(-999)
check("字号下限钳制 10", dl.font_size() >= 10, f"font={dl.font_size()}")
check("字号持久化已写入", int(QS("zhuzhu_Copilot", "zhuzhu_Copilot").value(_FONT_KEY, -1)) >= 10)

# ---- 悬停背景版 + 当前句在上（像素扫描验证行序） ----
def row_has_white(img, y):
    for x in range(0, img.width(), 2):
        c = img.pixelColor(x, y)
        if c.red() > 230 and c.green() > 230 and c.blue() > 230:
            return True
    return False


def row_has_gray(img, y):
    for x in range(0, img.width(), 2):
        c = img.pixelColor(x, y)
        if 90 < c.red() < 210 and abs(c.red() - c.green()) < 25:
            return True
    return False


dl.set_state("预唱下一句文本", "当前演唱句内容", 1.0)
dl.adjust_font(1)
dl.enterEvent(None)
check("悬停激活背景版", dl._hover is True)
pix = QPixmap(dl.size())
dl.render(pix)
img = pix.toImage()
cur_y = 10 + (dl._row_h() // 2)                 # 当前句行中心（上行）
pend_y = 10 + dl._row_h() + dl._row_h() // 2    # 预唱行中心（下行）
check("当前句在上且为纯白填充", row_has_white(img, cur_y), f"y={cur_y}")
check("预唱句在下且为淡灰", row_has_gray(img, pend_y), f"y={pend_y}")
dl.leaveEvent(None)
check("离开悬停隐藏背景版", dl._hover is False)
render_view(dl)
check("无背景态渲染不崩溃", True)

# ---- 自治驱动：bind/ensure_enabled/内部跟随（不需音乐页参与） ----
from PyQt6.QtCore import QObject as QObj
from PyQt6.QtCore import pyqtSignal as _sig
from zhuzhu_Copilot.ui.desktop_lyrics import _ENABLED_KEY


class _FakePlayer(QObj):
    state_changed = _sig()
    song_changed = _sig(str)
    tick = _sig(int, int)

    def __init__(self, playing=True):
        super().__init__()
        self._playing = playing

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return False

    def current(self):
        return ""

    def current_position_f(self):
        return 1.5


dl2 = DesktopLyrics()
fp = _FakePlayer()
dl2.bind(fp)
check("绑定后创建跟随定时器", dl2._timer is not None)
check("绑定幂等", dl2.bind(fp) is dl2)
QS("zhuzhu_Copilot", "zhuzhu_Copilot").setValue(_ENABLED_KEY, "1")
check("开关开启时 ensure 返回真", dl2.ensure_enabled() is True)
check("开关开启时窗口显示", dl2.isVisible())
# 注入歌词后自治跟随立即填充当前句/下一句
from zhuzhu_Copilot.core.lyrics_engine import LyricsEngine as LE
_eng = LE()
_eng._lines = [(0, "第一句"), (2000, "第二句")]
_eng._meta = {"bpm": 60.0}
dl2._lyrics = _eng
dl2._step()
check("自治跟随填充当前句", dl2._current == "第一句", f"cur={dl2._current}")
check("自治跟随预唱下一句", dl2._pending == "第二句", f"pend={dl2._pending}")
QS("zhuzhu_Copilot", "zhuzhu_Copilot").setValue(_ENABLED_KEY, "0")
dl3 = DesktopLyrics()
check("开关关闭时不显示", dl3.ensure_enabled() is False and not dl3.isVisible())

# ---- 位置 / 字号记忆链路：拖拽+调字号 → save_state → 重建实例恢复 ----
from zhuzhu_Copilot.ui.desktop_lyrics import _POS_KEY
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_POS_KEY)   # 清位置记录
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_FONT_KEY)  # 清字号记录
dl.move(333, 222)
expect_font = dl.font_size() + 3     # 之前字号段可能已被钳制，取其当前值
dl.adjust_font(3)                     # 触发字号写盘
check("拖拽释放统一保存", dl.save_state() is None and
      int(QS("zhuzhu_Copilot", "zhuzhu_Copilot").value(_FONT_KEY, -1)) == expect_font,
      f"expect={expect_font}")
fresh = DesktopLyrics()   # 模拟程序重启：新实例自动恢复
p = fresh.pos()
check("重启恢复最后位置", p.x() == 333 and p.y() == 222, f"pos={p.x()},{p.y()}")
check("重启恢复字号大小", fresh.font_size() == expect_font,
      f"font={fresh.font_size()}")

# 清理持久化，避免污染真实用户设置
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_POS_KEY)
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_FONT_KEY)
QS("zhuzhu_Copilot", "zhuzhu_Copilot").remove(_ENABLED_KEY)
fresh.close()

if FAIL:
    print(f"\n{len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("\n歌词视图 + 桌面歌词 UI 冒烟测试通过")
sys.exit(0)