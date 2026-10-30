"""性能面板页（Copilot 插件 perf-turbo 的界面部分）。

一个只读看板 + 三档切换：
  · 顶部：档位（激进 / 均衡 / 保守）、刷新、清空、复制报告；
  · 指标：主线程响应间隔、首字延迟、输出吞吐、渲染缓存命中率；
  · 慢工具榜：按 P95 排序，一眼看出「哪一步在拖时间」；
  · 建议：按实测数据规则化生成（不猜、不夸大）。

页面只在可见时刷新（showEvent/hideEvent 起停 1s 定时器），不可见时零开销。
所有读数来自 `zhuzhu_Copilot.core.perf_turbo` 的单例，与服务/工具共用同一份统计。
"""

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)


def _theme():
    """从主面板取当前主题色（主题切换后自动跟随；取不到时用安全兜底色）。"""
    try:
        from zhuzhu_Copilot.ui import agent_panel as ap
        return {
            "text": getattr(ap, "TEXT", "#E6E8EC"),
            "dim": getattr(ap, "TEXT_DIM", "#98A2B3"),
            "panel": getattr(ap, "PANEL", "#151A21"),
            "card": getattr(ap, "CARD", getattr(ap, "PANEL", "#1A2028")),
            "border": getattr(ap, "BORDER", "#2A323C"),
            "accent": getattr(ap, "ACCENT", "#3B82F6"),
            "ok": getattr(ap, "OK", "#22C55E"),
            "warn": getattr(ap, "WARN", "#F59E0B"),
            "err": getattr(ap, "ERR", "#EF4444"),
        }
    except Exception:
        return {"text": "#E6E8EC", "dim": "#98A2B3", "panel": "#151A21",
                "card": "#1A2028", "border": "#2A323C", "accent": "#3B82F6",
                "ok": "#22C55E", "warn": "#F59E0B", "err": "#EF4444"}


def _kernel():
    """性能内核（源码级变更未生效时返回 None，页面降级为只读提示）。"""
    try:
        from zhuzhu_Copilot.core import perf_turbo
        return perf_turbo
    except Exception:
        return None


class _Tile(QFrame):
    """一张指标卡：标题 + 主数值 + 副说明。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        self.lbl_title = QLabel(title)
        self.lbl_value = QLabel("—")
        self.lbl_sub = QLabel("")
        self.lbl_title.setStyleSheet("font-size: 11px;")
        f = QFont(self.lbl_value.font())
        f.setPointSize(max(13, f.pointSize() + 3))
        f.setBold(True)
        self.lbl_value.setFont(f)
        self.lbl_sub.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_value)
        lay.addWidget(self.lbl_sub)

    def set(self, value: str, sub: str = "", color: str = ""):
        c = _theme()
        self.lbl_title.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        self.lbl_value.setText(value)
        self.lbl_value.setStyleSheet("color: %s;" % (color or c["text"]))
        self.lbl_sub.setText(sub)
        self.lbl_sub.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        self.setStyleSheet("QFrame { background: %s; border: 1px solid %s;"
                           " border-radius: 10px; }" % (c["card"], c["border"]))


class PerfPage(QWidget):
    """性能看板页（由插件清单登记为 Copilot 浮层页签）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = None
        self._build()

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
        title = QLabel("性能看板")
        f = QFont(title.font())
        f.setBold(True)
        f.setPointSize(max(12, f.pointSize() + 2))
        title.setFont(f)
        title.setStyleSheet("color: %s;" % c["text"])
        head.addWidget(title)
        head.addSpacing(8)

        self._profile_btns = []
        for key, label in (("aggressive", "激进"), ("balanced", "均衡"),
                           ("conservative", "保守")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(26)
            b.clicked.connect(lambda _c=False, _k=key: self._pick_profile(_k))
            head.addWidget(b)
            self._profile_btns.append((key, b))
        head.addStretch(1)

        for text, slot in (("刷新", self.refresh), ("清空统计", self._reset),
                           ("复制报告", self._copy)):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(26)
            b.clicked.connect(lambda _c=False, _s=slot: _s())
            head.addWidget(b)
        outer.addLayout(head)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("font-size: 11px; color: %s;" % c["dim"])
        outer.addWidget(self.lbl_hint)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles = {
            "ui": _Tile("主线程响应 P95"),
            "ttft": _Tile("首字延迟 P95"),
            "cps": _Tile("输出吞吐"),
            "md": _Tile("渲染缓存命中"),
        }
        for i, key in enumerate(("ui", "ttft", "cps", "md")):
            grid.addWidget(self.tiles[key], 0, i)
        outer.addLayout(grid)

        body = QHBoxLayout()
        body.setSpacing(10)

        self.lbl_tools = QLabel("—")
        self.lbl_tools.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_tools.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_tools.setWordWrap(True)
        self.lbl_tools.setStyleSheet("font-size: 12px; color: %s;" % c["text"])
        box_t = self._box("工具耗时（按 P95 排序）", self.lbl_tools)
        body.addWidget(box_t, 1)

        self.lbl_advice = QLabel("—")
        self.lbl_advice.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_advice.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_advice.setWordWrap(True)
        self.lbl_advice.setStyleSheet("font-size: 12px; color: %s;" % c["text"])
        box_a = self._box("诊断建议", self.lbl_advice)
        body.addWidget(box_a, 1)
        outer.addLayout(body, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.lbl_turns = QLabel("—")
        self.lbl_turns.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_turns.setStyleSheet("font-size: 12px; color: %s;" % c["text"])
        scroll.setWidget(self.lbl_turns)
        scroll.setMaximumHeight(90)
        outer.addWidget(scroll)

        self.refresh()

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
        child.setStyleSheet(child.styleSheet() + " background: transparent; border: none;")
        lay.addWidget(child, 1)
        return box

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _pick_profile(self, key: str):
        k = _kernel()
        if k is not None:
            try:
                k.set_profile(key)
            except Exception:
                pass
        self.refresh()

    def _reset(self):
        k = _kernel()
        if k is not None:
            try:
                k.reset()
            except Exception:
                pass
        self.refresh()

    def _copy(self):
        k = _kernel()
        if k is None:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(k.report_text())
            self.lbl_hint.setText("性能报告已复制到剪贴板。")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def refresh(self):
        c = _theme()
        k = _kernel()
        if k is None:
            self.lbl_hint.setText(
                "性能内核未加载：请在设置中启用本插件（启用后即时热加载，无需重启）。")
            return
        try:
            snap = k.snapshot()
        except Exception as e:
            self.lbl_hint.setText("读取统计失败：%s" % e)
            return

        prof = snap.get("profile") or "balanced"
        for key, b in self._profile_btns:
            on = (key == prof)
            b.setChecked(on)
            b.setStyleSheet(
                "QPushButton { border: 1px solid %s; border-radius: 8px; padding: 3px 10px;"
                " font-size: 12px; color: %s; background: %s; }"
                % (c["accent"] if on else c["border"],
                   c["accent"] if on else c["dim"],
                   c["card"] if on else "transparent"))

        ui, llm, md = snap["ui"], snap["llm"], snap["md"]
        if ui.get("samples"):
            col = c["err"] if ui["p95"] > 66 else (c["warn"] if ui["p95"] > 45 else c["ok"])
            self.tiles["ui"].set("%.0f ms" % ui["p95"],
                                 "P50 %.0fms · 卡顿 %.1f%%（%d 次采样）"
                                 % (ui["p50"], ui["slow_ratio"], ui["samples"]), col)
        else:
            self.tiles["ui"].set("—", "界面心跳未运行（服务未启动）")

        if llm.get("count"):
            col = c["err"] if llm["ttft_p95"] > 3000 else (
                c["warn"] if llm["ttft_p95"] > 1200 else c["ok"])
            self.tiles["ttft"].set("%.2fs" % (llm["ttft_p95"] / 1000.0),
                                   "P50 %.2fs · %d 次请求"
                                   % (llm["ttft_p50"] / 1000.0, llm["count"]), col)
            self.tiles["cps"].set("%.0f 字/秒" % llm["cps"],
                                  "输入均 %s 字 · 输出均 %s 字"
                                  % (format(llm["in_avg"], ","),
                                     format(llm["out_avg"], ",")),
                                  c["ok"] if llm["cps"] >= 15 else c["warn"])
        else:
            self.tiles["ttft"].set("—", "发一轮对话后再看")
            self.tiles["cps"].set("—", "发一轮对话后再看")

        ratio = md.get("hit_ratio", 0)
        self.tiles["md"].set("%.0f%%" % ratio,
                             "省 %s 字符 · %d 段 / %.1fMB"
                             % (format(md.get("saved_chars", 0), ","),
                                md.get("entries", 0), md.get("chars", 0) / 1048576.0),
                             c["ok"] if ratio >= 25 else c["dim"])

        rows = snap.get("tools") or []
        if rows:
            lines = ["<table cellspacing='0' cellpadding='3' width='100%'>",
                     "<tr><td style='color:%s;font-size:11px'>工具</td>"
                     "<td align='right' style='color:%s;font-size:11px'>次数</td>"
                     "<td align='right' style='color:%s;font-size:11px'>P95</td>"
                     "<td align='right' style='color:%s;font-size:11px'>最大</td></tr>"
                     % (c["dim"], c["dim"], c["dim"], c["dim"])]
            for t in rows[:8]:
                p95 = t["p95"]
                col = c["err"] if p95 > 3000 else (c["warn"] if p95 > 800 else c["text"])
                lines.append(
                    "<tr><td style='font-family:Consolas;color:%s'>%s</td>"
                    "<td align='right' style='color:%s'>%d</td>"
                    "<td align='right' style='color:%s'>%.0fms</td>"
                    "<td align='right' style='color:%s'>%.0fms</td></tr>"
                    % (c["text"], _esc(t["name"]), c["dim"], t["n"],
                       col, p95, c["dim"], t["max"]))
            lines.append("</table>")
            self.lbl_tools.setText("".join(lines))
        else:
            self.lbl_tools.setText("<span style='color:%s'>暂无工具调用</span>" % c["dim"])

        try:
            advice = k._suggestions(snap)
        except Exception:
            advice = []
        if advice:
            self.lbl_advice.setText("".join(
                "<p style='margin:0 0 6px 0;line-height:150%%'>· %s</p>" % _esc(a)
                for a in advice))
        else:
            self.lbl_advice.setText("<span style='color:%s'>暂无</span>" % c["dim"])

        turns = snap.get("turns") or []
        if turns:
            self.lbl_turns.setText("　".join(
                "<span style='color:%s'>最近任务 %.1fs</span>"
                "<span style='color:%s'>（%d 工具）</span>"
                % (c["dim"], t["seconds"], c["text"], t["tools"])
                for t in turns[-6:]))
        else:
            self.lbl_turns.setText("<span style='color:%s'>暂无任务记录</span>" % c["dim"])

        self.lbl_hint.setText(
            "档位「%s」：%s　统计为进程内实时数据，%s"
            % (snap.get("profile_label"), k.PROFILES.get(prof, {}).get("desc", ""),
               time.strftime("%H:%M:%S")))

    # 可见才刷新：不可见时零开销（浮层页签会常驻）
    def showEvent(self, e):
        super().showEvent(e)
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._timer is not None:
            self._timer.stop()


def _esc(text: str) -> str:
    """HTML 转义（工具名可能含 & < > 等字符）。"""
    s = str(text or "")
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def show_report():
    """托盘动作：弹窗展示完整性能报告（无需打开面板）。"""
    from PyQt6.QtWidgets import QMessageBox
    k = _kernel()
    text = k.report_text() if k is not None else "性能内核未加载（请在设置中启用本插件，启用后即时热加载，无需重启）。"
    box = QMessageBox()
    box.setWindowTitle("性能报告")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
