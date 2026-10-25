"""代码地图页（Copilot 插件 code-map 的界面部分）。

一块只读看板：选目录 → 后台扫描 → 文件数 / 代码行 / 体积 / 耗时 + 类型与目录分布
+ 最大的文件。扫描在独立线程里跑（页面不卡），结果与 `code_map` 工具共用同一份缓存，
所以「服务预热过的目录」打开页面立刻就有数。

页面只在可见时刷新（showEvent/hideEvent 起停定时器），不可见时零开销。
"""

import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)


def _theme():
    """从主面板取当前主题色（跟随主题切换；取不到时用安全兜底色）。"""
    try:
        from zhuzhu_Copilot.ui import agent_panel as ap
        return {
            "text": getattr(ap, "TEXT", "#E6E8EC"),
            "dim": getattr(ap, "TEXT_DIM", "#98A2B3"),
            "card": getattr(ap, "CARD", getattr(ap, "PANEL", "#1A2028")),
            "border": getattr(ap, "BORDER", "#2A323C"),
            "accent": getattr(ap, "ACCENT", "#3B82F6"),
            "ok": getattr(ap, "OK", "#22C55E"),
            "warn": getattr(ap, "WARN", "#F59E0B"),
        }
    except Exception:
        return {"text": "#E6E8EC", "dim": "#98A2B3", "card": "#1A2028",
                "border": "#2A323C", "accent": "#3B82F6", "ok": "#22C55E",
                "warn": "#F59E0B"}


def _kernel():
    """代码地图内核（源码级变更未生效时返回 None）。"""
    try:
        from zhuzhu_Copilot.core import code_map
        return code_map
    except Exception:
        return None


def _esc(text) -> str:
    s = str(text or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _human(n) -> str:
    """字节 → 人类可读（B/KB/MB/GB）。"""
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return ("%.0f %s" % (v, unit)) if unit == "B" else ("%.1f %s" % (v, unit))
        v /= 1024.0
    return "%.1f GB" % v


def _mid(path: str, keep: int = 34) -> str:
    """长路径中间省略（保留头尾，便于识别盘符与末级目录）。"""
    s = str(path or "")
    if len(s) <= keep:
        return s
    head = s[: keep // 2 - 2]
    tail = s[-(keep // 2 - 1):]
    return head + "…" + tail


class _Tile(QFrame):
    """一张指标卡：标题 + 主数值 + 副说明。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        self.lbl_title = QLabel(title)
        self.lbl_value = QLabel("—")
        self.lbl_sub = QLabel("")
        f = QFont(self.lbl_value.font())
        f.setPointSize(max(13, f.pointSize() + 3))
        f.setBold(True)
        self.lbl_value.setFont(f)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_value)
        lay.addWidget(self.lbl_sub)
        self.restyle()

    def restyle(self):
        c = _theme()
        self.lbl_title.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        self.lbl_value.setStyleSheet("color: %s;" % c["text"])
        self.lbl_sub.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        self.setStyleSheet("QFrame { background: %s; border: 1px solid %s;"
                           " border-radius: 10px; }" % (c["card"], c["border"]))

    def set(self, value: str, sub: str = "", color: str = ""):
        c = _theme()
        self.lbl_value.setText(value)
        self.lbl_value.setStyleSheet("color: %s;" % (color or c["text"]))
        self.lbl_sub.setText(sub)


class CodeMapPage(QWidget):
    """代码地图页（由插件清单登记为 Copilot 浮层页签）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._busy = False
        self._worker = None
        self._poll = None
        self._tick = None
        self._build()
        self._adopt_cache()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self):
        c = _theme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("代码地图")
        f = QFont(title.font())
        f.setBold(True)
        f.setPointSize(max(12, f.pointSize() + 2))
        title.setFont(f)
        title.setStyleSheet("color: %s;" % c["text"])
        head.addWidget(title)
        self.lbl_root = QLabel("未选择目录")
        self.lbl_root.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        head.addWidget(self.lbl_root)
        head.addStretch(1)
        for text, slot in (("选择目录", self._pick_dir), ("重新扫描", self._rescan),
                           ("复制报告", self._copy)):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(26)
            b.clicked.connect(lambda _c=False, _s=slot: _s())
            head.addWidget(b)
        outer.addLayout(head)

        self.lbl_hint = QLabel("选一个目录开始扫描；也可以在对话里让 AI 调用 code_map 工具。")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        outer.addWidget(self.lbl_hint)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles = {
            "files": _Tile("文件"),
            "lines": _Tile("代码行"),
            "size": _Tile("总体积"),
            "time": _Tile("扫描耗时"),
        }
        for i, key in enumerate(("files", "lines", "size", "time")):
            grid.addWidget(self.tiles[key], 0, i)
        outer.addLayout(grid)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.lbl_ext = self._rich_label()
        body.addWidget(self._box("按类型", self.lbl_ext), 1)
        self.lbl_files = self._rich_label()
        body.addWidget(self._box("最大的文件", self.lbl_files), 1)
        outer.addLayout(body, 1)

        self.lbl_dirs = self._rich_label()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.lbl_dirs)
        scroll.setMaximumHeight(140)
        outer.addWidget(self._box("按目录（行数占比）", scroll))

    def _rich_label(self) -> QLabel:
        c = _theme()
        lbl = QLabel("—")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 12px; color: %s; background: transparent;"
                          " border: none;" % c["text"])
        return lbl

    def _box(self, title: str, child: QWidget) -> QFrame:
        c = _theme()
        box = QFrame()
        box.setStyleSheet("QFrame { background: %s; border: 1px solid %s;"
                          " border-radius: 10px; }" % (c["card"], c["border"]))
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 11px; color: %s; border: none;" % c["dim"])
        lay.addWidget(lbl)
        lay.addWidget(child, 1)
        return box

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------
    def _adopt_cache(self):
        """有缓存（服务预热过）就直接显示，不重复扫。"""
        k = _kernel()
        if k is None:
            self.lbl_hint.setText(
                "代码地图内核未加载：请启用插件并重启应用（源码级变更需要重启才生效）。")
            return
        try:
            data = k.cached()
        except Exception:
            data = {}
        if data:
            self._render(data)
        else:
            root = ""
            try:
                root = k.load_root()
            except Exception:
                pass
            if root:
                self.lbl_root.setText(_mid(root))
                self.lbl_hint.setText("已有上次的目录记录，点「重新扫描」或稍候自动扫描。")
                self._start_scan(root)

    def _pick_dir(self):
        start = ""
        k = _kernel()
        if k is not None:
            try:
                start = k.load_root()
            except Exception:
                start = ""
        d = QFileDialog.getExistingDirectory(self, "选择要扫描的目录", start or "")
        if d:
            self._start_scan(d)

    def _rescan(self):
        k = _kernel()
        if k is None:
            return
        root = ""
        try:
            root = k.load_root()
        except Exception:
            root = ""
        if not root and self._data:
            root = self._data.get("root") or ""
        if root:
            self._start_scan(root)
        else:
            self._pick_dir()

    def _start_scan(self, root: str):
        k = _kernel()
        if k is None or self._busy:
            return
        if not Path(root).is_dir():
            self.lbl_hint.setText("目录不存在：%s" % root)
            return
        self._busy = True
        self.lbl_root.setText(_mid(root))
        self.lbl_hint.setText("扫描中…（大目录会自动截断，不会卡住界面）")
        box = {}

        def _run():
            try:
                box["data"] = k.scan(root)
            except Exception as e:          # 线程里绝不抛出
                box["error"] = str(e)

        self._worker = threading.Thread(target=_run, daemon=True)
        self._worker.start()
        if self._poll is None:
            self._poll = QTimer(self)
            self._poll.setInterval(150)
            self._poll.timeout.connect(lambda: self._check(box))
        self._poll.start()
        self._box = box

    def _check(self, box):
        if self._worker is not None and self._worker.is_alive():
            return
        if self._poll is not None:
            self._poll.stop()
        self._busy = False
        err = box.get("error")
        if err:
            self.lbl_hint.setText("扫描失败：%s" % err)
            return
        data = box.get("data") or {}
        if data:
            try:
                k = _kernel()
                if k is not None:
                    k.save_root(data["root"])
                    k.store_cache(data)
            except Exception:
                pass
            self._render(data)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(self, data: dict):
        c = _theme()
        self._data = data or {}
        d = self._data
        root = d.get("root") or ""
        self.lbl_root.setText(_mid(root))
        for t in self.tiles.values():
            t.restyle()
        self.tiles["files"].set(
            format(d.get("files", 0), ","),
            "代码文件 %s 个 · 目录 %s 个"
            % (format(d.get("code_files", 0), ","), format(d.get("dirs", 0), ",")))
        self.tiles["lines"].set(format(d.get("code_lines", 0), ","),
                                "只统计代码/文本类后缀", c["ok"])
        self.tiles["size"].set(_human(d.get("bytes", 0)),
                               "跳过二进制与依赖目录")
        trunc = d.get("truncated")
        self.tiles["time"].set("%.2fs" % float(d.get("elapsed") or 0),
                               "已达上限截断（部分结果）" if trunc else "完整扫描",
                               c["warn"] if trunc else c["text"])

        rows = (d.get("by_ext") or [])[:10]
        if rows:
            total = max(1, max(r["lines"] for r in rows))
            out = ["<table cellspacing='0' cellpadding='3' width='100%'>"]
            for r in rows:
                w = max(2, int(96 * r["lines"] / total))
                out.append(
                    "<tr><td style='font-family:Consolas;color:%s;width:64px'>%s</td>"
                    "<td style='color:%s;width:52px' align='right'>%s</td>"
                    "<td><div style='background:%s;height:8px;width:%dpx;"
                    "border-radius:4px;'></div></td>"
                    "<td style='color:%s;width:76px' align='right'>%s 行</td></tr>"
                    % (c["text"], _esc(r["ext"]), c["dim"],
                       format(r["files"], ","), c["accent"], w, c["dim"],
                       format(r["lines"], ",")))
            out.append("</table>")
            self.lbl_ext.setText("".join(out))
        else:
            self.lbl_ext.setText("<span style='color:%s'>无代码文件</span>" % c["dim"])

        tops = (d.get("top_files") or [])[:10]
        if tops:
            out = ["<table cellspacing='0' cellpadding='3' width='100%'>"]
            for r in tops:
                out.append(
                    "<tr><td style='font-family:Consolas;color:%s'>%s</td>"
                    "<td align='right' style='color:%s;width:70px'>%s 行</td>"
                    "<td align='right' style='color:%s;width:64px'>%s</td></tr>"
                    % (c["text"], _esc(_mid(r["path"], 40)), c["dim"],
                       format(r["lines"], ","), c["dim"], _human(r["bytes"])))
            out.append("</table>")
            self.lbl_files.setText("".join(out))
        else:
            self.lbl_files.setText("<span style='color:%s'>—</span>" % c["dim"])

        dirs = (d.get("by_dir") or [])[:10]
        if dirs:
            total = max(1, max(r["lines"] for r in dirs) or 1)
            out = []
            for r in dirs:
                w = max(2, int(100 * (r["lines"] or 0) / total))
                out.append(
                    "<div style='margin:0 0 5px 0'>"
                    "<span style='font-family:Consolas;color:%s'>%s</span>"
                    "<span style='color:%s'> · %s 文件 · %s 行</span>"
                    "<div style='background:%s;height:7px;width:%d%%;"
                    "border-radius:4px;margin-top:2px'></div></div>"
                    % (c["text"], _esc(r["dir"][:28]), c["dim"],
                       format(r["files"], ","), format(r["lines"], ","),
                       c["accent"], w))
            self.lbl_dirs.setText("".join(out))
        else:
            self.lbl_dirs.setText("<span style='color:%s'>—</span>" % c["dim"])

        self.lbl_hint.setText(
            "扫描于 %s · 已排除 .git/node_modules/dist 等依赖与产物目录 · 符号链接不跟随"
            % time.strftime("%H:%M:%S"))

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _copy(self):
        k = _kernel()
        if k is None or not self._data:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(k.format_report(self._data, top=10))
            self.lbl_hint.setText("报告已复制到剪贴板。")
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        if self._tick is None:
            self._tick = QTimer(self)
            self._tick.setInterval(2000)
            self._tick.timeout.connect(self._soft_refresh)
        self._tick.start()
        if not self._data:
            self._adopt_cache()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._tick is not None:
            self._tick.stop()

    def _soft_refresh(self):
        """可见期间的轻刷新：只跟随内核缓存变化，不主动触发扫描。"""
        if self._busy or not self._data:
            return
        k = _kernel()
        if k is None:
            return
        try:
            data = k.cached()
        except Exception:
            return
        if data and data.get("root") != self._data.get("root"):
            self._render(data)


def quick_report():
    """托盘动作 / 快捷键：弹窗展示代码地图报告（必要时先扫一次）。"""
    from PyQt6.QtWidgets import QMessageBox
    k = _kernel()
    if k is None:
        QMessageBox.information(None, "代码地图",
                                "内核未加载：请启用插件并重启应用。")
        return
    data = k.cached(max_age=600)
    if not data:
        root = k.load_root()
        if not root:
            QMessageBox.information(
                None, "代码地图",
                "还没有扫描过目录。请在 Copilot 浮层的「代码地图」页里选一个目录，"
                "或让 AI 调用 code_map 工具。")
            return
        data = k.cache_scan(root)
    if not data:
        QMessageBox.warning(None, "代码地图", "扫描失败，请检查目录是否可访问。")
        return
    box = QMessageBox()
    box.setWindowTitle("代码地图")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(k.format_report(data, top=8))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
