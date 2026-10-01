"""回合内区块几何探针：合成一个多段长回合，展开后检查块是否重叠、回合高度是否少算。

守护两条契约（`tests/test_chat_bubble_demo_parity.py` 里亦有回归用例）：
  1. 展开执行过程后，「思考气泡 / 工具调用输出 / 命令输出 / 正文」四类块的几何必须
     严格不重叠 —— 块高曾只钉**下界**（setMinimumHeight），而 QVBoxLayout 会在
     「最小值 < 推荐值 < 最大值」之间做弹性分配，槽位与块高对不上就叠在一起；
  2. 回合高度必须 ≥ 内部布局的真实最小需求，否则内容溢出容器、滚动区里下一个
     气泡会骑到这个回合上。

用法：python scripts/_probe_turn_layout.py
输出各块的 y 区间 / 实际高度 / 最小高度 / heightForWidth，并直接给出「重叠块数」。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget        # noqa: E402

from zhuzhu_Copilot.ui import agent_chat_bubbles as cb                # noqa: E402
from zhuzhu_Copilot.ui import agent_panel as ap                       # noqa: E402

STYLE = cb.ChatStyle(
    card="#1F232C", border="#272C36", border_soft="#333A46", dash="#333A46",
    text="#F3F5F9", text_dim="#9BA3B0", accent="#2F52D8", muted="#9BA3B0",
    icon_shell="#272C36", icon_color="#2F52D8", tag_bg="#2A3040", tag_fg="#9BA3B0",
    user_bg="#2F52D8", user_fg="#FFFFFF", cmd_fg="#F3F5F9", ok_fg="#5B82F6",
    hover="#272C36", panel="#181B21", bg="#101216",
)

LONG_THINK = "先枚举安装产物再按指纹过滤。" * 40
LONG_OUT = "".join(f"第 {i} 行：这是一段较长的输出内容。<br/>" for i in range(40))
LONG_CMD = "dir " + "--include=/very/long/path/segment " * 40
BODY = "最终结论：一切正常。"


def _pump(app, ms=80):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def _blocks():
    out = []
    for rep in range(8):
        out.append((cb.KIND_THINK, {"tag": "PLANNING", "body": f"<div>{LONG_THINK}</div>",
                                    "sid": rep}, (f"th{rep}", 0)))
        out.append((cb.KIND_TOOL, {"name": "scan_drives", "meta": "target=D:/", "params": {},
                                   "out": LONG_OUT, "ico": None, "tip": ""}, (f"tl{rep}", 0)))
        out.append((cb.KIND_CMD, {"label": "run_command", "cmd": LONG_CMD, "out": LONG_OUT},
                    (f"cmd{rep}", 0)))
    out.append((cb.KIND_STREAM, {"html": f"<div>{BODY}</div>"}, ("body", 0)))
    return out


def _report(turn, app):
    items = [(i, r) for i, r in enumerate(turn._items) if not r.widget.isHidden()]
    prev_bottom, prev_kind, bad = None, None, 0
    print("  明细（前 8 块 + 所有异常块）：")
    for n, (i, ref) in enumerate(items):
        g = ref.widget.geometry()
        w = ref.widget
        notes = []
        if prev_bottom is not None and g.top() < prev_bottom:
            notes.append(f"与上一块({prev_kind})重叠 {prev_bottom - g.top()}px")
        if g.height() < w.minimumHeight() - 2:
            notes.append(f"高度被压 {g.height()}<{w.minimumHeight()}")
        if notes:
            bad += 1
        if notes or n < 8:
            print(f"    #{i:3d} {ref.kind:7s} y={g.top():6d}..{g.bottom():6d} "
                  f"h={g.height():5d} min={w.minimumHeight():5d} "
                  f"hfw={w.heightForWidth(turn.width()):5d} "
                  f"sHint={w.sizeHint().height():5d}"
                  + ("  <<< " + "；".join(notes) if notes else ""))
        prev_bottom, prev_kind = g.bottom(), ref.kind
    return bad


def _dump_layout(turn, limit=40):
    lay = turn._box_lay
    print(f"\n  [_box_lay] count={lay.count()} enabled={lay.isEnabled()} "
          f"_box h={turn._box.height()} min={turn._box.minimumHeight()} "
          f"layMin={lay.minimumSize().height()} layHint={lay.sizeHint().height()}")
    for j in range(min(lay.count(), limit)):
        it = lay.itemAt(j)
        w = it.widget()
        sp = it.spacerItem()
        if w is not None:
            g = w.geometry()
            print(f"    {j:3d} W {type(w).__name__:12s} y={g.top():6d}..{g.bottom():6d} "
                  f"h={g.height():5d} hidden={w.isHidden()} min={w.minimumHeight():5d}")
        elif sp is not None:
            print(f"    {j:3d} SPACER h={sp.sizeHint().height()}")


def _dump_cmd(turn, idx):
    """打印某块的内部构成（布局分配高度 vs 子控件需求）"""
    ref = turn._items[idx]
    w = ref.widget
    lay = w.layout()
    print(f"\n  [块 #{idx} {ref.kind}] 宽={w.width()} 高={w.height()} "
          f"hfw={w.heightForWidth(turn.width())} min={w.minimumHeight()} "
          f"布局项={lay.count()} 边距={lay.contentsMargins().top()}/{lay.contentsMargins().bottom()} "
          f"spacing={lay.spacing()} 需求总高={w.sizeHint().height()}")
    for j in range(lay.count()):
        it = lay.itemAt(j)
        cw = it.widget()
        if cw is None:
            continue
        print(f"      项{j} {type(cw).__name__:12s} 高={cw.height():5d} "
              f"sHint={cw.sizeHint().height():5d} min={cw.minimumHeight():5d} "
              f"hidden={cw.isHidden()} textLen="
              f"{len(getattr(cw, 'text', lambda: '')() or '') if hasattr(cw, 'text') else '-'}")


def main():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    host.resize(760, 4000)
    turn = cb.ChatTurn(STYLE, ap._line_icon)
    turn.setMaximumWidth(760)
    lay.addWidget(turn)
    nex = cb.ChatTurn(STYLE, ap._line_icon)      # 下一回合：看它会不会被骑上来
    nex.setMaximumWidth(760)
    nex.render([(cb.KIND_STREAM, {"html": "<div>下一条回复</div>"}, ("n", 0))],
               live=False, done=True)
    lay.addWidget(nex)
    host.show()
    _pump(app, 200)

    turn.render(_blocks(), live=False, done=True)
    _pump(app, 300)
    print(f"[收起态] turnHeight={turn.height()} min={turn.minimumHeight()} "
          f"hfw={turn.heightForWidth(760)} wrap={turn.parentWidget().height() if turn.parentWidget() else None}")
    print(f"  下一回合 top={nex.geometry().top()}（上一边界 {turn.geometry().bottom()}）")

    turn._toggle.click()                     # 展开过程区
    _pump(app, 200)
    while getattr(turn, "_reveal_pending", None):
        _pump(app, 60)
    # 只让出第一页（保持「继续显示」存在），复现用户点开后的初始状态
    print(f"\n[展开一页后] _more hidden={turn._more.isHidden()} "
          f"_reveal_rest={len(turn._reveal_rest or [])}")
    print(f"  turnHeight={turn.height()} min={turn.minimumHeight()} hfw={turn.heightForWidth(760)}")
    box_need = turn._box.sizeHint().height()
    print(f"  _box.sizeHint 高={box_need}  outer_pad={turn._outer_pad()} "
          f"box_lay_margins={turn._box_lay.contentsMargins().top()}/"
          f"{turn._box_lay.contentsMargins().bottom()} "
          f"toggle_h={turn._toggle.sizeHint().height()} more_h={turn._more.sizeHint().height()}")
    turn._box_lay.invalidate()
    print(f"  invalidate+activate -> {turn._box_lay.activate()}   "
          f"_box h={turn._box.height()}")
    _pump(app, 120)
    print(f"  预期 minimumHeight ≈ {turn._outer_pad() + turn._box_lay.contentsMargins().top() + turn._box_lay.contentsMargins().bottom() + box_need}")
    found = _report(turn, app)
    print(f"  回合内重叠块数：{found}")
    _dump_cmd(turn, 2)
    _dump_cmd(turn, 0)
    _dump_cmd(turn, 1)
    _dump_layout(turn, 60)
    print(f"  下一回合 top={nex.geometry().top()}（上一回合 bottom={turn.geometry().bottom()}）"
          + ("  <<< 下一回合被骑上" if nex.geometry().top() < turn.geometry().bottom()
             else ""))

    # 全部显示后再看一次
    while getattr(turn, "_reveal_rest", None):
        turn._reveal_page()
        _pump(app, 120)
    while getattr(turn, "_reveal_pending", None):
        _pump(app, 60)
    turn.relayout_heights(turn.width())
    _pump(app, 200)
    print(f"\n[全部显示后] _more hidden={turn._more.isHidden()} "
          f"min={turn.minimumHeight()} hfw={turn.heightForWidth(760)}")
    found = _report(turn, app)
    print(f"  回合内重叠块数：{found}")
    print(f"  下一回合 top={nex.geometry().top()}（上一回合 bottom={turn.geometry().bottom()}）"
          + ("  <<< 下一回合被骑上" if nex.geometry().top() < turn.geometry().bottom()
             else ""))


if __name__ == "__main__":
    main()
