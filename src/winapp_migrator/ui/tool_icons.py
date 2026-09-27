# -*- coding: utf-8 -*-
"""工具专属线条矢量图标。

要求：**每个注册工具都有自己专属的图标**，而不是一律用同一个扳手。做法是
「族底图 + 动作角标」的二位组合：

- 底图（base）表示工具家族（文件 / 终端 / 浏览器 / 文档 / 表格 / 语音 / 工作流 …），
  画在左上，细线，作家族提示；
- 角标（ornament）表示具体动作（读 / 写 / 执行 / 搜索 / 增 / 删 / 折 / 展开 …），
  画在右下，稍粗，作主标识。

这样 20+ 种底图 × 20+ 种角标能覆盖全部工具且**两两不重复**（有测试守着唯一性），
新增工具只需在 `TOOL_ICON_SPEC` 里加一行，不需要新画图形。

风格约束沿用项目规范：淡灰线条、无 emoji、不填充大面积色块、颜色由调用方注入
（本模块不出现任何颜色字面量）。
"""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, QSizeF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

# 画布分区：底图缩到左上作族提示，角标在右下作主标识（两者都要能在 16px 下分辨）
_BASE_BOX = (0.02, 0.00, 0.68)
_ORNA_BOX = (0.36, 0.36, 0.62)


class _Glyph:
    """以 0..1 归一化坐标描述线条图形，再映射到画布上的一个方形盒。"""

    def __init__(self, p: QPainter, box: tuple):
        self.p = p
        self.x0, self.y0, self.k = box

    def _pt(self, x: float, y: float) -> QPointF:
        return QPointF(self.x0 + x * self.k, self.y0 + y * self.k)

    def _rect(self, x, y, w, h) -> QRectF:
        return QRectF(self._pt(x, y), QSizeF(w * self.k, h * self.k))

    def line(self, x1, y1, x2, y2):
        self.p.drawLine(self._pt(x1, y1), self._pt(x2, y2))

    def poly(self, pts, close=False):
        poly = QPolygonF([self._pt(x, y) for x, y in pts])
        if close:
            self.p.drawPolygon(poly)
        else:
            self.p.drawPolyline(poly)

    def rect(self, x, y, w, h, r=0.0):
        if r > 0:
            path_r = r * self.k
            self.p.drawRoundedRect(self._rect(x, y, w, h), path_r, path_r)
        else:
            self.p.drawRect(self._rect(x, y, w, h))

    def ellipse(self, x, y, w, h):
        self.p.drawEllipse(self._rect(x, y, w, h))

    def arc(self, x, y, w, h, start_deg, span_deg):
        self.p.drawArc(self._rect(x, y, w, h), int(start_deg * 16), int(span_deg * 16))


# ---------------------------------------------------------------------------
# 族底图
# ---------------------------------------------------------------------------
def _b_file(g: _Glyph):
    g.poly([(0.06, 0.10), (0.52, 0.10), (0.78, 0.36), (0.78, 0.94), (0.06, 0.94)], True)
    g.poly([(0.52, 0.10), (0.52, 0.36), (0.78, 0.36)])


def _b_folder(g: _Glyph):
    g.poly([(0.02, 0.24), (0.30, 0.24), (0.38, 0.36), (0.86, 0.36),
            (0.86, 0.92), (0.02, 0.92)], True)


def _b_terminal(g: _Glyph):
    g.rect(0.02, 0.14, 0.84, 0.74, 0.10)
    g.poly([(0.16, 0.38), (0.30, 0.50), (0.16, 0.62)])
    g.line(0.42, 0.62, 0.66, 0.62)


def _b_browser(g: _Glyph):
    g.rect(0.02, 0.14, 0.84, 0.74, 0.10)
    g.line(0.02, 0.34, 0.86, 0.34)
    g.line(0.16, 0.24, 0.16, 0.24)


def _b_clipboard(g: _Glyph):
    g.rect(0.12, 0.14, 0.64, 0.82, 0.08)
    g.rect(0.30, 0.06, 0.28, 0.14, 0.05)


def _b_doc(g: _Glyph):
    g.poly([(0.10, 0.06), (0.60, 0.06), (0.82, 0.28), (0.82, 0.94), (0.10, 0.94)], True)
    g.line(0.26, 0.44, 0.66, 0.44)
    g.line(0.26, 0.60, 0.66, 0.60)
    g.line(0.26, 0.76, 0.52, 0.76)


def _b_sheet(g: _Glyph):
    g.rect(0.06, 0.16, 0.76, 0.72, 0.06)
    g.line(0.06, 0.40, 0.82, 0.40)
    g.line(0.34, 0.16, 0.34, 0.88)
    g.line(0.58, 0.16, 0.58, 0.88)


def _b_slide(g: _Glyph):
    g.rect(0.04, 0.18, 0.82, 0.60, 0.06)
    g.line(0.40, 0.86, 0.50, 0.78)
    g.line(0.50, 0.78, 0.60, 0.86)
    g.line(0.34, 0.30, 0.60, 0.30)


def _b_image(g: _Glyph):
    g.rect(0.04, 0.16, 0.82, 0.68, 0.08)
    g.ellipse(0.20, 0.32, 0.16, 0.16)
    g.poly([(0.14, 0.78), (0.40, 0.46), (0.62, 0.78)])


def _b_pdf(g: _Glyph):
    g.poly([(0.10, 0.06), (0.60, 0.06), (0.82, 0.28), (0.82, 0.94), (0.10, 0.94)], True)
    g.rect(0.22, 0.56, 0.48, 0.22, 0.04)


def _b_branch(g: _Glyph):
    g.ellipse(0.06, 0.04, 0.20, 0.20)
    g.ellipse(0.06, 0.70, 0.20, 0.20)
    g.ellipse(0.62, 0.36, 0.20, 0.20)
    g.line(0.16, 0.24, 0.16, 0.70)
    g.poly([(0.26, 0.14), (0.62, 0.14), (0.62, 0.36)])


def _b_grid(g: _Glyph):
    for x in (0.06, 0.52):
        for y in (0.06, 0.52):
            g.rect(x, y, 0.40, 0.40, 0.08)


def _b_list(g: _Glyph):
    for y in (0.20, 0.48, 0.76):
        g.ellipse(0.06, y - 0.04, 0.10, 0.10)
        g.line(0.26, y, 0.86, y)


def _b_clock(g: _Glyph):
    g.ellipse(0.06, 0.06, 0.80, 0.80)
    g.line(0.46, 0.26, 0.46, 0.48)
    g.line(0.46, 0.48, 0.64, 0.58)


def _b_server(g: _Glyph):
    g.rect(0.08, 0.08, 0.76, 0.34, 0.06)
    g.rect(0.08, 0.52, 0.76, 0.34, 0.06)
    g.line(0.22, 0.25, 0.22, 0.25)
    g.line(0.22, 0.69, 0.22, 0.69)


def _b_globe(g: _Glyph):
    g.ellipse(0.06, 0.06, 0.80, 0.80)
    g.line(0.06, 0.46, 0.86, 0.46)
    g.ellipse(0.32, 0.06, 0.28, 0.80)


def _b_agent(g: _Glyph):
    g.ellipse(0.30, 0.06, 0.34, 0.34)
    g.arc(0.08, 0.44, 0.78, 0.78, 0, 180)


def _b_flow(g: _Glyph):
    g.rect(0.06, 0.06, 0.34, 0.28, 0.06)
    g.rect(0.06, 0.58, 0.34, 0.28, 0.06)
    g.rect(0.58, 0.32, 0.34, 0.28, 0.06)
    g.poly([(0.40, 0.20), (0.58, 0.20), (0.58, 0.34)])
    g.poly([(0.40, 0.72), (0.58, 0.72), (0.58, 0.58)])


def _b_mic(g: _Glyph):
    g.rect(0.32, 0.06, 0.28, 0.50, 0.14)
    g.arc(0.16, 0.28, 0.60, 0.52, 180, 180)
    g.line(0.46, 0.68, 0.46, 0.86)
    g.line(0.28, 0.90, 0.64, 0.90)


def _b_puzzle(g: _Glyph):
    g.poly([(0.08, 0.20), (0.36, 0.20), (0.36, 0.08), (0.54, 0.08), (0.54, 0.20),
            (0.86, 0.20), (0.86, 0.50), (0.74, 0.50), (0.74, 0.62), (0.86, 0.62),
            (0.86, 0.90), (0.08, 0.90)], True)


def _b_plug(g: _Glyph):
    g.line(0.16, 0.06, 0.16, 0.40)
    g.line(0.62, 0.06, 0.62, 0.40)
    g.rect(0.06, 0.40, 0.66, 0.30, 0.08)
    g.line(0.39, 0.70, 0.39, 0.94)


def _b_drive(g: _Glyph):
    g.rect(0.06, 0.14, 0.78, 0.66, 0.10)
    g.line(0.06, 0.56, 0.84, 0.56)
    g.ellipse(0.64, 0.66, 0.10, 0.10)


def _b_app(g: _Glyph):
    g.rect(0.06, 0.06, 0.78, 0.78, 0.16)
    g.ellipse(0.26, 0.26, 0.16, 0.16)
    g.line(0.52, 0.70, 0.74, 0.70)
    g.line(0.63, 0.59, 0.74, 0.70)
    g.line(0.63, 0.81, 0.74, 0.70)


_ICON_BASE = {
    "file": _b_file, "folder": _b_folder, "terminal": _b_terminal,
    "browser": _b_browser, "clipboard": _b_clipboard, "doc": _b_doc,
    "sheet": _b_sheet, "slide": _b_slide, "image": _b_image, "pdf": _b_pdf,
    "branch": _b_branch, "grid": _b_grid, "list": _b_list, "clock": _b_clock,
    "server": _b_server, "globe": _b_globe, "agent": _b_agent, "flow": _b_flow,
    "mic": _b_mic, "puzzle": _b_puzzle, "plug": _b_plug, "drive": _b_drive,
    "app": _b_app,
}


# ---------------------------------------------------------------------------
# 动作角标
# ---------------------------------------------------------------------------
def _o_eye(g: _Glyph):
    g.poly([(0.04, 0.50), (0.30, 0.16), (0.70, 0.16), (0.96, 0.50)], False)
    g.poly([(0.04, 0.50), (0.30, 0.84), (0.70, 0.84), (0.96, 0.50)], False)
    g.ellipse(0.38, 0.38, 0.24, 0.24)


def _o_pencil(g: _Glyph):
    g.poly([(0.10, 0.90), (0.24, 0.52), (0.76, 0.00), (0.98, 0.22), (0.46, 0.74)], True)
    g.line(0.24, 0.52, 0.46, 0.74)


def _o_search(g: _Glyph):
    g.ellipse(0.06, 0.06, 0.56, 0.56)
    g.line(0.58, 0.58, 0.96, 0.96)


def _o_plus(g: _Glyph):
    g.line(0.50, 0.06, 0.50, 0.94)
    g.line(0.06, 0.50, 0.94, 0.50)


def _o_minus(g: _Glyph):
    g.line(0.06, 0.50, 0.94, 0.50)


def _o_check(g: _Glyph):
    g.poly([(0.06, 0.52), (0.38, 0.84), (0.94, 0.18)])


def _o_cross(g: _Glyph):
    g.line(0.12, 0.12, 0.88, 0.88)
    g.line(0.88, 0.12, 0.12, 0.88)


def _o_play(g: _Glyph):
    g.poly([(0.16, 0.06), (0.16, 0.94), (0.92, 0.50)], True)


def _o_pause(g: _Glyph):
    g.rect(0.16, 0.08, 0.24, 0.84, 0.06)
    g.rect(0.60, 0.08, 0.24, 0.84, 0.06)


def _o_in(g: _Glyph):
    g.line(0.50, 0.00, 0.50, 0.60)
    g.poly([(0.24, 0.36), (0.50, 0.62), (0.76, 0.36)])
    g.line(0.06, 0.92, 0.94, 0.92)


def _o_out(g: _Glyph):
    g.line(0.50, 0.62, 0.50, 0.02)
    g.poly([(0.24, 0.28), (0.50, 0.02), (0.76, 0.28)])
    g.line(0.06, 0.92, 0.94, 0.92)


def _o_refresh(g: _Glyph):
    g.arc(0.06, 0.06, 0.88, 0.88, 40, 280)
    g.poly([(0.68, 0.02), (0.98, 0.20), (0.72, 0.34)])


def _o_swap(g: _Glyph):
    g.line(0.06, 0.32, 0.86, 0.32)
    g.poly([(0.66, 0.12), (0.90, 0.32), (0.66, 0.52)])
    g.line(0.94, 0.70, 0.14, 0.70)
    g.poly([(0.34, 0.50), (0.10, 0.70), (0.34, 0.90)])


def _o_right(g: _Glyph):
    g.line(0.06, 0.50, 0.86, 0.50)
    g.poly([(0.64, 0.24), (0.92, 0.50), (0.64, 0.76)])


def _o_down(g: _Glyph):
    g.line(0.50, 0.06, 0.50, 0.78)
    g.poly([(0.22, 0.54), (0.50, 0.84), (0.78, 0.54)])


def _o_list(g: _Glyph):
    for y in (0.18, 0.50, 0.82):
        g.line(0.06, y, 0.94, y)


def _o_gear(g: _Glyph):
    g.ellipse(0.28, 0.28, 0.44, 0.44)
    for x1, y1, x2, y2 in ((0.50, 0.00, 0.50, 0.18), (0.50, 0.82, 0.50, 1.00),
                           (0.00, 0.50, 0.18, 0.50), (0.82, 0.50, 1.00, 0.50),
                           (0.14, 0.14, 0.28, 0.28), (0.86, 0.14, 0.72, 0.28),
                           (0.14, 0.86, 0.28, 0.72), (0.86, 0.86, 0.72, 0.72)):
        g.line(x1, y1, x2, y2)


def _o_info(g: _Glyph):
    g.ellipse(0.04, 0.04, 0.92, 0.92)
    g.line(0.50, 0.42, 0.50, 0.76)
    g.line(0.50, 0.22, 0.50, 0.26)


def _o_star(g: _Glyph):
    g.poly([(0.50, 0.02), (0.63, 0.34), (0.98, 0.38), (0.72, 0.62),
            (0.80, 0.96), (0.50, 0.78), (0.20, 0.96), (0.28, 0.62),
            (0.02, 0.38), (0.37, 0.34)], True)


def _o_quest(g: _Glyph):
    g.arc(0.14, 0.06, 0.72, 0.60, 200, 220)
    g.line(0.50, 0.46, 0.50, 0.62)
    g.line(0.50, 0.84, 0.50, 0.88)


def _o_alert(g: _Glyph):
    g.poly([(0.50, 0.04), (0.98, 0.92), (0.02, 0.92)], True)
    g.line(0.50, 0.36, 0.50, 0.66)
    g.line(0.50, 0.80, 0.50, 0.84)


def _o_code(g: _Glyph):
    g.poly([(0.34, 0.16), (0.06, 0.50), (0.34, 0.84)])
    g.poly([(0.66, 0.16), (0.94, 0.50), (0.66, 0.84)])


def _o_click(g: _Glyph):
    g.poly([(0.20, 0.06), (0.20, 0.86), (0.38, 0.68), (0.52, 0.98),
            (0.66, 0.90), (0.52, 0.62), (0.76, 0.60)], True)


def _o_link(g: _Glyph):
    g.arc(0.00, 0.22, 0.46, 0.46, 60, 260)
    g.arc(0.54, 0.32, 0.46, 0.46, 240, 260)
    g.line(0.34, 0.44, 0.66, 0.56)


def _o_doc(g: _Glyph):
    g.poly([(0.14, 0.04), (0.62, 0.04), (0.86, 0.28), (0.86, 0.96), (0.14, 0.96)], True)
    g.line(0.34, 0.50, 0.66, 0.50)
    g.line(0.34, 0.70, 0.66, 0.70)


_ICON_ORNAMENT = {
    "eye": _o_eye, "pencil": _o_pencil, "search": _o_search, "plus": _o_plus,
    "minus": _o_minus, "check": _o_check, "cross": _o_cross, "play": _o_play,
    "pause": _o_pause, "in": _o_in, "out": _o_out, "refresh": _o_refresh,
    "swap": _o_swap, "right": _o_right, "down": _o_down, "list": _o_list,
    "gear": _o_gear, "info": _o_info, "star": _o_star, "quest": _o_quest,
    "alert": _o_alert, "code": _o_code, "click": _o_click, "link": _o_link,
    "doc": _o_doc,
}


# ---------------------------------------------------------------------------
# 工具 → (族底图, 动作角标)：每个注册工具一行，新增工具在此登记即可获得专属图标
# ---------------------------------------------------------------------------
TOOL_ICON_SPEC = {
    # ---- 应用与文件 ----
    "find_app": ("app", "search"),
    "search_files": ("folder", "search"),
    "grep": ("file", "search"),
    "search_code": ("terminal", "search"),
    "search_large": ("drive", "search"),
    "explore_project": ("folder", "eye"),
    "read_file": ("file", "eye"),
    "write_file": ("file", "out"),
    "edit_file": ("file", "pencil"),
    "search_replace": ("file", "swap"),
    "insert_lines": ("file", "plus"),
    "undo_file": ("file", "refresh"),
    "delete_file": ("file", "minus"),
    "list_directory": ("folder", "list"),
    "new_project": ("folder", "plus"),
    "ask_user": ("agent", "quest"),
    # ---- 命令与系统 ----
    "run_command": ("terminal", "play"),
    "check_command": ("terminal", "refresh"),
    "system_info": ("server", "info"),
    "get_time": ("clock", "eye"),
    "env_var": ("terminal", "gear"),
    "fast_download": ("globe", "down"),
    "migrate_app": ("app", "right"),
    "uninstall_app": ("app", "minus"),
    "clipboard": ("clipboard", "in"),
    # ---- 记忆与任务清单 ----
    "save_memory": ("drive", "in"),
    "load_memory": ("drive", "out"),
    "optimize_memory": ("drive", "star"),
    "update_todo": ("list", "check"),
    "list_todo": ("list", "eye"),
    # ---- 联网与浏览器 ----
    "web_search": ("globe", "search"),
    "web_fetch": ("globe", "in"),
    "browser_open": ("browser", "plus"),
    "browser_navigate": ("browser", "right"),
    "browser_snapshot": ("browser", "eye"),
    "browser_click": ("browser", "click"),
    "browser_type": ("browser", "pencil"),
    "browser_scroll": ("browser", "down"),
    "browser_eval": ("browser", "code"),
    "browser_html": ("browser", "doc"),
    "browser_close": ("browser", "cross"),
    "browser_tabs": ("browser", "list"),
    "browser_switch_tab": ("browser", "swap"),
    # ---- 文档与办公 ----
    "extract_text": ("doc", "out"),
    "create_docx": ("doc", "plus"),
    "create_pptx": ("slide", "plus"),
    "create_xlsx": ("sheet", "plus"),
    "beautify_docx": ("doc", "star"),
    "beautify_pptx": ("slide", "star"),
    "beautify_xlsx": ("sheet", "star"),
    "read_docx": ("doc", "eye"),
    "read_pptx": ("slide", "eye"),
    "read_xlsx": ("sheet", "eye"),
    "read_pdf": ("pdf", "eye"),
    "edit_docx": ("doc", "pencil"),
    "edit_pptx": ("slide", "pencil"),
    "edit_xlsx": ("sheet", "pencil"),
    "generate_image": ("image", "plus"),
    "git_info": ("branch", "info"),
    # ---- 语音 ----
    "tts_create_voice": ("mic", "plus"),
    "tts_query_voice": ("mic", "eye"),
    "tts_delete_voice": ("mic", "minus"),
    "tts_speak": ("mic", "play"),
    # ---- 工作流 ----
    "list_workflows": ("flow", "list"),
    "create_workflow": ("flow", "plus"),
    "list_builtin_workflows": ("flow", "star"),
    "switch_workflow": ("flow", "swap"),
    "list_workflow_agents": ("flow", "eye"),
    "use_workflow_agent": ("flow", "check"),
    "inspect_workflow": ("flow", "info"),
    "delete_workflow": ("flow", "minus"),
    # ---- Agent / 子 Agent / 网络 ----
    "list_agents": ("agent", "list"),
    "create_agent": ("agent", "plus"),
    "delete_agent": ("agent", "minus"),
    "edit_agent_file": ("agent", "pencil"),
    "list_sub_agents": ("agent", "out"),
    "register_sub_agent": ("agent", "in"),
    "dispatch_sub_agents": ("flow", "out"),
    "chat_with": ("agent", "right"),
    "look_context": ("doc", "search"),
    "pause_agent": ("agent", "pause"),
    "resume_agent": ("agent", "play"),
    "warn_agent": ("agent", "alert"),
    "shared_context": ("flow", "link"),
    "list_agents_network": ("globe", "list"),
    "register_agent_network": ("globe", "plus"),
    "set_session_name": ("list", "pencil"),
    # ---- 技能 / 插件 / 定制 ----
    "create_skill": ("puzzle", "plus"),
    "create_plugin": ("plug", "plus"),
    "manage_uiux": ("grid", "gear"),
    "register_panel_btn": ("grid", "plus"),
    "inspect_customization": ("grid", "info"),
    "register_feature_panel": ("grid", "star"),
    "set_feature_deps": ("grid", "link"),
    # ---- MCP ----
    "create_mcp": ("server", "plus"),
    "list_mcp": ("server", "list"),
    "set_mcp": ("server", "gear"),
}

# 未登记工具的兜底底图（保证任何工具名都能画出图标，不会渲染空白）
FALLBACK_SPEC = ("app", "gear")


def spec_of(tool: str) -> tuple:
    """工具名 → (底图, 角标)；未登记的工具回落到通用兜底组合"""
    return TOOL_ICON_SPEC.get(tool, FALLBACK_SPEC)


@lru_cache(maxsize=512)
def tool_icon(tool: str, size: int, color: str) -> QIcon:
    """工具专属线条矢量图标（组合绘制 + 缓存）。

    画布上先画族底图（左上、细线），再画动作角标（右下、稍粗），两者叠加后仍能在
    工具行 30px 图标壳（实际绘制约 20px）中分辨出「哪一族 + 做什么」。

    color 由调用方注入（主题色板），因此本模块**没有任何颜色字面量**，深浅主题
    与自定义 UI/UX 包都能直接复用（有回归测试守着该不变式）。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base, orna = spec_of(tool)
    col = QColor(color)

    def _pen(width: float) -> QPen:
        pen = QPen(col, max(1.0, width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    p.setBrush(Qt.BrushStyle.NoBrush)
    drawer = _ICON_BASE.get(base)
    if drawer is not None:
        p.setPen(_pen(size * 0.062))
        drawer(_Glyph(p, (size * _BASE_BOX[0], size * _BASE_BOX[1],
                            size * _BASE_BOX[2])))
    draw_orna = _ICON_ORNAMENT.get(orna)
    if draw_orna is not None:
        p.setPen(_pen(size * 0.076))
        draw_orna(_Glyph(p, (size * _ORNA_BOX[0], size * _ORNA_BOX[1],
                            size * _ORNA_BOX[2])))
    p.end()
    return QIcon(pm)
