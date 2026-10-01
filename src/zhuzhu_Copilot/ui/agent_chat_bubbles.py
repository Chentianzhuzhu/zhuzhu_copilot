# -*- coding: utf-8 -*-
"""AI 事件流气泡组件（1:1 复刻 ui_style_demo/index.html）。

设计来源：`ui_style_demo/index.html` 的「AI 事件流」结构。与旧实现（单个填充色
气泡 + 内部富文本）的关键差异：

- AI 回合**没有填充气泡**：改为上下 1px 虚线分区 + 左侧竖向虚线（demo `.ai-turn`），
  左上角骑在虚线上的「耗时徽章」（demo `.cost-ribbon`）。
- 思考过程是**独立气泡**（demo `.think-bubble`：卡片底 + 1px 边框 + 非对称圆角
  6/16/16/16 + 图标壳 + tag 胶囊），正文超 THINK_FOLD_LINES 行自动折叠渐隐。
- 工具调用是**无气泡行内条目**（demo `.tool-call`：图标壳 + 工具名 + meta + 参数 chip）。
- 执行命令是**独立命令块**（demo `.cmd`：圆角 10 边框 + 标题栏三点 + `$ 命令` + 输出行）。
- 正文回复是**纯文本**（demo `.stream`），永不参与折叠。
- AI 完成汇报后**过程区整体收起**，只留正文与「查看执行过程」按钮（demo `.ai-proc`）。
- 用户消息气泡为非对称圆角（demo `.msg`：18/18/6/18）。

本模块只负责「外壳与排版」，不负责内容语义：各段内层 HTML 由调用方
（agent_panel）逐段渲染后经 `render()` 传入，因此既有的 markdown 增量渲染、段级
内容签名缓存、链接识别、长文本截断能力全部复用。颜色/字体由 `ChatStyle` 注入，
几何常量取自 `ui.tokens`，本模块内不出现主题色字面量。

性能约定：`render()` 按段内容签名增量更新 —— 签名未变的段不重建控件、不触发重排，
因此流式输出（每 60ms 一次刷新）实际只更新正在增长的那一个标签。
"""
from __future__ import annotations
from zhuzhu_Copilot import app_identity

import dataclasses
import datetime
import os
import re
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QRegion,
    QTransform,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from zhuzhu_Copilot.ui.tokens import (
    BUBBLE_RADIUS_THINK_TAIL,
    BUBBLE_RADIUS_USER,
    BUBBLE_RADIUS_USER_TAIL,
    DASH_PATTERN,
    FONT_BASE,
    FONT_BODY,
    FONT_CAPTION,
    FONT_SMALL,
    RADIUS_CHIP,
    RADIUS_CMD,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    RADIUS_TILE,
    SPACING_XS,
    OUT_FOLD_LINES,
    THINK_FOLD_LINES,
)

# ---------------------------------------------------------------------------
# demo 几何：数值 1:1 取自 ui_style_demo/index.html（颜色不在此处，由 ChatStyle 注入）
# ---------------------------------------------------------------------------
BUBBLE_PAD_V = 13              # .msg padding: 13px 18px
BUBBLE_PAD_H = 18
# 回合内/回合间留白：demo 原值 18/6/30，实测观感过于松散（每段文字上下都有大片空白），
# 按用户反馈收紧到 12/6/18；虚线分区与「耗时徽章骑线」的结构不变。
AI_TURN_PAD_TOP = 8            # demo 18 → 8（收紧）
AI_TURN_PAD_BOTTOM = 3         # demo 6  → 3
AI_TURN_GAP = 10               # demo 30 → 10（回合间距；原值在紧凑界面里像大片空白）
RIBBON_H = 21                  # .cost-ribbon 实测高度（上下 padding 4 + 行高 12 + 边框）
LEFT_GUTTER = 14               # .vline left:-14px 的等效留白（竖虚线到内容左缘）
THINK_PAD_V = 11               # .think-bubble padding: 13px 15px → 11px 15px（收紧）
THINK_PAD_H = 15
THINK_HEAD_GAP = 8             # .tb-head margin-bottom
TOOL_ICON = 30                 # .tc-icon 30x30
TOOL_GLYPH_RATIO = 0.66        # 工具图标绘制比例（族底图 + 动作角标要在壳内看清）
TOOL_OUT_GAP = 9               # 工具行与其输出之间的间距（输出紧贴该工具行下方）
OUT_LINE_W = 2                 # 输出区左侧竖线宽（同系列工具的多次输出按同一视觉挂载）
OUT_LINE_GAP = 9               # 竖线到输出文字的间距
# 折叠态只铺进标签的输出前缀长度（字符）：与思考气泡同一手法（见 THINK_PREVIEW_CHARS）——
# 全文入标签会让每次高度测量都重排整篇富文本，长输出下成本与篇幅成正比。工具输出不会
# 像思考那样逐 tick 增长，故预算可以给得更宽，保证「任意窗口宽度下前缀都比折叠上限高」。
OUT_PREVIEW_CHARS = 2400
# 输出保留上限（字符）：**不是展示上限**（展示由折叠负责，点「展开全部」可见全文），
# 仅防止单条病态输出把内存/会话文件撑爆（run_command 默认不限长）。超出的部分只回给模型。
OUT_KEEP_CHARS = 200000
THINK_ICON = 28                # .tb-head .icon 28x28
# 折叠态只渲染的思考正文前缀长度（字符）：折叠态可见区就是前 5 行，全文入标签会让
# **每个流式 tick 都重排一次整篇富文本**（长推理下成本随篇幅线性增长 → O(n²)），
# 主线程被排满后气泡与打字指示器就会剧烈抖动。只铺前缀即把每 tick 成本压成常数，
# 展开时（`_fold_open is True`）再铺全文 —— 那时才是真的需要全部内容。
# 取值远大于「任意气泡宽度下 5 行」的字数，保证前缀本身仍被判为可折叠（按钮不消失）。
THINK_PREVIEW_CHARS = 1200
# 折叠开关（PillButton）与正文之间的间距（px）：单独给一份，避免与各块内部的其它间距耦合
FOLD_GAP = 8
FOLD_CLOSED_TEXT = "收起"      # 展开态下的开关文案（点它收起回去）
THINK_FOLD_TEXT = "继续查看"   # 思考气泡折叠态文案（与输出的措辞区分）
OUT_FOLD_TEXT = "展开全部"     # 工具/命令输出折叠态文案
CMD_PAD_V = 7                  # .cmd .bar padding: 7px 12px
CMD_PAD_H = 13                 # .cmd .in / .out 左右内边距
DOT_D = 8                      # .cmd .bar .dotbtn 直径
DOT_GAP = 5
DOTS_TICK_MS = 260             # 思考胶囊三点动画节拍
RIBBON_TICK_MS = 200           # 耗时徽章实时刷新节拍
RIBBON_UNKNOWN_TEXT = "--"     # 徽章常驻但拿不到耗时时的占位文案（老会话）
FADE_H = 22                    # 折叠遮罩渐变高度（约 1.5 行）

# ---------------------------------------------------------------------------
# 流式落字「渐变模糊浮现」动效参数（demo 无此动效，为流式输出体验新增）
# ---------------------------------------------------------------------------
# 语义：区块每落下一批新字（思考正文 / 工具行 / 命令输出 / 回复正文都一样），新字先以
# 「模糊虚影 + 半透明」浮现，随后在 EMERGE_MS 内逐渐清晰并就位；同一列不同高度按**行龄**
# 连续渐变（越靠下越新越模糊），于是肉眼看到的是连续推进的清晰波前，而不是一跳一跳地
# 落字 —— 上游 token 成批到达也不会跳变。
EMERGE_MS = 760                # 单行像素从浮现到清晰的总时长（越长越像「浮现」而非「闪现」；
                               # 0.46s 时新字几乎是「一下就清楚了」，用户反馈「速度太快」）
EMERGE_TICK_MS = 8             # 动效节拍（≈125fps）：只决定「多久催一次重绘」，真正上屏受屏幕
                               # 刷新率约束，故不再往下压 —— 再快只是白烧主线程
EMERGE_SRC_MS = 8              # 底图/模糊层重建的最小间隔。**这是丝滑感的关键**：QWidget 每屏
                               # 帧最多重绘一次，所以「源图新鲜度」必须不低于刷新率。原先 24ms
                               # 只给 40fps，模糊层天天吃旧图，新字在里面一格一格跳进去 ——
                               # 观感就是卡顿。取 8ms（=动效节拍）后每屏帧（含 120Hz 高刷）
                               # 都拿到当前文本，重建只重渲当前这一块，成本可控
EMERGE_SAMPLE_MS = 8           # 行龄采样最小间隔（把高频落字合并成有界的曲线采样）。
                               # 同样压到刷新率之上，波前推进才会逐屏帧连续
EMERGE_RISE_PX = 4             # 浮现时虚影的上浮位移（由下而上「浮现」）
EMERGE_MAX_H = 260             # 浮现层高度上限（一次涌入超长正文时只对尾部做动效）
EMERGE_MIN_LINES = 2.2         # 浮现层最小高度（行）：保证波段上缘落在上一行清晰区内
EMERGE_FEATHER = (16, 8, 4)    # 上 / 左右 / 下 四边羽化高度（px）
EMERGE_TOP_RAMP = 20           # 波段上缘强制收敛到全清晰的过渡高度（羽化无痕的前提）
EMERGE_BLUR = ((6, 0.42), (3, 0.22))   # (降采样倍数, 虚影权重)：粗 / 细两级虚影
EMERGE_SHARP_MIN = 0.30        # 刚落下时的清晰层不透明度下限（新字仍可读，不至于糊成一片）
EMERGE_SAMPLES = 18            # 清晰度曲线采样点数（渐变停靠点数量）
# 「书写行」软化：正文高度不变时（行内继续落字，中文流式最常见的形态）行是不会变新的，
# 此时按行龄无法区分新旧字 —— 改为把**笔尖所在的一行**整体压软，落字越密越软、
# 停顿 EMERGE_MS 后回升到全清晰。保证「行内落字」同样有浮现感，且不会永久发虚。
EMERGE_WRITING_LINES = 1.2     # 软化范围（行）：覆盖笔尖所在行 + 与上一行的过渡
EMERGE_WRITING_DROP = 0.50     # 书写期间该区域清晰度的最大降幅

# 高度「只增不减」棘轮的放行阈值（px）：流式期间 markdown 被半解析（代码围栏还没闭合等）
# 会让行数瞬间变化，小幅（≤ 该值）收缩按抖动处理以保持几何稳定；而**真实收缩**（工具块被
# 移除、围栏闭合、过程区收起、折叠态切换）一次就掉很多行，必须放行 —— 否则那个偏大的高度
# 会被永久钉住，多余空间被布局摊到区块里，表现为气泡上下出现大片空白且整个任务期间不自愈。
HEIGHT_SHRINK_TOL = 24

# 段类型（调用方通过 render() 的 kind 字段指定）
KIND_THINK = "think"
KIND_TOOL = "tool"
KIND_CMD = "cmd"
KIND_RICH = "rich"
KIND_STREAM = "stream"

# 各段与下一段之间的间距（demo 中 .think-bubble/.cmd 的 margin-bottom、.tool-call 的 padding）
# 块间距：demo 的 .think-bubble/.cmd margin-bottom 为 14、.stream 约 16，统一收紧到 8
BLOCK_GAP = {KIND_THINK: 8, KIND_TOOL: 0, KIND_CMD: 8, KIND_RICH: SPACING_XS,
             KIND_STREAM: 8}

# 归入「过程区」的段类型（AI 完成汇报后整体收起，只留正文）
PROC_KINDS = frozenset({KIND_THINK, KIND_TOOL, KIND_CMD, KIND_RICH})

TOGGLE_OPEN_TEXT = "收起执行过程"
TOGGLE_CLOSED_TEXT = "查看执行过程"


@dataclasses.dataclass(frozen=True)
class ChatStyle:
    """一次渲染所需的样式快照：颜色取当前主题色板，字体族取平台字体栈。

    字段与 demo 的 CSS 变量一一对应（映射写在行尾注释），由面板 `_chat_style()`
    生成；主题切换后重新生成即整体换肤 —— 组件内不出现任何颜色字面量。
    """

    card: str          # --card        气泡/卡片底
    border: str        # --card-border 卡片描边
    border_soft: str   # --line        分隔线
    dash: str          # --dash        虚线
    text: str          # --title-fg    标题字色
    text_dim: str      # --body-fg     正文/次级字色
    accent: str        # --accent      强调色（图标/按钮/命令提示符）
    muted: str         # --muted       更弱的辅助字色
    icon_shell: str    # --icon-shell  图标壳底
    icon_color: str    # --icon-color  图标线条色
    tag_bg: str        # --tag-bg      胶囊底
    tag_fg: str        # --tag-fg      胶囊字色
    user_bg: str       # --user-bg     用户气泡底
    user_fg: str       # --user-fg     用户气泡字色
    cmd_fg: str        # --cmd-fg      命令字色
    ok_fg: str         # --ok-fg       命令输出字色（demo 用蓝，不用绿）
    hover: str         # 悬停底色（面板 HOVER）
    panel: str         # 代码块内底（面板 PANEL）
    bg: str            # --bg  面底色：正文直接坐落其上，落字浮现层据此遮底（面板 BG）
    # ---- 丰富的层次色（在「纯黑/淡灰/白/深蓝」四色系内做深浅展开，全部由色板派生）----
    # 缺省（空串）时回退到对应基础色：老构造点（测试/历史主题包）只给基础字段也不会缺色。
    think_bg: str = ""      # 思考气泡底（深蓝微调，与正文面底区分开）
    think_border: str = ""  # 思考气泡描边（深蓝微调）
    tag_plan_bg: str = ""   # PLANNING 阶段胶囊底
    tag_exec_bg: str = ""   # EXEC 阶段胶囊底（与 PLANNING 区分）
    tool_shell: str = ""    # 工具行图标壳底（深蓝微调）
    # 落字浮现的遮罩色：**必须不透明**（要盖住旧字）且贴近真实背景；留空则回退 bg
    band: str = ""
    out_fg: str = ""        # 工具输出字色（淡蓝）
    out_line: str = ""      # 工具输出区左侧竖线色
    font_ui: str = '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    font_mono: str = 'Consolas, "Cascadia Code", "Courier New", monospace'

    # ---- 派生色解析（空串 = 未派生 → 回退基础色）----
    def think_bg_of(self) -> str:
        return self.think_bg or self.card

    def think_border_of(self) -> str:
        return self.think_border or self.border

    def tag_bg_of(self, tag: str) -> str:
        """思考胶囊底色按阶段取色：EXEC 用深蓝调，PLANNING 用中性调"""
        if tag == "EXEC":
            return self.tag_exec_bg or self.tag_bg
        return self.tag_plan_bg or self.tag_bg

    def tool_shell_of(self) -> str:
        return self.tool_shell or self.icon_shell

    def out_fg_of(self) -> str:
        return self.out_fg or self.ok_fg

    def out_line_of(self) -> str:
        return self.out_line or self.accent


IconProvider = Callable[[str, int, str], QIcon]


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _dashed_pen(color: str) -> QPen:
    """虚线画笔：实 4 / 空 4，与 demo 的 dashed / linear-gradient 节奏一致。"""
    pen = QPen(QColor(color), 1, Qt.PenStyle.CustomDashLine)
    pen.setDashPattern(list(DASH_PATTERN))
    return pen


def rotate_icon(icon: QIcon, degrees: int, size: int) -> QIcon:
    """按角度旋转矢量图标（demo 用 CSS transform 旋转展开/收起指示符）。"""
    pm = icon.pixmap(size, size)
    if pm.isNull():
        return icon
    return QIcon(pm.transformed(QTransform().rotate(degrees),
                                Qt.TransformationMode.SmoothTransformation))


def fmt_seconds(seconds: float) -> str:
    """耗时文案：不足 1 分钟用 `12.4s`（与 demo 一致），超过用 `2m03s` 防长数字。"""
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def cost_from_meta(meta: str) -> Optional[float]:
    """系统时间行「HH:MM:SS → HH:MM:SS」→ 秒数（老会话没有 cost 时的兜底）。

    时间行只有时刻、没有日期，故结束早于开始时按**跨零点**处理（+24h）。整行解析
    失败一律返回 None —— 宁可让徽章显示占位符，也不显示一个错误时长。
    """
    txt = str(meta or "")
    if "\u2192" not in txt:
        return None
    try:
        a, b = (x.strip() for x in txt.split("\u2192", 1))
        t1 = datetime.datetime.strptime(a, "%H:%M:%S").time()
        t2 = datetime.datetime.strptime(b, "%H:%M:%S").time()
    except (ValueError, TypeError):
        return None
    delta = ((t2.hour - t1.hour) * 3600 + (t2.minute - t1.minute) * 60
             + (t2.second - t1.second))
    if delta < 0:
        delta += 86400
    return float(delta)


# 几何自诊断开关（默认关闭，零开销）：排查「生成中闪烁 / 残留空白」这类只有真实
# 窗口才能复现的问题时，设 WINAPP_CHAT_DEBUG=1 启动，把每次渲染的宽度/高度/写回
# 情况追加到 ~/.zhuzhu_Copilot/chat_debug.log，据此判断是「高度反复写回」还是
# 「高度被布局改小」。
_CHAT_DEBUG = os.environ.get("WINAPP_CHAT_DEBUG", "").strip() == "1"


def _dbg(msg: str) -> None:
    if not _CHAT_DEBUG:
        return
    try:
        path = app_identity.data_root() / "chat_debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


# 高度写回的最小变化量（px）：富文本换行取整会让 heightForWidth 在 ±1px 抖动，
# 若每 tick 都按抖动值写回高度，整块区域会随之反复位移 1px —— 生成过程中看起来
# 就是持续闪烁/抖动。要求变化达到该阈值才写回，几何自然稳定。
_H_DELTA_EPS = 2


def _need_resize(cur: int, want: int) -> bool:
    """高度是否需要写回：变化量达到容差阈值才写（消除 ±1px 抖动引发的重排）"""
    return want > 0 and abs(int(want) - int(cur)) >= _H_DELTA_EPS


def _label_hfw(lbl: QLabel, width: int, ver: int) -> int:
    """按 (内容版本, 宽度) 缓存**单个标签**的高度测量结果。

    QLabel.heightForWidth 会为整篇富文本做一次完整布局；流式刷新（60fps）会反复
    问同一批标签的高度，缓存后可把开销压到只剩「正在增长的那一个标签」。
    缓存挂在标签上而不是块上：一个块可能有多行文本（命令块 = 命令 + 输出），
    共用块级缓存会把两行的高度串味。

    **关键：测量前必须临时解除 minimumHeight 钳制。**
    QLabel.heightForWidth(w) 实际返回 max(文档在 w 下的真实高度, minimumHeight())。
    新块在布局给宽前以默认窄宽度（~100px）首测并钉入了偏大的最小高度，之后即使
    拿到真实宽度，每次测量都被这个旧钉值钳制 → 块/回合高度永远偏大，表现为正文
    上下（尤其下方）大片空白。测量前置零、测完恢复，才能拿到与宽度一致的真实高度。
    """
    key = (ver, int(width))
    if getattr(lbl, "_hfwk", None) == key:
        return lbl._hfqv
    saved = int(lbl.minimumHeight())
    if saved:
        lbl.setMinimumHeight(0)
    val = int(lbl.heightForWidth(width) or 0)
    if saved:
        lbl.setMinimumHeight(saved)
    lbl._hfwk, lbl._hfqv = key, val
    return val


def safe_prefix(html: str, budget: int) -> str:
    """按字符预算取 HTML 前缀，且保证**不切在标签中间**。

    ① 优先停在完整的 `<br/>` 边界：工具/命令输出的换行结构都由它承载，按行切最整齐
       （搜索范围放宽一个 `<br/>`，让「跨过预算一点但完整」的换行也能被选中）；
    ② 没有换行边界（单行超长输出）时退化为字符前缀，只额外保证切点不落在某个未闭合的
       标签内部 —— 切在标签中间会让 `<b` 这类残片直接显示成文字。
    """
    if len(html) <= budget:
        return html
    cut = html.rfind("<br/>", 0, int(budget) + len("<br/>"))
    if cut > 0:
        return html[:cut]
    cut = int(budget)
    lt = html.rfind("<", 0, cut)
    if lt > html.rfind(">", 0, cut):
        cut = max(0, lt)          # 切点落在未闭合的标签内：回退到该标签之前
    return html[:cut]


# ---------------------------------------------------------------------------
# 「是否可折叠」的廉价估算（见 `_FoldMixin._fold_full_h`）
# ---------------------------------------------------------------------------
# 折叠判定必须基于**全文**（前缀在超宽窗口下可能不到折叠上限，按前缀判定会让开关
# 反复闪烁），但全文测量很贵：QLabel.heightForWidth 会为整篇富文本做一次完整布局，
# 2 万字符的输出单块就要数十毫秒，长回合里上百块累积成秒级主线程阻塞 —— 这正是
# 「应用启动加载长对话后点开执行过程直接无响应」的大头。故先用 O(1) 估算给出明确
# 结论，只有量级贴着阈值时才退回真实测量（此时内容本就不长，成本有界）。
_EST_SAMPLE_CHARS = 1500       # 估算采样长度（字符）：足以代表正文的平均字宽
_EST_MARGIN_LINES = 2          # 估算与阈值的「边界地带」（行）：落在这里才真测
_FOLD_TAG_RE = re.compile(r"<[^>]*>")
_FOLD_BREAK_RE = re.compile(r"<br\s*/?>|</div>|</p>|</li>", re.I)


def _fold_line_floor(html: str) -> int:
    """HTML 的**显式换行数下限**（块级分隔标签数 + 1）"""
    return len(_FOLD_BREAK_RE.findall(html or "")) + 1


def _fold_est_lines(html: str, width: int, fm: QFontMetrics) -> int:
    """估算 HTML 在给定宽度下的行数（不排版，O(1)）。

    做法：去掉标签后采样前 `_EST_SAMPLE_CHARS` 个字符，算平均像素宽，再按可见字符
    总数外推自动折行数，叠加显式换行数。误差约 ±10%，只用于阈值判定、不参与几何
    写回，因此不需要与 QLabel 的排版逐像素一致。
    """
    width = max(1, int(width))
    floor = _fold_line_floor(html)
    plain = _FOLD_TAG_RE.sub("", html or "")
    n = len(plain)
    if n <= 0:
        return floor
    sample = plain[:_EST_SAMPLE_CHARS]
    try:
        px = float(fm.horizontalAdvance(sample) or 0)
    except Exception:
        px = 0.0
    if px > 0:
        avg = px / max(1, len(sample))
    else:
        # 字宽取不到（空样本 / 异常）：退化为字阶粗估（CJK 约 1.6 倍字号宽）
        avg = max(1.0, float(fm.averageCharWidth() or FONT_BODY))
    return int(floor + (n * avg) / width)


def cap_output(text: str) -> tuple:
    """输出的**保留**上限，返回 (保留文本, 提示语)；展示折叠不在这里（见 `_FoldMixin`）。

    只防病态长度（`run_command` 默认不限长）把内存与会话文件撑爆，不承担"让界面好看"
    的职责 —— 界面由折叠负责，点「展开全部」即可看到这里保留的全部内容。上限见
    `OUT_KEEP_CHARS`；提示语说明完整内容已返回模型，避免被误读成内容丢失。
    """
    src = str(text or "")
    if len(src) <= OUT_KEEP_CHARS:
        return src, ""
    return src[:OUT_KEEP_CHARS], f"（超出 {OUT_KEEP_CHARS} 字的部分未在界面保留，完整内容已返回模型）"


def _widget_hfw(wid: QWidget, width: int) -> int:
    """控件在给定宽度下的真实高度：优先 heightForWidth（换行标签），否则用 sizeHint。"""
    try:
        h = wid.heightForWidth(max(1, int(width)))
        if isinstance(h, int) and h > 0:
            return h
    except Exception:
        pass
    return int(wid.sizeHint().height())


# ---------------------------------------------------------------------------
# 历史回合「隐藏过程块收尾」的空闲分摊
# ---------------------------------------------------------------------------
# 背景（探针实测）：内层输出标签首次 setVisible 时，Qt 会为整篇富文本做一次排版
# （2000 字符 ≈ 20ms、2 万字符级可达百毫秒），长对话里上百个过程块累积成秒级
# 主线程阻塞（切换会话时表现为白屏）。历史回合的过程块默认收起（隐藏），其排版
# 结果用户当下看不到 —— 故把「让内层控件生效」的收尾动作推迟到事件循环空闲里
# 分片执行（每片 ≤ _IDLE_SLICE_MS），切换本身只做「建块 + 填内容」（毫秒级）；
# 某回合在铺完之前被展开时，ChatTurn._resume_deferred() 会先同步补齐，正确性不变。
_IDLE_SLICE_MS = 6.0

# 展开过程区的分页参数（见 ChatTurn._set_proc_visible）：
# 实测（scripts/_probe_switch_perf.py，192 个过程块的长回合，~78 万字）：逐块 setVisible
# 显示时 Qt 会重排整条回合（每次约 18 次 resizeEvent + 全量子块测量），单块成本随
# **已显示块数**增长 —— 一次性展开 192 块阻塞主线程 49 秒（表现为「直接无响应」），
# 即便分片也只把 49s 摊成多次数秒卡顿。故展开按页给出：一页 `_PROC_PAGE` 块，
# 其余留给「继续显示」按钮；每页内再按 `_REVEAL_CHUNK` 分片，任何一次占用都有界。
_PROC_PAGE = 12          # 一页显示的过程块数
_REVEAL_CHUNK = 2        # 页内每次补显的块数（首次同步显示，其余进空闲切片）

# 批量切换过程块可见性时是否暂停本回合布局。
# 收益：单个 setVisible 会让父布局立即重排一次（隔离实测 13~46 ms/次，随回合内子项数
# 增长），暂停后一批只付一次重排。正确性由「块用固定高度钉死」（见 `_measure_block`）
# 保证：槽位与块高恒等，禁用期间也不会留下错位。
_PAUSE_LAYOUT_ON_REVEAL = True


class _IdleSpreader(QObject):
    """空闲切片执行器：把推迟的收尾动作分摊到事件循环空闲里逐个执行。

    调用方只 push 一个无参可调用对象；控件已销毁时其内部抛出的 RuntimeError 被吞掉
    （回合被移除/重建后残留的任务直接跳过，不影响其余任务）。"""

    def __init__(self):
        super().__init__()
        self._queue: list = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

    def push(self, fn: callable):
        self._queue.append(fn)
        if not self._timer.isActive():
            self._timer.start(0)

    def drop_pending(self):
        """丢弃尚未执行的收尾任务（用户主动交互时立刻让路）。

        长会话加载时这里会堆积上百个「隐藏过程块收尾」，每个首次收尾都要为富文本排版
        （数十毫秒），全部跑完是数秒 —— 用户在这期间点开执行过程，就是在和这条队列抢
        主线程，点击响应因此忽快忽慢。被丢弃的任务对应的控件全都处于**隐藏**状态
        （历史回合收起态），其延迟标志仍留在控件上：将来真正展开时
        `ChatTurn._apply_proc_visible` 会逐块同步补齐（幂等），正确性不受影响。
        """
        self._queue.clear()

    def _tick(self):
        budget = time.perf_counter() + _IDLE_SLICE_MS / 1000.0
        while self._queue:
            fn = self._queue.pop(0)
            try:
                fn()
            except RuntimeError:
                pass                     # 控件已销毁（回合被清空/重建）：跳过
            if time.perf_counter() >= budget:
                break
        if self._queue:
            self._timer.start(0)


_SPREADER: Optional[_IdleSpreader] = None


def _spreader() -> _IdleSpreader:
    """惰性单例：首次需要时创建（此时 QApplication 必然已存在）"""
    global _SPREADER
    if _SPREADER is None:
        _SPREADER = _IdleSpreader()
    return _SPREADER


class PillButton(QPushButton):
    """胶囊按钮（demo .fold-btn 虚线描边 / .proc-btn 实线描边）。"""

    def __init__(self, style: ChatStyle, text: str, icon: QIcon = None,
                 dashed: bool = False, parent: QWidget = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        border_style = "dashed" if dashed else "solid"
        border_color = style.dash if dashed else style.border
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {style.accent};"
            f" font-family: {style.font_ui}; font-size: {FONT_SMALL}px; font-weight: 600;"
            f" border: 1px {border_style} {border_color};"
            f" border-radius: {RADIUS_PILL}px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ background: {style.hover}; border-style: solid; }}")
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(FONT_SMALL, FONT_SMALL))


class DotsLabel(QWidget):
    """思考胶囊里的三点（demo .dots 依次弹起）。仅在回合进行中滚动，完成后静止，
    避免历史长对话里几十个定时器空转。"""

    def __init__(self, color: str, parent: QWidget = None):
        super().__init__(parent)
        self._color = color
        self._phase = 0
        self._live = False
        self._d = max(3.0, FONT_CAPTION / 2.6)
        self.setFixedSize(int(self._d * 3 + 6), int(self._d))
        self._timer = QTimer(self)
        self._timer.setInterval(DOTS_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_live(self, live: bool):
        live = bool(live)
        if live == self._live:
            return
        self._live = live
        self._phase = 0
        if live and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 1) % 3
        self.update()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self._live:
            self._timer.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        base = QColor(self._color)
        slot = self.width() / 3.0
        for i in range(3):
            c = QColor(base)
            active = self._live and i == self._phase
            c.setAlpha(255 if active else 110)
            p.setBrush(c)
            x = slot * i + (slot - self._d) / 2
            y = (self.height() - self._d) / 2 - (self._d * 0.45 if active else 0.0)
            p.drawEllipse(QRect(int(x), int(y), int(self._d), int(self._d)))
        p.end()


class FlowLayout(QLayout):
    """按行流式排列并自动换行（demo `.kv { display:flex; flex-wrap:wrap }`）。

    Qt 无内置流式布局；工具参数 chip 个数不确定，不换行会撑破面板宽度。

    性能：heightForWidth 按宽度缓存测量结果。展开执行过程时外层布局会反复询问
    同一宽度（QVBoxLayout 多轮试探），无缓存时 15 个 FlowLayout 各被问 60+ 次，
    累计近 1000 次 _measure（每次遍历所有 chip 调 sizeHint）。缓存后同宽度
    直接返回，展开耗时显著下降。
    """

    def __init__(self, parent: QWidget = None, h_gap: int = SPACING_XS,
                 v_gap: int = SPACING_XS):
        super().__init__(parent)
        self._items: list = []
        self._h_gap = h_gap
        self._v_gap = v_gap
        self._hfw_cache: Optional[tuple] = None   # (width, height)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)
        self._hfw_cache = None

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        self._hfw_cache = None
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        w = max(1, int(width))
        if self._hfw_cache is not None and self._hfw_cache[0] == w:
            return self._hfw_cache[1]
        h = self._measure(w)
        self._hfw_cache = (w, h)
        return h

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect)
        self._hfw_cache = None

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _visible_items(self) -> list:
        out = []
        for item in self._items:
            wid = item.widget()
            if wid is not None and not wid.isHidden():
                out.append(item)
        return out

    def _measure(self, width: int) -> int:
        x, y, line_h = 0, 0, 0
        for item in self._visible_items():
            nxt = item.sizeHint()
            if x + nxt.width() > width and line_h > 0:
                x = 0
                y += line_h + self._v_gap
                line_h = 0
            x += nxt.width() + self._h_gap
            line_h = max(line_h, nxt.height())
        return y + line_h

    def _do_layout(self, rect: QRect):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._visible_items():
            nxt = item.sizeHint()
            if x + nxt.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._v_gap
                line_h = 0
            item.setGeometry(QRect(QPoint(x, y), nxt))
            x += nxt.width() + self._h_gap
            line_h = max(line_h, nxt.height())


def _mk_label(style: ChatStyle, wrap: bool = True) -> QLabel:
    """气泡内文本标签的公共配置（保持一致的选择行为与字体族）。"""
    lbl = QLabel()
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse |
        Qt.TextInteractionFlag.LinksAccessibleByMouse)
    lbl.setOpenExternalLinks(False)
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    # 一律左上对齐：QLabel 默认是 AlignVCenter，一旦布局给了多余高度，文字会被
    # 垂直居中 → 上下各留一大片空白（用户反馈的「每段文字上下都有很大空白」根因之一）
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    lbl.setStyleSheet(f"background: transparent; font-family: {style.font_ui};")
    return lbl


def _tile_pixmap(icon: QIcon, size: int, ratio: float = 0.55) -> QPixmap:
    """图标壳内的实际绘制尺寸：组合类图标（工具专属图标）用更大比例才看得清"""
    glyph = max(12, int(size * ratio))
    return icon.pixmap(glyph, glyph)


def _tile(style: ChatStyle, icon: QIcon, size: int, radius: int,
          ratio: float = 0.55, bg: str = None) -> QLabel:
    """图标壳（demo .tc-icon / .tb-head .icon）：壳底 + 内描边 + 居中线条矢量图标。
    bg 可覆盖壳底色（工具调用行用深蓝微调的 tool_shell，与思考/正文的壳区分层次）。"""
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setPixmap(_tile_pixmap(icon, size, ratio))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background: {bg or style.icon_shell}; border: 1px solid {style.border};"
        f" border-radius: {radius}px;")
    return lbl


class _FadeMask(QWidget):
    """折叠遮罩：由透明渐变到气泡底色，模拟 demo 的 mask-image 渐隐。"""

    def __init__(self, color: str, parent: QWidget = None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedHeight(FADE_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        top = QColor(self._color)
        top.setAlpha(0)
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, self._color)
        p.fillRect(self.rect(), g)
        p.end()


class _EmergeBand(QWidget):
    """流式落字「渐变模糊浮现」层：贴在区块正文底部，随落字推进清晰波前。

    原理：波段内**每一行像素都记得自己是何时落下的**，清晰度按行龄自下而上连续渐变 ——
    越靠下（刚落下的行）越模糊、越靠上（早落下的行）越清晰。因此看到的是连续推进的
    清晰波前，而不是一跳一跳地落字；上游 token 成批到达时也只是波前快一点。

    区块只需要给两样东西：
      · `area`：要浮现的正文区域（区块坐标系，**底边 = 正文底边**）——思考正文、工具行
        文字、命令输出、回复正文各自给出即可，同一套动效覆盖所有输出形态；
      · `fill`：正文坐落其上的那层面底色（透明区块取面板底色、卡片取卡片底色）。
    底图按该区域**渲染整个区块（含子控件、排除本层自身）**，因此卡片圆角/边框/图标壳
    都原样保留，只有文字参与浮现。

    与上下文无缝衔接（不出现矩形边界）的三条约定：
      1. 底图 = 区块同区域的原样渲染（同一套排版引擎，几何逐像素对齐）；
      2. 波段上缘另叠一段强制收敛到全清晰的过渡（EMERGE_TOP_RAMP），因此上缘之外
         的内容与波段内的合成结果完全一致；
      3. 合成后四边羽化，底色差异与边界都被摊平。

    性能：底图与两级模糊底图按「内容版本 + 波段几何」缓存，动效帧只做合成与贴图
    （实测 <0.2ms/帧），因此 EMERGE_TICK_MS 的高帧节拍不会占用主线程；空闲即隐藏。
    动画只在流式进行中播放（`set_live`）：历史会话重排/切回不会补动画。
    """

    def __init__(self, owner: QWidget, fill: str, area: Callable[[], QRect]):
        super().__init__(owner)
        self._owner = owner
        self._fill = QColor(fill)
        self._area = area          # () -> QRect：正文区域（owner 坐标系）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._hist: list = []      # [(落字时刻, 内容高度)]：清晰度曲线的行龄采样
        self._h = 0                # 最近一次正文内容高度（px）
        self._w = -1               # 最近一次正文宽度（宽度变化 → 历史作废）
        self._settled_h = 0        # 行龄作废时「已落定内容」的高度基线（防整块重播）
        self._first_wave = False   # 块刚上屏：本波允许整段可见正文一起浮现（一次性）
        self._rev = 0              # 内容版本：底图缓存的失效依据
        self._live = False         # 流式进行中（仅此时记录落字并播放动效）
        self._failed = False       # 绘制失败即退场（不再绘制，避免异常拖垮进程）
        self._paused = False       # 底图渲染护栏：渲染区块时本层不绘制（否则自渲染递归）
        self._cache = None         # (底图键, 波段底图, [两级模糊底图])
        self._built_at = 0.0       # 底图最近一次重建时刻（限频用）
        self._buf = None           # (尺寸, 合成缓存图, 蒙版缓存图)
        self._mask = None          # (尺寸, 四边羽化蒙版)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(EMERGE_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # ---------- 外部接口 ----------
    def touch(self, by_geometry: bool = False):
        """正文内容 / 几何变化后由所属区块调用：记录落字时刻并推进波前。

        by_geometry=True 表示只是几何变了（布局生效、窗口缩放、块被拉高）：此时**绝不**
        把高度变化当成新落下的字，否则每长一行都会被当成新一波浮现的起点。

        宽度变化（面板缩放、滚动条出现/消失导致的换行重算）一律作废行龄，但**不重播动画**：
        作废时把当前可见内容记为「已落定」（`_settled_h`），之后只有新增长出来的那部分
        才浮现 —— 否则每次宽度抖动都会让整块内容重新浮现一遍（用户反馈的「动画重复播放」）。
        只有「块刚上屏」（本层第一次拿到真实宽度）才把整段可见正文记为刚落下。
        """
        if not self._live and not self._timer.isActive():
            # 非流式块（历史回合 / 已收尾）与动效无关：直接跳过。长会话里上百个区块若
            # 各自做一次文本测量，窗口缩放与布局重排都会被拖成卡顿（这是本层必须守住的
            # 零开销路径）。
            return
        rect = self._area()
        height, width = int(rect.height()), int(rect.width())
        if width <= 0 or height <= 0:
            # 正文区域此刻不可浮现（思考气泡折叠态返回空区域、块被隐藏、尚未布局）：
            # 不能拿它去改写宽度/高度基线 —— 否则「空区域 → 真实宽度」会被当成一次
            # 宽度变化，块一恢复可见就把整段内容当成新落下的字重播一遍。
            self._sync()
            return
        if width != self._w:
            self._hist = []
            self._w = width
            self._h = height
            self._settled_h = height
            self._rev += 1
            self._cache = None
            # 只有「块刚上屏」才把整段可见正文记为刚落下（一次成型的内容因此整块浮现）；
            # 其余宽度变化（面板缩放、滚动条出现/消失）只是换了换行宽度，已显示的内容
            # 不得被动效重播 —— 这正是「动画重复播放若干遍」的根源。
            wave_first, self._first_wave = self._first_wave, False
            if self._live and wave_first:
                self._start_wave(height)
                self._timer.start()
            self._sync()
            return
        prev_h = self._h
        self._h = height
        if by_geometry:
            # 只是几何变了：不动底图缓存（x/y/w/h 已在缓存键里，几何变了键自然不同），
            # 否则每次布局重排都会作废一批底图，把「一次增长一次重渲」放大成「每帧重渲」。
            self._sync()
            return
        self._rev += 1
        self._cache = None
        if height < prev_h:
            self._hist = []            # 内容收缩（markdown 半解析 / 折叠）→ 历史作废
            self._settled_h = height   # 收缩后的内容已落定，只有其后的增长才浮现
        if not self._live:
            self._sync()
            return
        now = time.perf_counter()
        if not self._hist:
            self._start_wave(height, self._settled_h)
        elif (now - self._hist[-1][0]) * 1000.0 >= EMERGE_SAMPLE_MS:
            self._hist.append((now, height))
            self._prune(now)
        self._timer.start()
        self._sync()

    def arm_first_wave(self):
        """块首次被布局上屏（拿到真实几何）：允许下一波把整段可见正文记为「刚落下」，
        因此一次成型的内容（工具行 / 命令块 / 刚上屏的正文）会有整块浮现的效果。

        只在**首次**上屏时由区块调用（见 `_EmergeMixin.resizeEvent`）：之后的宽度变化
        不是「上屏」，不得重播动画 —— 否则面板缩放/滚动条出现都会让整块内容重新浮现。
        """
        self._first_wave = True

    def _start_wave(self, height: int, settled: int = 0):
        """起波基线 (落字时刻, 内容高度)。

        settled = 「本次之前已落定内容」的高度：把它放到浮现窗口之外（视作早已清晰），
        于是只有其后的**新增行**处在浮现中。settled=0（块刚上屏）时整段可见正文都算
        新落下 —— 一次成型的内容（工具行/命令块）才有整块浮现的效果。
        """
        now = time.perf_counter()
        base = max(0, min(int(settled), int(height)))
        self._hist = [(now - EMERGE_MS / 1000.0, base), (now, int(height))]

    def set_live(self, live: bool):
        """流式开关：关闭后不再记录新落字，让**当前这一波自然收尾**（≤EMERGE_MS）后隐藏。

        不做“立即隐藏/清空”：回合收尾瞬间正文往往正处在浮现中途，硬切回清晰会出现
        可见的跳变；历史会话重排/切回时本层尚未记录任何落字，故也不会误播。
        """
        self._live = bool(live)

    # ---------- 动画节拍 ----------
    def _tick(self):
        now = time.perf_counter()
        if not self._hist or (now - self._hist[-1][0]) * 1000.0 >= EMERGE_MS:
            # 窗口内不再有落字：本波浮现已走完 → 停表隐藏（下一批落字再起）
            self._timer.stop()
            self._cache = None
            self.hide()
            return
        if not self.isVisible():
            # 面板/回合被隐藏（最小化、过程区收起、会话切换）：停表**并隐藏本层** ——
            # 只停表不隐藏的话，重新显示时会残留上一帧的模糊画面（内容已不是那一帧的了）。
            self._timer.stop()
            self.hide()
            return
        self._sync()
        self.update()

    def _sync(self):
        """把浮现层贴到正文底部：覆盖「窗口内落下的正文」+ 羽化余量。

        **只在波前推进中（定时器在跑）才显示**：收尾或被隐藏时会收起本层，之后的几何
        变化（重新上屏、布局重排）不得把上一帧的残留画面又贴出来。
        """
        rect = self._area()
        top, height = self._band_geom(int(rect.height()))
        if top < 0 or height <= 0 or rect.width() <= 1:
            self.hide()
            return
        x, y = int(rect.x()), int(rect.y()) + top
        if (self.x(), self.y(), self.width(), self.height()) != (x, y, rect.width(), height):
            self.setGeometry(x, y, int(rect.width()), height)
        if self.isHidden() and self._timer.isActive():
            self.show()

    def _line_h(self) -> float:
        """单行行高（px，取区块字体度量）：波段最小高度与「书写行」范围都用它。"""
        return max(1.0, float(QFontMetrics(self._owner.font()).height()))

    def _band_geom(self, h_now: int):
        """波段几何 (top, height)（**正文内容坐标系**）：覆盖浮现窗口内落下的正文。"""
        if h_now <= 0 or not self._hist:
            return 0, 0
        min_h = int(round(self._line_h() * EMERGE_MIN_LINES))
        grow = max(0, h_now - int(self._hist[0][1]))    # 窗口内新增的正文高度
        height = int(max(min_h, grow + EMERGE_FEATHER[0] + EMERGE_FEATHER[2]))
        height = int(min(height, EMERGE_MAX_H, h_now))  # 首字落地时波段即整段
        return h_now - height, height

    def _prune(self, now: float):
        """行龄采样裁剪：只保留浮现窗口内的采样 + 一条窗口外基线（波段起点）。"""
        cut = now - EMERGE_MS / 1000.0
        while len(self._hist) > 1 and self._hist[1][0] <= cut:
            self._hist.pop(0)

    # ---------- 清晰度曲线 ----------
    def _profile(self, top: int, band_h: int, now: float) -> list:
        """清晰度曲线 [(自波段顶的距离, 原始清晰度 0..1)]，自下而上递增。

        原始清晰度 = 该行像素的「行龄 ÷ 浮现时长」：刚落下 ≈0（只在虚影里清晰个轮廓），
        早落下 →1（与正常正文一致）。另外叠加两条规则：
          · 「书写行」软化：正文仍在变化时把笔尖所在行压软（行内继续落字也看得见浮现感）；
          · 上缘过渡：波段上缘强制收敛到 1（top>0 时），使上缘外侧的清晰正文与波段内
            逐像素一致，羽化才不会留下痕迹 —— 故它最后生效、优先级最高。
        """
        hist = self._hist
        window = EMERGE_MS / 1000.0
        step = max(1, band_h // EMERGE_SAMPLES)
        top_ramp = EMERGE_TOP_RAMP if top > 0 else 0   # 波段顶即正文顶：上方无正文可比
        span = max(1.0, self._line_h() * EMERGE_WRITING_LINES)
        write_top = self._h - span                     # 书写行软化的起点（正文高度）
        u_write = 1.0 - min(1.0, max(0.0, (now - hist[-1][0]) / window))
        out = []
        # 采样点取「像素行上沿」：末点必须是最后一行的上沿（top+band_h-1），
        # 用下沿会把最后一行判成“还没落下”，反而最先清晰。
        for i in sorted({*range(0, band_h, step), band_h - 1}):
            y = top + i                       # 该行像素在正文中的高度
            birth = hist[0][0]
            for t, h in hist:                 # 历史上第一次「底部越过该行」的时刻
                if h > y:
                    birth = t
                    break
            p = (now - birth) / window
            if p < 0.0:
                p = 0.0
            elif p > 1.0:
                p = 1.0
            if u_write > 0.0 and y > write_top:
                ramp = (y - write_top) / span
                cap = 1.0 - EMERGE_WRITING_DROP * u_write * (1.0 if ramp > 1.0 else ramp)
                if cap < p:
                    p = cap
            if top_ramp:
                ramp = (top_ramp - i) / float(top_ramp)             # 上缘 → 1
                if ramp > p:
                    p = 1.0 if ramp > 1.0 else ramp
            out.append((i, p))
        return out

    @staticmethod
    def _grad(profile: list, band_h: int, fn) -> QLinearGradient:
        """清晰度曲线 → alpha 遮罩渐变：颜色取黑、只使用 alpha 通道（纯蒙版，非主题色）。"""
        g = QLinearGradient(0, 0, 0, band_h)
        for i, p in profile:
            e = p * p * (3.0 - 2.0 * p)                 # smoothstep：过渡更顺、无棱角
            a = int(round(255 * max(0.0, min(1.0, fn(e)))))
            g.setColorAt(min(1.0, i / float(band_h)), QColor(0, 0, 0, a))
        return g

    # ---------- 图层与合成 ----------
    def _layers(self, x: int, y: int, w: int, h: int, now: float):
        """(波段底图, [粗模糊, 细模糊])：按内容版本 + 波段几何缓存，内容没变不重渲染。

        内容变化但尺寸相同的（帧内连续落字）限定为每 EMERGE_SRC_MS 重建一次：落字刷新
        可达 240Hz，而屏幕刷新只有 60~144Hz，逐帧重渲底图 + 重算模糊层纯属浪费主线程；
        代价仅是「新字最多晚一帧进入浮现层」（肉眼不可见）。
        """
        key = (self._rev, x, y, w, h)
        cache = self._cache
        if cache is not None:
            ckey, csrc, cblurs = cache
            if ckey == key:
                return csrc, cblurs
            if (ckey[2:] == key[2:] and self._built_at
                    and (now - self._built_at) * 1000.0 < EMERGE_SRC_MS):
                return csrc, cblurs
        self._built_at = now
        src = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        src.fill(Qt.GlobalColor.transparent)
        self._paused = True
        try:
            # 渲染整个区块（卡片底/边框/图标壳/参数 chip 都在内），仅排除本层自身：
            # _paused 期间本层不绘制，既杜绝自渲染递归，也不会把上一帧的合成吃进底图。
            self._owner.render(src, QPoint(0, 0), QRegion(QRect(x, y, w, h)),
                               QWidget.RenderFlag.DrawWindowBackground
                               | QWidget.RenderFlag.DrawChildren)
        except Exception:
            return None, None
        finally:
            self._paused = False
        blurs = [self._blur(src, factor) for factor, _amp in EMERGE_BLUR]
        self._cache = (key, src, blurs)
        return src, blurs

    @staticmethod
    def _blur(src: QImage, factor: int) -> QImage:
        """廉价模糊：降采样再放大（成本与倍数成反比，远低于逐像素卷积）。

        倍数按波段尺寸收敛（各向同性）：波段很矮（单行）时若仍按 6 倍降采样，纵向只剩
        2~3 个像素，放大回来会成块状色块而不是模糊。
        """
        w, h = src.width(), src.height()
        f = max(1, min(factor, h // 6, w // 24))
        small = src.scaled(max(1, w // f), max(1, h // f),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

    def _buffers(self, w: int, h: int):
        """合成缓存图 (输出, 蒙版层)：按尺寸缓存，避免每帧新建图像（60fps 下的分配开销）。"""
        if self._buf is None or self._buf[0] != (w, h):
            self._buf = ((w, h),
                         QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied),
                         QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied))
        return self._buf[1], self._buf[2]

    @staticmethod
    def _masked(layer: QImage, scratch: QImage, grad: QLinearGradient) -> QImage:
        """图层裁上透明度曲线：dst = layer × grad.alpha（只借 alpha 通道做蒙版）。"""
        scratch.fill(Qt.GlobalColor.transparent)
        q = QPainter(scratch)
        q.drawImage(0, 0, layer)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.fillRect(scratch.rect(), grad)
        q.end()
        return scratch

    def _feather(self, w: int, h: int) -> QImage:
        """四边羽化蒙版：上/左右/下都渐隐，遮底与合成结果不会露出矩形边界。"""
        if self._mask is not None and self._mask[0] == (w, h):
            return self._mask[1]
        top = max(1, min(EMERGE_FEATHER[0], h - 2))          # 渐变停靠点必须严格递增
        bottom = max(1, min(EMERGE_FEATHER[2], h - 1 - top))
        side = max(1, min(EMERGE_FEATHER[1], w // 2 - 1))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        v = QLinearGradient(0, 0, 0, h)
        v.setColorAt(0.0, QColor(0, 0, 0, 0))
        v.setColorAt(top / float(h), QColor(0, 0, 0, 255))
        v.setColorAt((h - bottom) / float(h), QColor(0, 0, 0, 255))
        v.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(img.rect(), v)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        gh = QLinearGradient(0, 0, w, 0)
        gh.setColorAt(0.0, QColor(0, 0, 0, 0))
        gh.setColorAt(side / float(w), QColor(0, 0, 0, 255))
        gh.setColorAt((w - side) / float(w), QColor(0, 0, 0, 255))
        gh.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(img.rect(), gh)
        p.end()
        self._mask = ((w, h), img)
        return img

    def _compose(self, now: float):
        """合成一帧：底 = 面底色（遮住下方的清晰正文），其上叠「虚影 ⊕ 清晰正文」。"""
        w, h, x, y = self.width(), self.height(), self.x(), self.y()
        if w <= 4 or h <= 4 or x < 0 or y < 0:
            return None
        src, blurs = self._layers(x, y, w, h, now)
        if src is None:
            return None
        out, scratch = self._buffers(w, h)
        # 清晰度曲线按**内容坐标**取（0 = 正文顶），波段上沿 = 内容底边 - 波段高
        profile = self._profile(max(0, int(self._area().height()) - h), h, now)
        # 虚影整体下移 = 由下而上「浮现」；位移按最新一行（曲线末点）的新鲜度取
        rise = int(round(EMERGE_RISE_PX * (1.0 - profile[-1][1])))
        out.fill(self._fill)
        pr = QPainter(out)
        for (_factor, amp), blur in zip(EMERGE_BLUR, blurs):
            grad = self._grad(profile, h, lambda e, a=amp: a * (1.0 - e))
            pr.drawImage(0, rise, self._masked(blur, scratch, grad))
        grad = self._grad(profile, h,
                          lambda e: EMERGE_SHARP_MIN + (1.0 - EMERGE_SHARP_MIN) * e)
        pr.drawImage(0, 0, self._masked(src, scratch, grad))
        pr.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        pr.drawImage(0, 0, self._feather(w, h))     # 四边羽化（乘 alpha，不动 RGB）
        pr.end()
        return out

    def paintEvent(self, _e):
        # 底图渲染期间（_paused）必须什么都不画：区块渲染会把本层也算进去，
        # 不挡就会自渲染递归，且会把上一帧的合成结果当成内容再渲染一遍。
        if self._failed or self._paused:
            return
        try:
            img = self._compose(time.perf_counter())
            if img is None:
                return
            p = QPainter(self)
            p.drawImage(0, 0, img)
            p.end()
        except Exception:
            # 绘制期异常必须就地吞掉：Qt 虚拟函数里逃出的 Python 异常会让整个进程 abort。
            # 失败即退场（本层是透明叠加层：不再绘制 = 只少了浮现动效，正文照常显示）。
            self._failed = True
            self._timer.stop()


class _EmergeMixin:
    """给区块接入「落字浮现」：内容变化后 `_emerge_touch()`，流式开关由 ChatTurn 注入。

    区块只需在 `__init__` 末尾 `_emerge_init(fill, area)` 给出「正文区域 + 面底色」，
    之后在每次内容更新后 `_emerge_touch()`；几何变化走 `_emerge_touch(by_geometry=True)`，
    避免把布局拉高误判成新落下的字（见 `_EmergeBand.touch`）。
    """

    _emerge: Optional["_EmergeBand"] = None

    def _emerge_init(self, fill: str, area: Callable[[], QRect]):
        self._emerge = _EmergeBand(self, fill, area)

    def _emerge_touch(self, by_geometry: bool = False):
        if self._emerge is not None:
            self._emerge.touch(by_geometry=by_geometry)

    def set_live(self, live: bool):
        """流式开关（回合进行中才播放落字浮现）"""
        if self._emerge is not None:
            self._emerge.set_live(live)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 首次被布局上屏（拿到真实几何）→ 允许下一波「整块浮现」。用一次性标记而不是
        # 「宽度从 -1 变过来」：块在布局给宽之前就写入了内容，那时读到的高度来自控件
        # 默认窄宽度，不能算上屏；而流式期间后续的每一次宽度变化都不该重播整块动画。
        if self._emerge is not None and not getattr(self, "_laid_out", False):
            self._laid_out = True
            self._emerge.arm_first_wave()
        self._emerge_touch(by_geometry=True)


class _PinMixin:
    """换行标签的高度钉定 + 测量缓存。

    两件事：
    1. **钉高度**：QLabel.sizeHint 对换行长文只按单行计高，必须按真实宽度显式钉住
       最小高度，否则气泡底部的文字会被裁掉。
    2. **缓存测量**：QLabel.heightForWidth 会为整篇富文本做一次完整布局。流式期间
       ChatTurn 每 tick 都会问一遍所有块的高度，长正文/大子块会把主线程拖垮 ——
       按 (内容版本, 宽度) 缓存测量结果，内容或宽度没变就直接返回旧值。
    """

    _content_ver = 0      # 内容版本：set_content/set_html/折叠态变化时自增
    _hfw_key = None       # (内容版本, 宽度)
    _hfw_val = 0
    _pin_w = -1           # 上次钉定所用的宽度（-1 = 未钉定）

    def _fix_vertical(self):
        """竖直方向固定为内容高度，并开启 heightForWidth 协商。

        必须在子类构造里显式调用（本 mixin 无 __init__，不能自动生效）。
        `setHeightForWidth(True)` 让布局直接以**目标宽度**询问高度，而不是先给宽度
        再回头改高度 —— 少了这一轮，块会先按旧宽度定高、再收缩，观感就是闪烁与
        底部残留空白。
        """
        pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def _bump_content(self):
        """内容或影响高度的状态发生变化 → 让测量缓存失效（高度由 ChatTurn 统一重算）"""
        self._content_ver = getattr(self, "_content_ver", 0) + 1
        self._hfw_key = None
        self._pin_w = -1

    def _inner_w(self) -> int:
        m = self.layout().contentsMargins()
        return max(1, self.width() - m.left() - m.right())

    def _pin_wrapping(self, width: int = None):
        """按给定宽度把正文标签的**最小高度**钉到内容的真实高度。

        三条要点：
        1. **只钉下界、不设上限**：设上限会在「新内容已 setText、上限还是旧值」的
           瞬间把文字裁掉并回收，反复触发就是闪烁；下界足够保证不被压扁；
        2. **必须顶端对齐**（见 `_mk_label`）：QLabel 默认 AlignVCenter 会把多余高度
           上下均分，看起来就是每段文字上下各一片空白；
        3. **宽度必须由调用方给定**：控件刚创建/刚重建时 `width()` 还是 Qt 默认值，
           按它测出的高度明显偏大 → 正文底部残留空白。
        """
        lbl = getattr(self, "_body", None)
        if lbl is None:
            return
        w = int(width) if width else self._inner_w()
        if w <= 0:
            return
        self._pin_w = w
        h = _label_hfw(lbl, w, self._content_ver)
        if _need_resize(lbl.minimumHeight(), h):
            lbl.setMinimumHeight(h)

    def sizeHint(self) -> QSize:
        """块高度 = 内容在**当前宽度**下的真实高度。

        换行标签的 sizeHint 只按单行计高（QLabel 固有行为），若直接交给布局，块会被
        压扁/留白。这里按当前宽度给出真实高度，配合竖直 Fixed 策略，块的高度就恒定
        等于内容高度 —— 这是「消灭多余高度 ⇒ 消灭上下空白」的第二半。
        """
        w = self.width()
        if w <= 0:
            return super().sizeHint()
        return QSize(w, self.heightForWidth(w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pin_wrapping()


class _FoldMixin:
    """正文超行自动折叠 + 「展开/收起」开关：思考气泡、工具输出、命令块共用一套。

    折叠只改两件事：**铺进标签的正文**（折叠态只铺前缀）与**钉住的最小高度**（折叠态 =
    行数上限）。全文留在 `_fold_parts`（`_fold_full` 是它的拼接）里、绝不丢弃，点开即看到
    完整内容 —— 因此界面上不存在任何截断，「展开全部」名副其实。

    **折叠区可以是一条标签，也可以是多条**（`_fold_targets()`）：命令块把「`$ 命令` +
    输出」当成**同一个**折叠区 —— 任一段超出上限就出现同一个「展开全部」，点开两段同时
    铺全文，用户不必分两次点。判定与钉高都按「各段独立上限」做（每段各自最多
    `_fold_lines()` 行），因此任一超长段都不会把另一段挤没。

    子类只差几处，各自实现钩子给出：
      · `_fold_label()`     承载遮罩的那条标签（多段时取**最下方**那条）；
      · `_fold_targets()`   参与折叠的全部标签（默认就是 `_fold_label()` 一条）；
      · `_fold_avail_w()`   标签的可用宽度（图标壳 / 描边 / 内边距各不相同）；
      · `_fold_gap()`       正文与开关之间的间距（由各自布局给出的实际值）；
      · `_fold_preview()`   折叠态铺进**每一段**的字符预算。
    开关的位置由子类自己插进布局（各块结构不同），混入只负责状态、显隐、测量与遮罩。
    """

    _fold_open: Optional[bool] = None   # 用户手动选择：None = 自动（超出上限即折叠）
    _folded = None                      # 折叠态缓存：仅在状态真的变化时改几何
    _fold_handler: Optional[Callable[[], None]] = None
    _fold_content_reset = True          # 内容非上一次的增长时复位手动展开（思考气泡置 False）

    def _fold_init(self, style: ChatStyle, fill: str, open_text: str,
                   icon_provider: IconProvider = None):
        """创建开关与渐隐遮罩，返回开关控件（由子类放进自己的布局）。

        `icon_provider` 可省：命令块在部分构造点拿不到图标提供者，此时开关就是纯文字。
        """
        self._fold_style = style
        self._fold_provider = icon_provider
        self._fold_open_text = open_text
        self._fold_parts: list = []      # 各段全文（与 `_fold_targets()` 一一对应）
        self._fold_full = ""             # 各段拼接（非空判定 / 长度口径，兼容既有调用方）
        self._fold_h_key = None          # 各段全文高度缓存的键 (内容版本, 宽度)
        self._fold_h_val: list = []
        self._fold_btn = PillButton(style, open_text, self._fold_chev(),
                                    dashed=True, parent=self)
        self._fold_btn.clicked.connect(self._fold_toggle)
        self._fold_btn.hide()
        self._fold_mask = _FadeMask(fill, self)
        self._fold_mask.hide()
        return self._fold_btn

    def _fold_chev(self, rotated: int = 0) -> Optional[QIcon]:
        """开关左侧的箭头图标（无图标提供者时为 None → 纯文字开关）"""
        if self._fold_provider is None:
            return None
        icon = self._fold_provider("chev", FONT_SMALL, self._fold_style.accent)
        return rotate_icon(icon, rotated, FONT_SMALL) if rotated else icon

    def set_fold_handler(self, fn: Callable[[], None]):
        """折叠态改变本块高度：必须让外层重钉回合高度，否则展开后被回合总额压扁/遮挡。"""
        self._fold_handler = fn

    # ---------- 子类钩子 ----------
    def _fold_label(self) -> QLabel:
        """承载渐隐遮罩的那条标签（多段折叠区取**最下方**那条）"""
        raise NotImplementedError

    def _fold_targets(self) -> list:
        """参与折叠的全部标签，按显示顺序（默认只有 `_fold_label()` 一条）。

        返回多条时它们共享一个折叠开关与一个判定：**任一段**超出上限即折叠，展开时
        各段同时铺全文。全文按同样顺序经 `_fold_set_parts()` 喂入。
        """
        return [self._fold_label()]

    def _fold_avail_w(self, width: int = None) -> int:
        raise NotImplementedError

    def _fold_gap(self) -> int:
        raise NotImplementedError

    def _fold_preview(self) -> int:
        raise NotImplementedError

    def _fold_on_state(self, foldable: bool):
        """折叠态收敛后的钩子：子类据此同步自己那层开关外壳的显隐。

        默认无操作（思考气泡把开关直接放进主布局，不需要外壳）。"""

    # ---------- 状态 ----------
    def _fold_set_full(self, full_html: str):
        """喂入**单段**全文（多段折叠区用 `_fold_set_parts`）"""
        self._fold_set_parts([full_html])

    def _fold_set_parts(self, parts: list):
        """喂入各段全文（顺序与 `_fold_targets()` 一致）。

        内容**不是上一份的增长**（另一个工具的输出出现在同一控件上）时复位用户的手动展开：
        否则上一个工具的展开状态会串到新内容上。思考气泡不适用这条（它的富文本被
        `<div>` 包裹，增长时首尾都变），改由调用方按段标识 `sid` 判定 —— 见
        `_fold_content_reset`。
        """
        new_parts = [str(p or "") for p in (parts or [])]
        joined = "".join(new_parts)
        if self._fold_content_reset and self._fold_full and not joined.startswith(self._fold_full):
            self._fold_reset()
        self._fold_parts = new_parts
        self._fold_full = joined

    def _fold_reset(self):
        """回到「自动」判定并把开关文案复位（换内容时调用）"""
        self._fold_open = None
        if self._fold_btn is not None and self._fold_btn.text() != self._fold_open_text:
            self._fold_btn.setText(self._fold_open_text)
            self._fold_btn.setIcon(self._fold_chev())

    def _line_height(self) -> int:
        """单行行高（px）：折叠上限按它折算"""
        return max(1, self._fold_label().fontMetrics().lineSpacing())

    def _fold_limit_h(self) -> int:
        return self._line_height() * self._fold_lines()

    def _fold_lines(self) -> int:
        raise NotImplementedError

    def _fold_heights(self, width: int = None) -> list:
        """各段全文（不截断）在可用宽度下的高度列表，带 (内容版本, 宽度) 缓存。

        判定必须基于**全文**：若按「当前铺进标签的正文」判断，短前缀在超宽窗口下可能
        不到折叠上限 → 判定在折叠/展开之间翻转（开关反复闪烁）。

        实现上**估算优先**（见 `_fold_line_floor` / `_fold_est_lines` 的说明）：先看显式
        换行数是否已超上限，再用采样字宽外推总行数，两者都能明确给出结论时**完全不做
        排版**；只有估算落在阈值附近（`_EST_MARGIN_LINES`）才临时铺一次全文真测 ——
        此时内容量与阈值同量级，成本有界。
        """
        w = max(1, self._fold_avail_w(width))
        key = (self._content_ver, w)
        if self._fold_h_key == key:
            return self._fold_h_val
        vals = []
        for i, lbl in enumerate(self._fold_targets()):
            html = self._fold_parts[i] if i < len(self._fold_parts) else ""
            vals.append(self._fold_one_h(lbl, html, w))
        self._fold_h_key, self._fold_h_val = key, vals
        return vals

    def _fold_one_h(self, lbl: QLabel, html: str, w: int) -> int:
        """单段全文在宽度 `w` 下的高度（估算优先，只有阈值附近才真测）"""
        if not html:
            return 0
        lines, line_h = self._fold_lines(), self._line_height()
        limit = lines * line_h
        floor = _fold_line_floor(html)
        if floor > lines:
            return limit + (floor - lines) * line_h + 1    # 显式换行已超上限：必然可折叠
        est = _fold_est_lines(html, w, lbl.fontMetrics())
        if est > lines + _EST_MARGIN_LINES:
            return limit + line_h + 1                      # 明显超出：不排版
        if est < lines - _EST_MARGIN_LINES:
            return max(1, int(est * line_h))               # 明显不足：不排版
        return self._fold_measure_one(lbl, html, w)        # 阈值附近：真测

    def _fold_measure_one(self, lbl: QLabel, html: str, w: int) -> int:
        """把一段全文临时铺进它的标签量一次真实高度（昂贵，仅阈值附近调用），测完立刻还原"""
        saved_txt, saved_min = lbl.text(), lbl.minimumHeight()
        lbl.setMinimumHeight(0)
        lbl.setText(html)
        h = int(lbl.heightForWidth(w) or 0)
        lbl.setText(saved_txt)
        lbl.setMinimumHeight(saved_min)
        return h

    def _fold_foldable(self, width: int = None) -> bool:
        """是否可折叠：**任一段**全文超出各自的行数上限即成立（成本见 `_fold_heights`）。

        `width` 由 `heightForWidth` 传入，保证「判定宽度」与「测量宽度」一致 ——
        否则布局探宽期间会按控件瞬时宽度反复改判，开关跟着布局闪烁。
        """
        if not self._fold_full:
            return False
        limit = self._fold_limit_h()
        return any(h > limit for h in self._fold_heights(width))

    def _fold_is_folded(self) -> bool:
        return self._fold_foldable() and self._fold_open is not True

    # ---------- 应用 ----------
    def _fold_shown_parts(self) -> list:
        """各段当前该铺进标签的正文：展开态（或本来就短）给全文，否则给前缀 + 省略号。

        预算**按段各自给**（`_fold_preview()`）：命令与输出各留一份，任一超长的段都不会
        因为另一段太长而被整段挤掉，用户点开两段同时看到全文。
        """
        if self._fold_open is True:
            return list(self._fold_parts)
        budget = self._fold_preview()
        return [p if len(p) <= budget else safe_prefix(p, budget) + "…"
                for p in self._fold_parts]

    def _fold_apply_text(self) -> bool:
        """按折叠态铺各段正文；与标签现状一致时不触碰标签（返回是否真的改了）。"""
        want = self._fold_shown_parts()
        changed = False
        for i, lbl in enumerate(self._fold_targets()):
            txt = want[i] if i < len(want) else ""
            if lbl.text() != txt:
                lbl.setText(txt)
                changed = True
        return changed

    def _fold_visible_h(self, width: int = None) -> int:
        """折叠区当前应占的高度：折叠态 = **各段上限之和**，展开态 = 各段全文高度之和。

        块高预算必须用它，而不是直接测标签：折叠态标签里铺的是**前缀**（比上限长，
        为的是让折叠判定稳定），直接测就会按前缀申请高度 —— 块比可见内容高出一截，
        底部留白（正是此前反复修掉的那类问题）。
        """
        w = self._fold_avail_w(width)
        if w <= 0:
            return 0
        limit = self._fold_limit_h()
        open_ = self._fold_open is True
        total = 0
        for lbl in self._fold_targets():
            if lbl.isHidden():
                continue
            h = _label_hfw(lbl, w, self._content_ver)
            total += h if open_ else min(h, limit)
        return total

    def _fold_pin(self, width: int = None):
        """钉住各段标签的高度：折叠态 = 各自的行数上限，展开态 = 全文高度。

        **绝不允许钉入超过折叠上限的值**：否则本块真实高度远超 heightForWidth 的估算，
        把整条回合撑爆、把下方开关压扁（与思考气泡同一条约束）。
        """
        w = self._fold_avail_w(width)
        if w <= 0:
            return
        limit = self._fold_limit_h()
        open_ = self._fold_open is True
        for lbl in self._fold_targets():
            if lbl.isHidden():
                continue
            h = _label_hfw(lbl, w, self._content_ver)
            target = h if open_ else min(h, limit)
            if target > 0 and _need_resize(lbl.minimumHeight(), target):
                lbl.setMinimumHeight(target)

    def _fold_extra_h(self, width: int = None) -> int:
        """本块高度里属于「间距 + 开关」的那一份（不可折叠时为 0）"""
        if not self._fold_foldable(width):
            return 0
        return self._fold_gap() + self._fold_btn.sizeHint().height()

    def _fold_apply(self):
        """收敛到目标折叠态：铺正文、钉高度、显隐开关与遮罩。

        正文必须在**每次**调用时对齐当前折叠态：内容的更新（`_fold_set_full`）不改折叠态
        本身，若只在状态翻转时铺正文，新到的输出就会因为「状态没变」而被跳过（实测踩到：
        短输出到达后标签仍是空串）。
        """
        foldable = self._fold_foldable()
        folded = foldable and self._fold_open is not True
        self._fold_apply_text()
        if self._folded == folded:
            self._fold_pin()
            self._fold_place_mask()
            return
        self._folded = folded
        self._fold_on_state(foldable)      # 先让外壳显隐到位，再钉子项
        self._fold_btn.setVisible(foldable)
        self._fold_pin()
        self._fold_mask.setVisible(folded)
        self._fold_place_mask()
        self.updateGeometry()

    def _fold_place_mask(self):
        """遮罩贴在折叠边界。用 `_folded` 判定而非 isVisible —— 面板未显示时 isVisible 恒为
        False，据此判定会让遮罩在显示后落在错误位置。"""
        if not self._folded:
            return
        lbl = self._fold_label()
        p = lbl.mapTo(self, QPoint(0, 0))
        y = p.y() + lbl.height() - FADE_H
        self._fold_mask.setGeometry(p.x(), max(0, y), max(1, lbl.width()), FADE_H)
        self._fold_mask.raise_()      # 覆盖在正文之上：控件创建顺序不保证叠放次序

    def _fold_toggle(self):
        opened = self._fold_open is not True
        self._fold_open = opened
        self._fold_btn.setText(FOLD_CLOSED_TEXT if opened else self._fold_open_text)
        self._fold_btn.setIcon(self._fold_chev(180 if opened else 0))
        self._fold_apply_text()      # 展开铺全文 / 收起回到前缀
        self._bump_content()         # 折叠态影响高度 → 测量缓存失效
        self._fold_apply()
        if self._fold_handler is not None:
            self._fold_handler()


class ThinkBubble(_FoldMixin, _PinMixin, _EmergeMixin, QFrame):
    """思考过程气泡（demo .think-bubble）：卡片底 + 1px 边框 + 非对称圆角
    （左上 6 为「小尾巴」角），头行 = 图标壳 + 「思考过程」+ tag 胶囊（含三点），
    正文超 THINK_FOLD_LINES 行自动折叠并以渐隐遮罩收尾（折叠机制见 `_FoldMixin`）。"""

    # 思考正文被 <div> 包裹，同一段继续落字时首尾都会变 → 不能用「是否上一份的增长」判
    # 同段；改由 sid（段标识）判定，故关掉基类的内容复位。
    _fold_content_reset = False

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self._icon_provider = icon_provider
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"ThinkBubble {{ background: {style.think_bg_of()};"
            f" border: 1px solid {style.think_border_of()};"
            f" border-top-left-radius: {BUBBLE_RADIUS_THINK_TAIL}px;"
            f" border-top-right-radius: {RADIUS_LG}px;"
            f" border-bottom-right-radius: {RADIUS_LG}px;"
            f" border-bottom-left-radius: {RADIUS_LG}px; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(THINK_PAD_H, THINK_PAD_V, THINK_PAD_H, THINK_PAD_V)
        root.setSpacing(THINK_HEAD_GAP)

        head = QWidget(self)
        head.setFixedHeight(THINK_ICON)     # 固定头行高度：避免布局余量被头行吸走
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(9)
        self._icon = _tile(style, icon_provider("think", FONT_BODY + 3, style.icon_color),
                           THINK_ICON, RADIUS_SM)
        hl.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel("思考过程")
        title.setStyleSheet(
            f"color: {style.text}; font-size: {FONT_BODY}px; font-weight: 600;"
            f" font-family: {style.font_ui}; background: transparent;")
        hl.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch(1)
        self._tag = QWidget(head)
        self._tag.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tag_style("")     # 胶囊底随阶段（PLANNING/EXEC）取色
        tl = QHBoxLayout(self._tag)
        tl.setContentsMargins(9, 4, 9, 4)
        tl.setSpacing(6)
        self._tag_text = QLabel("")
        self._tag_text.setStyleSheet(
            f"color: {style.tag_fg}; font-size: {FONT_CAPTION}px; background: transparent;")
        tl.addWidget(self._tag_text, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dots = DotsLabel(style.tag_fg, self._tag)
        tl.addWidget(self._dots, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(head)

        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; color: {style.text_dim};"
            f" font-size: {FONT_BODY}px; line-height: 1.75;"
            f" font-family: {style.font_ui};")
        root.addWidget(self._body)

        fold_btn = self._fold_init(style, style.think_bg_of(), THINK_FOLD_TEXT,
                                   icon_provider)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addStretch(1)
        wrap.addWidget(fold_btn)
        wrap.addStretch(1)
        root.addLayout(wrap)
        # 末尾留白：外层若给了多余高度（历史重建/布局余量），余量全部落到底部，
        # 避免被 QBoxLayout 摊到各子项之间，把正文与开关拉开大片空隙
        root.addStretch(1)

        self._seg_id = None      # 已绑定的思考段标识（换段 → 折叠态回到自动判定）
        self._fix_vertical()
        # 正文坐落于卡片底（fill=浮现遮罩色），浮现只覆盖思考正文标签那一块（头行图标/胶囊不动）
        self._emerge_init(style.band or style.think_bg_of(), self._emerge_area)

    # ---------- 折叠钩子（见 _FoldMixin） ----------
    def _fold_label(self) -> QLabel:
        return self._body

    def _fold_lines(self) -> int:
        return THINK_FOLD_LINES

    def _fold_preview(self) -> int:
        return THINK_PREVIEW_CHARS

    def _fold_gap(self) -> int:
        return THINK_HEAD_GAP       # 正文与开关之间就是主布局的 spacing

    def _fold_avail_w(self, width: int = None) -> int:
        return int(width) if width else self._inner_w()

    def _fold_foldable(self, width: int = None) -> bool:
        """思考正文按**当前铺进标签的正文**判断（折叠态即前缀），而非全文。

        长篇推理每 tick 都在落字，按全文测量等于每 tick 重排整篇富文本（O(n²)，主线程被
        排满后气泡与指示器剧烈抖动 —— 这正是 THINK_PREVIEW_CHARS 存在的原因）。
        THINK_PREVIEW_CHARS 取值远大于任意宽度下 5 行的字数，故前缀高度恒 > 折叠上限，
        判定稳定不翻转（工具输出不逐帧增长，走基类的全文估算，见 `_FoldMixin._fold_full_h`）。
        """
        return self._full_h() > self._fold_limit_h()

    def _tag_style(self, tag: str):
        self._tag.setStyleSheet(
            f"QWidget {{ background: {self._style.tag_bg_of(tag)};"
            f" border-radius: {RADIUS_PILL}px; }}")

    # ---------- 内容 ----------
    def set_content(self, tag: str, body_html: str, sid=None, defer: bool = False):
        """写入思考正文（全文交 `_fold_full` 保存，标签按折叠态只铺前缀）。

        关键：正文没变（长推理折叠后每 tick 前缀都不再变化）时整段短路 —— 既不重设
        富文本、也不重排、也不重复设样式。否则长推理下每 tick 都要重排整篇文档，
        主线程被排满后气泡与打字指示器剧烈抖动、消息区上下反复出现大片空白。

        `sid` 是调用方（面板）下发的**段标识**（同一段思考持续落字时不变）：只有换到
        另一段思考才复位用户的手动展开（点过「继续查看/收起」）—— 此前每个 tick 无条件
        复位，用户一展开就被下一 tick 自动收起回去，表现为折叠态/动画被反复回放。
        """
        if sid is not None and self._seg_id is not None and sid != self._seg_id:
            self._fold_reset()
        if sid is not None:
            self._seg_id = sid
        self._fold_set_full(body_html or "")
        if self._tag_text.text() != (tag or ""):
            self._tag_text.setText(tag or "")
            self._tag_style(tag or "")   # setStyleSheet 会触发样式重算，仅在阶段变化时做
        self._tag.setVisible(bool(tag))
        if not self._fold_apply_text():
            return                        # 可见正文未变：几何/动效都不需要动
        self._body.setMinimumHeight(0)    # 重测前先解除旧钳制
        self._body.setMaximumHeight(16777215)
        self._bump_content()
        if defer:
            self._deferred_fold = True    # 见 ToolCallRow.set_content 的 defer 说明
        else:
            self._fold_apply()
        self._emerge_touch()

    def resume_deferred(self):
        """应用被推迟的折叠态收尾（见 ToolCallRow.set_content 的 defer 说明）"""
        if not getattr(self, "_deferred_fold", False):
            return
        self._deferred_fold = False
        self._fold_apply()

    def _emerge_area(self) -> QRect:
        """思考正文区域：折叠态只到折叠上限（正文已被裁，波段必须贴可视底边）。

        折叠态下笔尖所在的字在可视区之外，没有可浮现的对象 → 返回空区域让浮现层收起。
        """
        lbl = self._body
        full = self._full_h()
        if full > self._fold_limit_h() and self._fold_open is not True:
            return QRect()
        return QRect(lbl.x(), lbl.y(), max(1, lbl.width()), int(full))

    def set_live(self, live: bool):
        self._dots.set_live(live)
        super().set_live(live)

    # ---------- 折叠 ----------
    def _full_h(self) -> int:
        """当前铺进标签的正文高度（折叠态即前缀高度，见 `_fold_foldable`）"""
        return _label_hfw(self._body, self._inner_w(), self._content_ver)

    def _pin_wrapping(self, width: int = None):
        """钉住正文高度：折叠态 = 行数上限，展开态 = 全文高度（见 `_FoldMixin._fold_pin`）"""
        w = int(width) if width else self._inner_w()
        if w <= 0:
            return
        self._pin_w = w
        self._fold_pin(width)

    def _inner_w(self) -> int:
        """布局内区宽度：QFrame 的 1px 描边会把内区整体内缩，必须一并让出"""
        m = self.layout().contentsMargins()
        return max(1, self.width() - 2 * self.frameWidth() - m.left() - m.right())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fold_apply()

    def heightForWidth(self, width: int) -> int:
        """本块在给定宽度下需要的高度（必须与 _pin_wrapping 钉出的真实高度一致，
        否则 ChatTurn 的最小高度会小于内容，Qt 会压扁最后一个不设最小值的子项）。

        描边（1px × 上下）与内边距都要计入：漏算描边会让真高比预算多 2px，
        展开长思考时开关按钮被压掉两像素。
        """
        bd = 2 * self.frameWidth()
        m = self.layout().contentsMargins()
        inner = max(1, int(width) - bd - m.left() - m.right())
        h = bd + m.top() + m.bottom() + THINK_ICON + THINK_HEAD_GAP
        h += self._fold_visible_h(inner)
        return h + self._fold_extra_h()


class ToolCallRow(_FoldMixin, _PinMixin, _EmergeMixin, QWidget):
    """工具调用行（demo .tool-call）：无气泡，图标壳 + 工具名 + meta + 参数 chip。

    工具执行结果**挂在本行下方**（`_out_box`：左侧淡蓝竖线 + 输出正文），不再另起
    「执行结果」区块 —— 一次工具调用在视图上就是一个整体：调用在上、输出在下，
    中间由 TOOL_OUT_GAP 保持间距。超长输出由 `_FoldMixin` 自动折叠（可「展开全部」）。
    """

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self._icon_provider = icon_provider
        # 先放通用占位图标：真实工具名在 set_content 时才知道（那时换成专属图标）
        self._icon = _tile(style, icon_provider("tool", FONT_BASE + 2, style.icon_color),
                           TOOL_ICON, RADIUS_TILE, ratio=TOOL_GLYPH_RATIO,
                           bg=style.tool_shell_of())
        root.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._body = QWidget(self)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(3)
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {style.text}; font-size: {FONT_BODY}px; font-weight: 600;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._title)
        self._meta = QLabel("")
        self._meta.setWordWrap(True)
        self._meta.setStyleSheet(
            f"color: {style.muted}; font-size: {FONT_CAPTION}px;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._meta)
        self._chips = QWidget(self._body)
        self._chip_lay = FlowLayout(self._chips, h_gap=6, v_gap=6)
        bl.addWidget(self._chips)
        # ---- 输出区（工具/命令执行结果挂在本行下方）----
        self._out_box = QWidget(self._body)
        ob = QHBoxLayout(self._out_box)
        ob.setContentsMargins(0, TOOL_OUT_GAP, 0, 0)
        ob.setSpacing(OUT_LINE_GAP)
        self._out_line = QFrame(self._out_box)
        self._out_line.setFixedWidth(OUT_LINE_W)
        # QWidget 系控件的 QSS 底色默认不参与绘制，必须显式开启样式化背景
        # （与 CmdBlock / ThinkBubble 同一约定），否则竖线是「透明」的。
        self._out_line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._out_line.setStyleSheet(
            f"background: {style.out_line_of()}; border-radius: {OUT_LINE_W // 2}px;")
        ob.addWidget(self._out_line)
        self._out = _mk_label(style)
        self._out.setStyleSheet(
            f"background: transparent; color: {style.out_fg_of()};"
            f" font-size: {FONT_SMALL}px; font-family: {style.font_mono}; line-height: 1.6;")
        ob.addWidget(self._out, 1)
        self._out_box.hide()      # 无输出时不占高度（工具行等价于原样）
        bl.addWidget(self._out_box)
        # ---- 输出折叠开关（超长输出时出现，见 _FoldMixin）----
        fold_btn = self._fold_init(style, style.bg, OUT_FOLD_TEXT, icon_provider)
        self._fold_holder = QWidget(self._body)
        hl = QHBoxLayout(self._fold_holder)
        hl.setContentsMargins(0, FOLD_GAP, 0, 0)
        hl.setSpacing(0)
        hl.addStretch(1)
        hl.addWidget(fold_btn)
        hl.addStretch(1)
        self._fold_holder.hide()
        bl.addWidget(self._fold_holder)
        root.addWidget(self._body, 1)
        self._body_lay = bl
        self._fix_vertical()
        # 工具行无气泡（坐在面板底色上），浮现只覆盖文字容器，图标壳保持清晰
        self._emerge_init(style.band or style.bg, self._emerge_area)

    # ---------- 折叠钩子（见 _FoldMixin） ----------
    def _fold_label(self) -> QLabel:
        return self._out

    def _fold_lines(self) -> int:
        return OUT_FOLD_LINES

    def _fold_preview(self) -> int:
        return OUT_PREVIEW_CHARS

    def _fold_gap(self) -> int:
        # 开关外面还套了一层 holder（自身已含 FOLD_GAP 上边距），holder 与输出区之间
        # 由 _body_lay 的 spacing 隔开 —— 两份都要计入高度预算
        return self._body_lay.spacing() + FOLD_GAP

    def _fold_avail_w(self, width: int = None) -> int:
        w = int(width) if width else self._inner_w()
        return self._out_inner_w(self._body_inner_w(w))

    def _fold_on_state(self, foldable: bool):
        """开关外壳只在可折叠时占位：不可折叠时整层收起，工具行恢复为「一行调用 + 输出」"""
        self._fold_holder.setVisible(foldable)

    def _emerge_area(self) -> QRect:
        """工具行文字区域：文字容器矩形（含标题/meta/chip/输出），图标壳与行样式不参与。"""
        body = self._body
        return QRect(body.x(), body.y(), max(1, body.width()), max(0, body.height()))

    def set_content(self, name: str, meta: str, params: dict, *, ico: str = None,
                    out: str = None, tip: str = "", defer: bool = False):
        self._title.setText(name or "")
        # 图标 kind 优先取调用方给的 ico（技能/插件/并行等伪 kind），未给则按工具名解析；
        # 每个工具/伪 kind 都有自己的专属图标（tool_icons 表）。
        self._icon.setPixmap(_tile_pixmap(
            self._icon_provider(ico or name or "tool", FONT_BASE + 2,
                                self._style.icon_color),
            TOOL_ICON, TOOL_GLYPH_RATIO))
        # 图标气泡提示：说明这一行在调用什么（工具用途 / 技能说明 / 所属插件），
        # 仅挂在图标壳上，避免覆盖正文的文本选择与链接点击。
        self._icon.setToolTip(tip or "")
        self._meta.setText(meta or "")
        while self._chip_lay.count():
            item = self._chip_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for key, val in (params or {}).items():
            self._chip_lay.addWidget(self._chip(key, val))
        # 输出正文：空则整块收起，工具行恢复为「一行调用」；超长则由 _FoldMixin 折叠
        self._fold_set_full(out or "")
        if defer:
            # 隐藏过程块（历史回合收起态）：先只记下内层可见性，等空闲切片再 setVisible ——
            # 首次显示输出标签会触发整篇富文本排版，是长对话切换卡顿的大头（见 _IdleSpreader）
            self._deferred_show = (bool(meta), bool(params), bool(out))
        else:
            self._meta.setVisible(bool(meta))
            self._chips.setVisible(bool(params))
            self._out_box.setVisible(bool(out))
        self._bump_content()
        if defer:
            self._deferred_fold = True    # 折叠同样会铺富文本：一并推迟到空闲切片
        else:
            self._fold_apply()
        self._emerge_touch()

    def resume_deferred(self):
        """应用被推迟的内层可见性与折叠态（首次显示输出标签的排版在这里发生）"""
        st = getattr(self, "_deferred_show", None)
        if st is not None:
            self._deferred_show = None
            meta, chips, out = st
            self._meta.setVisible(meta)
            self._chips.setVisible(chips)
            self._out_box.setVisible(out)
        if getattr(self, "_deferred_fold", False):
            self._deferred_fold = False
            self._fold_apply()

    def _chip(self, key: str, value) -> QLabel:
        lbl = QLabel(f"<b>{key}</b>&nbsp;{value}")
        lbl.setStyleSheet(
            f"background: {self._style.tag_bg}; color: {self._style.tag_fg};"
            f" border-radius: {RADIUS_CHIP}px; padding: 4px 10px;"
            f" font-size: {FONT_CAPTION}px; font-family: {self._style.font_mono};")
        return lbl

    def _inner_w(self) -> int:
        return self._body_inner_w(self.width())

    def _body_inner_w(self, width: int) -> int:
        """文字列（标题/meta/输出）的可用宽度：块宽再让出左侧图标壳与它的间距。

        **不能用块宽直接当文字宽**：本行左侧还有一列图标壳，两者相差约 40px。
        按块宽测出的高度会偏小（长输出尤其明显：少算一行 ≈15px），而
        `QLabel.heightForWidth` 又受最小高钳制 —— 偏小的值会被外层布局当成槽位高度，
        块却被 QWidget 钳回自己的最小高，后一块便骑到本块上（用户反馈的
        「工具输出过长时遮挡/挤压思考气泡与回复正文」）。
        """
        lay = self.layout()
        return max(1, int(width) - TOOL_ICON - lay.spacing()
                   - lay.contentsMargins().right())

    @staticmethod
    def _out_inner_w(inner: int) -> int:
        """输出正文的可用宽度：工具行文字宽度再让出左侧竖线与它到文字的间距"""
        return max(1, int(inner) - OUT_LINE_W - OUT_LINE_GAP)

    def _pin_wrapping(self, width: int = None):
        w = int(width) if width else self._inner_w()
        if w <= 0:
            return
        self._pin_w = w            # 记的是**入参宽度**（ChatTurn 的增量判据按它比对）
        body = self._body_inner_w(w)
        if not self._meta.isHidden():
            h = _label_hfw(self._meta, body, self._content_ver)
            if _need_resize(self._meta.minimumHeight(), h):
                self._meta.setMinimumHeight(h)
        if not self._out_box.isHidden():
            self._fold_pin(width)      # 输出标签：折叠态 = 行数上限，展开态 = 全文高度

    def heightForWidth(self, width: int) -> int:
        inner = self._body_inner_w(width)

        h = self._title.sizeHint().height()
        if not self._meta.isHidden():
            h += self._body_lay.spacing() + _label_hfw(self._meta, inner, self._content_ver)
        if not self._chips.isHidden():
            h += self._body_lay.spacing() + self._chip_lay.heightForWidth(inner)
        if not self._out_box.isHidden():
            # 输出区高度 = 它自己的上边距 + 输出标签当前应占的高度（含折叠上限）
            h += self._body_lay.spacing() + TOOL_OUT_GAP + self._fold_visible_h(width)
            h += self._fold_extra_h(width)  # 折叠开关（含间距）：仅可折叠时占高
        return max(TOOL_ICON, h)


class CmdBlock(_FoldMixin, _PinMixin, _EmergeMixin, QFrame):
    """执行命令块（demo .cmd）：圆角边框 + 标题栏三点 + `$ 命令` + 输出行。

    「`$ 命令`」与「输出」同属**一个**折叠区（`_fold_targets`）：任一段超出
    `OUT_FOLD_LINES` 行即出现同一个「展开全部」，点开两段同时铺全文 —— 长命令与长输出
    都能完整查看；标题栏（三点 + 工具名）不参与折叠。
    """

    def __init__(self, style: ChatStyle, icon_provider: IconProvider = None,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CmdBlock {{ background: {style.panel};"
            f" border: 1px solid {style.border}; border-radius: {RADIUS_CMD}px; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            f"QWidget {{ background: transparent;"
            f" border-bottom: 1px solid {style.border}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(CMD_PAD_H, CMD_PAD_V, CMD_PAD_H, CMD_PAD_V)
        bl.setSpacing(DOT_GAP)
        for _ in range(3):
            dot = QLabel(bar)
            dot.setFixedSize(DOT_D, DOT_D)
            dot.setStyleSheet(f"background: {style.border}; border-radius: {DOT_D // 2}px;")
            bl.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addSpacing(CMD_PAD_H)
        self._bar_label = QLabel("")
        self._bar_label.setStyleSheet(
            f"color: {style.text_dim}; font-size: {FONT_CAPTION}px;"
            f" font-family: {style.font_mono}; background: transparent;")
        bl.addWidget(self._bar_label, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        root.addWidget(bar)

        self._cmd = _mk_label(style)
        self._cmd.setStyleSheet(
            f"background: transparent; padding: {CMD_PAD_V + 4}px {CMD_PAD_H}px;"
            f" color: {style.cmd_fg}; font-size: {FONT_BODY}px;"
            f" font-family: {style.font_mono};")
        root.addWidget(self._cmd)

        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; padding: 0 {CMD_PAD_H}px {CMD_PAD_V + 4}px;"
            f" color: {style.ok_fg}; font-size: {FONT_SMALL}px;"
            f" font-family: {style.font_mono};")
        root.addWidget(self._body)
        # ---- 输出折叠开关（超长输出时出现，见 _FoldMixin）----
        fold_btn = self._fold_init(style, style.panel, OUT_FOLD_TEXT, icon_provider)
        self._fold_holder = QWidget(self)
        hl = QHBoxLayout(self._fold_holder)
        hl.setContentsMargins(CMD_PAD_H, FOLD_GAP, CMD_PAD_H, CMD_PAD_V)
        hl.setSpacing(0)
        hl.addStretch(1)
        hl.addWidget(fold_btn)
        hl.addStretch(1)
        self._fold_holder.hide()
        root.addWidget(self._fold_holder)
        self._fix_vertical()
        # 命令块正文坐在 panel 底上；浮现覆盖「命令 + 输出」两段（标题栏三点不参与）
        self._emerge_init(style.band or style.panel, self._emerge_area)

    # ---------- 折叠钩子（见 _FoldMixin） ----------
    def _fold_label(self) -> QLabel:
        """遮罩贴在最下方那段（命令在上、输出在下）"""
        return self._body

    def _fold_targets(self) -> list:
        """「`$ 命令`」与「输出」同属一个折叠区，顺序即 `_fold_set_parts` 的顺序"""
        return [self._cmd, self._body]

    def _fold_lines(self) -> int:
        return OUT_FOLD_LINES

    def _fold_preview(self) -> int:
        return OUT_PREVIEW_CHARS

    def _fold_gap(self) -> int:
        return FOLD_GAP + CMD_PAD_V   # 开关外壳的上 / 下内边距都要计入

    def _fold_avail_w(self, width: int = None) -> int:
        return max(1, int(width) - 2) if width else self._inner_w()

    def _fold_on_state(self, foldable: bool):
        """开关外壳只在可折叠时占位（不可折叠时命令块恢复为原样）"""
        self._fold_holder.setVisible(foldable)

    def _emerge_area(self) -> QRect:
        """命令块正文区域：`$ 命令` 与输出两段的并集（标题栏不参与浮现）。

        左右各让出 2px：正文标签铺满块宽（内边距在标签内部的 QSS 里），不让出就会把
        左右 1px 描边一起遮掉，浮现期间边框出现缺口。
        """
        rects = [lbl.geometry() for lbl in (self._cmd, self._body) if not lbl.isHidden()]
        if not rects:
            return QRect()
        r = rects[0]
        for g in rects[1:]:
            r = r.united(g)
        h = sum(_label_hfw(lbl, self._inner_w(), self._content_ver)
                for lbl in (self._cmd, self._body) if not lbl.isHidden())
        inset = 2
        return QRect(r.x() + inset, r.y(), max(1, r.width() - inset * 2), int(h))

    def set_content(self, label: str, cmd_html: str, out_html: str, defer: bool = False):
        self._bar_label.setText(label or "")
        # 「`$` 命令」与「输出」都交给折叠机制（同一折叠区的两段）：全文存 `_fold_parts`，
        # 各段标签按折叠态铺前缀或全文 —— 长命令与长输出都能「展开全部」看全，而不是
        # 像以前那样命令被 `_RESULT_TRUNCATE` 静默截掉。
        self._fold_set_parts([
            (f'<span style="color:{self._style.accent};">$&nbsp;</span>{cmd_html}'
             if cmd_html else ""),
            (f'<span style="color:{self._style.muted};">ok</span>&nbsp;&nbsp;{out_html}'
             if out_html else ""),
        ])
        if defer:
            self._deferred_show = (bool(cmd_html), bool(out_html))   # 见 ToolCallRow.set_content
        else:
            self._cmd.setVisible(bool(cmd_html))
            self._body.setVisible(bool(out_html))
        self._bump_content()
        if defer:
            self._deferred_fold = True    # 折叠同样会铺富文本：一并推迟到空闲切片
        else:
            self._fold_apply()
        self._emerge_touch()

    def resume_deferred(self):
        """应用被推迟的内层可见性与折叠态（见 ToolCallRow.set_content 的 defer 说明）"""
        st = getattr(self, "_deferred_show", None)
        if st is not None:
            self._deferred_show = None
            self._cmd.setVisible(st[0])
            self._body.setVisible(st[1])
        if getattr(self, "_deferred_fold", False):
            self._deferred_fold = False
            self._fold_apply()

    def _inner_w(self) -> int:
        return max(1, self.width() - 2)

    def _pin_wrapping(self, width: int = None):
        w = int(width) if width else self._inner_w()
        if w <= 0:
            return
        self._pin_w = w
        if not self._cmd.isHidden() or not self._body.isHidden():
            # 命令 + 输出两段：折叠态 = 各自行数上限，展开态 = 全文高度（见 _FoldMixin）
            self._fold_pin(width)

    def heightForWidth(self, width: int) -> int:
        inner = max(1, int(width) - 2)

        h = self.layout().itemAt(0).widget().sizeHint().height() + 2
        if not self._cmd.isHidden() or not self._body.isHidden():
            h += self._fold_visible_h(width)   # 「命令 + 输出」两段各自的可见高度
            h += self._fold_extra_h(width)     # 折叠开关（含间距）：仅可折叠时占高
        return h


class RichBlock(_PinMixin, _EmergeMixin, QWidget):
    """通用富文本段容器（透明无气泡）：承载提问 / 子 Agent / 下载进度 / 截图等既有段，
    内容 HTML 由调用方渲染，链接点击回到面板既有处理链路。"""

    def __init__(self, style: ChatStyle, font_size: int = FONT_BODY,
                 color: str = None, parent: QWidget = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, SPACING_XS, 0, 0)
        root.setSpacing(0)
        self._body = _mk_label(style)
        self._body.setStyleSheet(
            f"background: transparent; color: {color or style.text};"
            f" font-size: {font_size}px; line-height: 1.8;"
            f" font-family: {style.font_ui};")
        root.addWidget(self._body)
        self._fix_vertical()
        # 透明区块：正文直接坐在面板底色上，浮现层据此遮底。
        # 遮罩色必须**不透明**（要盖住旧字）且贴近真实背景：玻璃态下由 style.band 给出
        # 「壁纸平均色压在面板色上」的合成色；直接用 style.bg（纯深色）会在流式落字时
        # 每落一行闪一块黑 —— 这正是「输出正文时有黑色元素瞬间出现」的根因。
        self._emerge_init(style.band or style.bg, self._emerge_area)

    def _emerge_area(self) -> QRect:
        """正文区域：标签实际宽度 × 真实内容高度（流式增长那一帧布局还没跟上时取测量值，
        否则波段会高出一行、新字先闪一下清晰）。"""
        lbl = self._body
        w = max(1, int(lbl.width()) or self.width())
        h = max(int(lbl.height()), _label_hfw(lbl, w, self._content_ver))
        return QRect(lbl.x(), lbl.y(), w, int(h))

    def set_html(self, html: str, defer: bool = False):
        self._body.setText(html or "")
        self._body.setMinimumHeight(0)
        self._bump_content()
        if defer:
            self._deferred_pin = True    # 见 ToolCallRow.set_content 的 defer 说明
        else:
            self._pin_wrapping()
        self._emerge_touch()

    def resume_deferred(self):
        """应用被推迟的高度钉定（见 ToolCallRow.set_content 的 defer 说明）"""
        if not getattr(self, "_deferred_pin", False):
            return
        self._deferred_pin = False
        self._pin_wrapping()

    def heightForWidth(self, width: int) -> int:
        m = self.layout().contentsMargins()
        inner = max(1, int(width) - m.left() - m.right())
        return _label_hfw(self._body, inner, self._content_ver) + m.top() + m.bottom()


class StreamBlock(RichBlock):
    """正文回复（demo .stream .txt）：纯文本、永不参与过程区折叠。

    流式期间由 `_EmergeBand` 呈现「渐变模糊浮现」落字（见该类的实现说明）；是否播放
    由回合的进行中状态经 `set_live` 注入 —— 历史会话重排/切回一律不补动效。
    """

    def __init__(self, style: ChatStyle, parent: QWidget = None):
        super().__init__(style, font_size=FONT_BASE, color=style.text, parent=parent)


class UserBubble(QLabel):
    """用户消息气泡（demo .msg）：深蓝底 + 非对称圆角 18/18/6/18。"""

    def __init__(self, style: ChatStyle, parent: QWidget = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(
            f"background: {style.user_bg}; color: {style.user_fg}; border: none;"
            f" border-top-left-radius: {BUBBLE_RADIUS_USER}px;"
            f" border-top-right-radius: {BUBBLE_RADIUS_USER}px;"
            f" border-bottom-right-radius: {BUBBLE_RADIUS_USER_TAIL}px;"
            f" border-bottom-left-radius: {BUBBLE_RADIUS_USER}px;"
            f" padding: {BUBBLE_PAD_V}px {BUBBLE_PAD_H}px;"
            f" font-family: {style.font_ui}; font-size: {FONT_BASE}px;")


class CostRibbon(QFrame):
    """耗时徽章（demo .cost-ribbon）：骑在回合顶部虚线上，时钟图标 + 耗时文本。

    回合进行中按 RIBBON_TICK_MS 实时刷新，完成后冻结为总耗时（由面板持久化）。
    """

    def __init__(self, style: ChatStyle, icon: QIcon, parent: QWidget = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CostRibbon {{ background: {style.card};"
            f" border: 1px solid {style.border}; border-radius: {RADIUS_PILL}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 12, 4)
        lay.setSpacing(7)
        self._icon = QLabel(self)
        self._icon.setPixmap(icon.pixmap(FONT_SMALL + 1, FONT_SMALL + 1))
        self._icon.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._text = QLabel("")
        self._text.setStyleSheet(
            f"background: transparent; border: none; color: {style.accent};"
            f" font-size: {FONT_SMALL}px; font-weight: 600;"
            f" font-family: {style.font_ui};")
        lay.addWidget(self._text, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(RIBBON_H)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._t0 = 0.0
        self._live = False
        self._timer = QTimer(self)
        self._timer.setInterval(RIBBON_TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, t0: float = None):
        self._t0 = float(t0 or time.time())
        self._live = True
        self._text.setText(fmt_seconds(time.time() - self._t0))
        self.show()
        self._timer.start()

    def freeze(self, seconds: float):
        self._live = False
        self._timer.stop()
        self._text.setText(fmt_seconds(seconds))
        self.show()

    def freeze_unknown(self):
        """拿不到耗时（老会话未落盘）：徽章**仍然常驻**，只显示占位符。

        用户要求「每个 agent 气泡都要有时间计时器样式」：此前无数据即 hide，重启后
        整条回合的计时样式整块消失，视觉上与其他回合不一致。样式常驻、数值待补，
        比时有时无更符合预期。
        """
        self._live = False
        self._timer.stop()
        self._text.setText(RIBBON_UNKNOWN_TEXT)
        self.show()

    def _tick(self):
        self._text.setText(fmt_seconds(time.time() - self._t0))

    @property
    def is_live(self) -> bool:
        return self._live

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self._live:
            self._timer.start()


class _BlockRef:
    """回合内一个区块的引用：控件 + 间距项 + 「是否过程块」分类。

    分类必须随每次渲染刷新：多轮任务里「中间轮次的正文」在后续正文出现后才变成
    过程块（要随过程区一起收起），此时内容签名不变，控件被复用但分类必须更新。
    """

    __slots__ = ("kind", "sig", "widget", "spacer", "is_proc", "h")

    def __init__(self, kind: str, sig, widget: QWidget, spacer: QSpacerItem, is_proc: bool):
        self.kind = kind
        self.sig = sig
        self.widget = widget
        self.spacer = spacer
        self.is_proc = is_proc
        self.h = 0      # 最近一次钉定的块高：增量重排据此用高度差修正回合总额

    def gap(self) -> int:
        return BLOCK_GAP.get(self.kind, SPACING_XS)


class ChatTurn(QWidget):
    """AI 回合容器（demo .ai-turn）。

    结构：上下 1px 虚线分区 + 左侧竖向虚线 + 耗时徽章（骑线）+ 过程区 + 正文 + 系统时间。
    过程区（思考/工具/命令/富文本段，以及多轮任务里非最终的正文）在回合完成后整体
    收起，只留最后一段正文与「查看执行过程」开关（demo `.ai-turn.done .ai-proc`）。
    """

    def __init__(self, style: ChatStyle, icon_provider: IconProvider,
                 parent: QWidget = None):
        super().__init__(parent)
        self._style = style
        self._icon_provider = icon_provider
        self.style_obj = style
        self._link_handler: Optional[Callable[[str], None]] = None
        self._menu_handler: Optional[Callable[[QPoint], None]] = None
        self._items: list = []          # [_BlockRef]，按布局顺序（收起态只含正文块）
        self._blocks_full: list = []    # 完整区块序列（含过程块），展开时按它补建
        self._proc_count = 0            # 完整序列里过程块的个数（决定开关是否出现）
        self._toggle_handler: Optional[Callable[[], None]] = None
        self._block_resize_handler: Optional[Callable[[], None]] = None
        self._done = False
        self._settled = False           # 回合是否已结束过（决定过程区开关是否常驻）
        self._user_open: Optional[bool] = None   # 用户对过程区的手动选择（优先于 done）
        self._live = False
        self._cost: Optional[float] = None
        self._t0 = 0.0
        self._inset = RIBBON_H // 2     # 顶部虚线相对容器顶部的偏移（徽章骑线）
        self._lay_w = -1                # 上次重算高度所用的宽度（去重用）
        self._hfw_cache: Optional[tuple] = None   # (width, height) 回合级高度缓存
        self._lay_min_h = 0              # 内部布局的最小高度（回合高度兜底）
        self._lay_min_dirty = False      # 可见性变化后是否需要重算上面的兜底值
        self._layer_dirty = True        # 布局参数（边距/开关/系统行）变了 → 下次必须全量重排
        self._live_applied: Optional[bool] = None  # 已下发的流式开关（未变则不重复遍历）
        self._reveal_pending: list = []  # 本页待补显的过程块（分片显示，见 _set_proc_visible）
        self._reveal_rest = None         # 尚未分页显示的过程块（None=未分页，[]=已全显示）
        self._proc_dirty: list = []      # 可见性变过、待增量重测的过程块（见 _apply_proc_visible）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._emit_menu(self.mapToGlobal(pos)))

        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(0)
        self._box = QWidget(self)
        self._box_lay = QVBoxLayout(self._box)
        self._box_lay.setContentsMargins(0, self._inset + AI_TURN_PAD_TOP, 0,
                                         AI_TURN_PAD_BOTTOM + AI_TURN_GAP)
        self._box_lay.setSpacing(0)
        self._lay.addWidget(self._box)

        self._ribbon = CostRibbon(style, icon_provider("clock", FONT_SMALL, style.accent), self)
        self._ribbon.hide()

        self._toggle = PillButton(style, TOGGLE_CLOSED_TEXT,
                                  icon_provider("chev", FONT_SMALL, style.accent), parent=self)
        self._toggle.clicked.connect(self._on_toggle)
        self._toggle.hide()
        self._toggle_idx = -1

        # 「继续显示」：长过程区按页给出（见 _PROC_PAGE），本按钮负责拉取下一页
        self._more = PillButton(style, "", dashed=True, parent=self)
        self._more.clicked.connect(self._reveal_page)
        self._more.hide()

        self._sys = QLabel("")          # demo .sys-meta：回合的「开始 → 结束」时间行
        self._sys.setStyleSheet(
            f"background: transparent; color: {style.muted};"
            f" font-size: {FONT_CAPTION}px; font-family: {style.font_mono};")
        self._sys.hide()
        self._box_lay.addWidget(self._sys)

    # ---------- 外部接口 ----------
    def set_link_handler(self, fn: Callable[[str], None]):
        """链接点击回调（折叠/文件路径等）"""
        self._link_handler = fn

    def set_menu_handler(self, fn: Callable[[QPoint], None]):
        """右键菜单回调（入参为全局坐标）"""
        self._menu_handler = fn

    @property
    def cost(self) -> Optional[float]:
        """本回合的总耗时（秒）；进行中/数据缺失为 None（供会话持久化）"""
        return self._cost

    def set_cost(self, seconds: Optional[float]):
        """注入已持久化的耗时（会话恢复）或重置为未知；徽章在下一次 render 生效"""
        self._cost = None if seconds is None else float(seconds)

    def content_labels(self) -> list:
        return [lbl for lbl in self.findChildren(QLabel)
                if lbl.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction]

    def render(self, blocks, *, cost: Optional[float] = None, live: bool = False,
               sys_meta: str = "", done: bool = False):
        """增量渲染：blocks 为 (kind, payload, sig) 序列。

        sig 未变且 kind 相同的段直接复用既有控件（不重建、不重排），因此流式刷新
        只影响内容变化的那一段。回合已结束时过程块**不建控件**（见 `_apply_done`）。
        """
        blocks = list(blocks)
        self._live = bool(live)
        self._blocks_full = blocks
        self._proc_count = sum(1 for kind, payload, _sig in blocks
                               if self._block_proc(kind, payload))
        # 系统时间行**先落位**：耗时徽章在缺少 cost 时要由它推导（老会话兜底），
        # 因此必须先于 _finalize_ribbon 写入，否则读到的是上一轮的时间行。
        show_sys = bool(sys_meta)
        if self._sys.isHidden() == show_sys:
            self._sys.setVisible(show_sys)
            self._layer_dirty = True
        self._sys.setText(sys_meta or "")
        self._finalize_ribbon(cost, live)
        self._apply_done(done)
        self._apply_live()

    def set_toggle_handler(self, fn: Callable[[], None]):
        """过程区展开/收起回调：面板借此重新钉定回合高度（否则收起后会残留
        展开态的高度，正文下方留一大片空白）。"""
        self._toggle_handler = fn

    def set_block_resize_handler(self, fn: Callable[[], None]):
        """块内就地改高度（思考气泡「继续查看」）后的回调：面板据此让消息流重排，
        保证展开的长正文立刻可见、不被滚动区几何遮挡。"""
        self._block_resize_handler = fn

    def _on_block_resize(self):
        """块内就地展开/收起后重钉本回合高度。

        这类变化不体现在内容签名里（`_rebuild_blocks` 不会标 dirty），回合级高度缓存
        还停在旧值 → 必须作废缓存并按当前宽度全量重排，否则长思考展开后被压扁。
        """
        self._hfw_cache = None
        self._layer_dirty = True
        w = self.width()
        if w > 0:
            self.relayout_heights(w)
            self.updateGeometry()
        if self._block_resize_handler is not None:
            self._block_resize_handler()

    # ---------- 过程区收起 / 展开 ----------
    @staticmethod
    def _block_proc(kind: str, payload) -> bool:
        """区块是否属于「过程区」（回合完成后随过程区收起）。

        默认按段类型判定；正文(text)段可由调用方用 payload["proc"] 显式标记为过程 ——
        多轮任务里除**最后一段正文**外，中间轮次的回复文字也属于过程，必须一起收起，
        否则折叠后仍会残留 AI 文字（用户反馈的「过程未完全折叠」）。
        """
        if isinstance(payload, dict) and "proc" in payload:
            return bool(payload["proc"])
        return kind in PROC_KINDS

    def _apply_done(self, done: bool):
        """收敛到目标显隐状态。

        过程块控件**始终创建并保留**，收起时仅隐藏（setVisible(False) + spacer 归零），
        展开时仅显示。旧实现收起时销毁过程块、展开时重建 20+ 个复杂控件，单次展开
        需 700-800ms（控件创建 250ms + 布局级联 500ms），用户感知明显卡顿。保留控件
        后展开退化为 O(n) 次 setVisible + 一次 relayout，实测 <50ms。

        内存代价：隐藏控件不参与布局与绘制，60 回合约 300 个过程块控件，可接受。
        一旦结束过（settled），开关按钮就常驻；用户的手动选择（_user_open）优先于
        传入的 done，避免窗口缩放等重渲染把它又自动收起。
        """
        done = bool(done)
        if done:
            self._settled = True
        if self._user_open is not None:
            done = not self._user_open
        self._done = done
        # 批量操作期间禁重绘：setVisible/setMinimumHeight 各触发一次失效，
        # 禁重绘后中间状态不 paint，结束后一次 update，减少绘制开销
        self.setUpdatesEnabled(False)
        try:
            # 始终按完整序列重建（增量：签名不变的块就地更新，不销毁）
            dirty, structural = self._rebuild_blocks(self._blocks_full)
            # 收起/展开只切换过程块可见性，不重建控件；状态变过的块进增量清单
            self._set_proc_visible(not done)

            show_toggle = self._settled and self._proc_count > 0
            toggle_changed = self._toggle.isHidden() == show_toggle
            if toggle_changed:
                self._toggle.setVisible(show_toggle)
            text = TOGGLE_CLOSED_TEXT if done else TOGGLE_OPEN_TEXT
            if self._toggle.text() != text:
                self._toggle.setText(text)
                self._toggle.setIcon(rotate_icon(
                    self._icon_provider("chev", FONT_SMALL, self._style.accent),
                    0 if done else 180, FONT_SMALL))
            if show_toggle:
                self._place_toggle()
            # 长回合流式刷新的主开销就在这一句：只有内容变化的块（通常 1 个）需要重测时
            # 走增量路径，其余情况（结构增删/边距/开关变化）才全量重排。
            # 过程区可见性切换（展开/收起/继续显示）也走增量：只有状态变过的块需要重测，
            # 未变的块保持原高度（`_hfw_cache` 与 `ref.h` 都还有效）—— 展开长回合时
            # 这一步决定了是「几十毫秒」还是「数百毫秒」。
            if structural or toggle_changed or self._layer_dirty or self._hfw_cache is None:
                self._proc_dirty = []
                self.relayout_heights(monotonic=self._live)
            else:
                pd = self._take_proc_dirty()
                if pd:
                    self._lay_min_dirty = True    # 可见性变了 → 兜底值要重算
                self.relayout_heights(monotonic=self._live,
                                      dirty=(dirty or []) + pd)
        finally:
            self.setUpdatesEnabled(True)
            self.update()

    def _set_proc_visible(self, visible: bool) -> bool:
        """收起/展开过程区：只切换过程块控件与对应 spacer 的可见性，不重建控件。

        QSpacerItem 无 setVisible，用 changeSize(0,0) / changeSize(0,gap) 切换。
        返回「是否真的发生了变化」：**状态没变就绝不 invalidate 布局** —— 流式每 tick
        都调本方法，无条件 invalidate 会让整条回合每帧全量重排（长任务卡顿的主因之一）。

        展开走**分片**（见 `_EXPAND_FIRST_MS` 的说明）：先同步显示一小批，其余进空闲队列；
        长回合（上百个过程块、单块含数万字输出）一次性显示会把主线程占住几十秒。
        """
        if not visible:
            self._reveal_pending.clear()          # 收起：取消未完成的补显与分页
            self._reveal_rest = None
            self._update_more()
            return self._apply_proc_visible([r for r in self._items if r.is_proc], False)
        if self._live:
            # 进行中的回合：过程块要随输出即时出现（新块由 _insert_blocks 直接显示），
            # 这里只做一次廉价对账
            return self._apply_proc_visible([r for r in self._items if r.is_proc], True)
        if self._reveal_pending:
            return False                          # 本页还在补显：不打断
        if self._reveal_rest is None:
            # 首次展开是用户主动交互：先丢弃后台「隐藏块收尾」积压（长会话里上百个，
            # 见 _IdleSpreader.drop_pending），把主线程立刻让给这次点击 —— 否则点击
            # 响应会随着后台队列的进度忽快忽慢。
            _spreader().drop_pending()
            # 待显示集合 = 当前仍隐藏的全部过程块；由 _reveal_page 切出第一页。
            # **不可**在这里先切掉 _PROC_PAGE —— 那会把首页整批丢弃（小回合里表现为
            # 「点了查看执行过程但一个块都没出现」）。
            self._reveal_rest = [r for r in self._items
                                 if r.is_proc and r.widget.isHidden()]
        return self._reveal_page()

    def _reveal_page(self):
        """显示「下一页」过程块：首个分片同步显示（点击即有内容），其余进空闲切片。
        返回本页是否真的改动了可见性（不变则不 invalidate 布局）。"""
        rest = self._reveal_rest or []
        page, self._reveal_rest = rest[:_PROC_PAGE], rest[_PROC_PAGE:]
        if not page:
            self._update_more()
            return False
        changed = self._apply_proc_visible(page[:_REVEAL_CHUNK], True)
        self._reveal_pending = page[_REVEAL_CHUNK:]
        if self._reveal_pending:
            _spreader().push(self._reveal_step)
        self._update_more()
        return changed

    def _update_more(self):
        """「继续显示」按钮的显隐与文案：过程区展开且仍有未显示的过程块时才出现"""
        n = len(self._reveal_rest or [])
        show = bool(n) and self._user_open is True
        self._more.setVisible(show)
        if show:
            self._more.setText(f"继续显示（还有 {n} 步）")
        self._place_toggle()

    def _apply_proc_visible(self, refs: list, visible: bool) -> bool:
        """对给定过程块应用可见性 + 间距；返回是否真的变了（不变则不 invalidate）。

        **切换期间暂停本回合的布局**：单个 `setVisible` 会让父布局立即重排一次
        （隔离实测 13~46ms/次，随回合内子项数增长），长回合里逐块切换就变成秒级
        阻塞。暂停后同样的切换只在末尾付一次重排 —— 期间几何由各块自身的
        heightForWidth 保证不缺不挤，排空后的重排再统一钉准。
        """
        if not refs:
            return False
        lay = self._box_lay
        dirty: list = []
        if _PAUSE_LAYOUT_ON_REVEAL:      # 见常量说明
            lay.setEnabled(False)
        try:
            for ref in refs:
                if visible:
                    # 显示前先补齐该块的可见性收尾（历史回合的隐藏块延迟到建块时只填内容，
                    # 见 ToolCallRow.set_content 的 defer 说明）—— 逐块补齐才能让每次
                    # 主线程占用有界，而不是一次补齐上百块。
                    try:
                        ref.widget.resume_deferred()
                    except RuntimeError:
                        continue                      # 控件已销毁：跳过
                if ref.widget.isHidden() != (not visible):
                    ref.widget.setVisible(visible)
                    dirty.append(ref)
                gap = ref.gap() if visible else 0
                if ref.spacer.sizeHint().height() != gap:
                    ref.spacer.changeSize(0, gap, QSizePolicy.Policy.Minimum,
                                          QSizePolicy.Policy.Fixed)
                    dirty.append(ref)
        finally:
            if _PAUSE_LAYOUT_ON_REVEAL:
                lay.setEnabled(True)
        if dirty:
            # **不整表作废高度缓存**：把状态变过的块记入增量清单，让随后的重排只重测
            # 这些块。整表作废会把上百个未变的块重测一遍 —— 展开长回合时正是这类
            # 全量重排造成了数百毫秒的单次卡顿。
            seen = {id(r) for r in self._proc_dirty}
            self._proc_dirty.extend(r for r in dirty if id(r) not in seen)
            lay.invalidate()
        return bool(dirty)

    def _take_proc_dirty(self) -> list:
        """取走并清空「过程区可见性变化」累积的待重测块（供增量重排）"""
        d = self._proc_dirty
        self._proc_dirty = []
        return d

    def _reveal_step(self):
        """空闲分片：补显下一批过程块；队列排空后统一重排一次（每片有界，不阻塞交互）。

        实测（`scripts/_probe_switch_perf.py`，192 个过程块的长回合）：分片补显期间
        **不能**每片都做回合重排与面板级重排 —— 那会让补显总时长从 ~4s 涨到 ~96s
        （回合越大、面板越重，每次重排越贵）。故分片只切可见性，几何交由队列排空后的
        一次重排统一钉定（期间由 Qt 按 heightForWidth 自排，不会挤压）。
        """
        if self._done or not self._reveal_pending:
            self._reveal_pending.clear()
        else:
            try:
                batch = self._reveal_pending[:_REVEAL_CHUNK]
                del self._reveal_pending[:_REVEAL_CHUNK]
                self._apply_proc_visible(batch, True)
                self.updateGeometry()
            except RuntimeError:
                self._reveal_pending.clear()      # 控件已销毁：停止补显
                return
            if self._reveal_pending:
                _spreader().push(self._reveal_step)
                return
        # 排空：一次性钉准几何，并让面板按新高度重排（滚动区跟手）。
        # 走**增量**（只重测本页新显示的那些块）：整表重测会把上百个未变的块也算一遍。
        # 「钉几何」与「面板跟手」**分两帧**执行：两者各自都可能上百毫秒（回合内上百个
        # 布局项），挤在同一帧里就是一次可感知的卡顿。
        try:
            self.relayout_heights(self.width(), dirty=self._take_proc_dirty())
        except RuntimeError:
            return
        if self._toggle_handler is not None:
            _spreader().push(self._notify_geometry)

    def _notify_geometry(self):
        """回合几何已钉准后通知面板跟手（单独一帧，见 `_reveal_step` 的说明）"""
        if self._toggle_handler is None:
            return
        try:
            self._toggle_handler()
        except RuntimeError:
            pass

    def _on_toggle(self):
        self._user_open = self._done      # 记住用户选择（后续重渲染不再自动收起）
        self._apply_done(not self._done)
        if self._toggle_handler is not None:
            self._toggle_handler()        # 让面板按新高度重新钉定（消除残留空白）

    def _place_toggle(self):
        """把开关（与「继续显示」）放到最后一个过程块之后（demo 中 .proc-ctrl 紧随 .ai-proc）；
        收起态下没有过程块，开关自然落到正文之前。位置未变时不重复插拔，
        避免流式刷新期间反复触发布局失效。"""
        idx = 0
        for i, ref in enumerate(self._items):
            if ref.is_proc:
                idx = i * 2 + 2
        idx = min(idx, max(0, self._box_lay.count() - 1))
        if idx != self._toggle_idx or self._toggle.parent() is not self._box:
            self._toggle_idx = idx
            self._box_lay.removeWidget(self._toggle)
            self._box_lay.insertWidget(idx, self._toggle)
        if self._more.parent() is not self._box:
            self._box_lay.insertWidget(self._box_lay.indexOf(self._toggle) + 1, self._more)

    # ---------- 增量重建 ----------
    def _rebuild_blocks(self, specs: list):
        """按 specs 同步区块结构与内容（头尾匹配 + 中间批量重建）。

        匹配策略：
        1. 头部匹配：从前往后，kind 相同则就地更新（sig 变化时），kind 不同停止。
        2. 尾部匹配：从后往前，同样规则。展开过程区时正文块在末尾，可直接保留，
           避免销毁重建（旧实现从头匹配，第一个 kind 不同就 drop 全部）。
        3. 中间批量插入：头尾之间的差异部分，先批量创建空控件加入布局（获得真实
           宽度），再统一 set_content。逐个 set_content 会每次触发 setMinimumHeight
           → 布局级联失效，是展开卡顿的主因（22 块 785ms → 批量后 <100ms）。

        返回 (dirty, structural)：dirty = 内容变过、需要重测高度的区块引用（通常只有
        正在增长的那一块）；structural = 块数/顺序变了。**只有结构变化才作废回合级
        高度缓存** —— 内容变化由增量重排就地修正总额（见 relayout_heights）。
        """
        specs = list(specs)
        dirty: list = []
        head = 0
        while head < min(len(self._items), len(specs)):
            kind, payload, sig = specs[head]
            ref = self._items[head]
            if ref.kind != kind:
                break
            ref.is_proc = self._block_proc(kind, payload)
            if ref.sig != sig:
                ref.sig = sig
                self._update_block(ref.widget, kind, payload)
                dirty.append(ref)
            head += 1

        tail = 0
        max_tail = min(len(self._items) - head, len(specs) - head)
        while tail < max_tail:
            kind, payload, sig = specs[len(specs) - 1 - tail]
            ref = self._items[len(self._items) - 1 - tail]
            if ref.kind != kind:
                break
            ref.is_proc = self._block_proc(kind, payload)
            if ref.sig != sig:
                ref.sig = sig
                self._update_block(ref.widget, kind, payload)
                dirty.append(ref)
            tail += 1

        structural = False
        drop_end = len(self._items) - tail
        if head < drop_end:
            self._drop_from(head, drop_end)
            structural = True

        insert_end = len(specs) - tail
        new_specs = specs[head:insert_end]
        if new_specs:
            self._insert_blocks(head, new_specs)
            structural = True
        if structural:
            self._hfw_cache = None
        return dirty, structural

    def _drop_from(self, start: int, end: int = None):
        if end is None:
            end = len(self._items)
        for ref in self._items[start:end]:
            self._box_lay.removeWidget(ref.widget)
            self._box_lay.removeItem(ref.spacer)
            ref.widget.setParent(None)
            ref.widget.deleteLater()
        del self._items[start:end]
        self._toggle_idx = -1

    def _insert_blocks(self, index: int, specs: list):
        """批量插入区块：先以 parent=None 创建并赋值（不触发布局级联），
        再批量加入布局，最后由 relayout_heights 统一钉高度。

        逐个 _append_block 时，每个 set_content 立即触发 setMinimumHeight → 布局
        失效级联，下一个控件在布局未稳定时测量，O(n²) 布局计算（22 块 785ms）。
        批量赋值时控件无父布局，setMinimumHeight 只标记自身，加入布局后一次
        relayout 即可，降为 O(n)。
        """
        self.setUpdatesEnabled(False)
        created = []
        for kind, payload, sig in specs:
            wid = self._make_block(kind, parent=None)
            is_proc = self._block_proc(kind, payload)
            # 历史回合（已结束）的过程块默认收起隐藏：内层可见性收尾推迟到空闲切片 ——
            # 隐藏输出的首次显示会为整篇富文本排版，长对话上百块累积成秒级加载阻塞
            # （见 _IdleSpreader）。进行中的回合（流式）不推迟：内容必须即时可见。
            # 若用户在补显完成前就展开，_apply_proc_visible 会逐块就地补齐（幂等）。
            defer = is_proc and self._done
            self._update_block(wid, kind, payload, defer=defer)
            if defer:
                _spreader().push(wid.resume_deferred)
            # 新块先同步流式状态再交付布局：占位/命令这类「一次成型」的块内容在创建时就
            # 已写入，只有此刻就带上 live，随后的首次布局重排才能把整块记为「刚浮现」。
            wid.set_live(self._live)
            self._wire(wid)
            spacer = QSpacerItem(0, BLOCK_GAP.get(kind, SPACING_XS),
                                 QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            created.append((kind, sig, wid, spacer, is_proc))
        for i, (kind, sig, wid, spacer, is_proc) in enumerate(created):
            pos = index * 2 + i * 2
            self._box_lay.insertWidget(pos, wid)
            self._box_lay.insertItem(pos + 1, spacer)
            # 新块必须在紧随其后的重排**之前**就处于「该可见」的状态：Qt 新建的子控件默认
            # 是隐藏的（`isHidden()` 为真），而 relayout_heights 把隐藏块按 0 高跳过 ——
            # 刚插入的这一块高度不计入回合总额，内层布局随即被挤、块又被钳回最小高，
            # 于是后一块骑到前一块上（超长工具输出「遮挡/挤压正文」的成因之一）。
            wid.setVisible(not (is_proc and self._done))
            self._items.insert(index + i, _BlockRef(kind, sig, wid, spacer, is_proc))
        self.setUpdatesEnabled(True)

    def _make_block(self, kind: str, parent: QWidget = None) -> QWidget:
        p = parent if parent is not None else self._box
        if kind in (KIND_THINK, KIND_TOOL, KIND_CMD):
            # 这三类都能就近折叠（思考气泡 / 工具输出 / 命令输出）：展开或收起会就地改高度，
            # 必须回报本回合重钉高度，否则展开后被回合总额压扁、过程块互相遮挡。
            if kind == KIND_THINK:
                b = ThinkBubble(self._style, self._icon_provider, p)
            elif kind == KIND_TOOL:
                b = ToolCallRow(self._style, self._icon_provider, p)
            else:
                b = CmdBlock(self._style, self._icon_provider, p)
            b.set_fold_handler(self._on_block_resize)
            return b
        if kind == KIND_STREAM:
            return StreamBlock(self._style, p)
        return RichBlock(self._style, parent=p)

    def _update_block(self, wid: QWidget, kind: str, payload: dict, defer: bool = False):
        payload = payload or {}
        if kind == KIND_THINK:
            wid.set_content(payload.get("tag", ""), payload.get("body", ""),
                            sid=payload.get("sid"), defer=defer)
        elif kind == KIND_TOOL:
            wid.set_content(payload.get("name", ""), payload.get("meta", ""),
                            payload.get("params") or {}, ico=payload.get("ico"),
                            out=payload.get("out"), tip=payload.get("tip") or "",
                            defer=defer)
        elif kind == KIND_CMD:
            wid.set_content(payload.get("label", ""), payload.get("cmd", ""),
                            payload.get("out", ""), defer=defer)
        else:
            wid.set_html(payload.get("html", ""), defer=defer)

    def _wire(self, wid: QWidget):
        """新块接入链接点击与右键菜单（右键菜单事件按需从子控件冒泡到外层）。"""
        for lbl in wid.findChildren(QLabel):
            flags = lbl.textInteractionFlags()
            if flags != Qt.TextInteractionFlag.NoTextInteraction:
                lbl.setOpenExternalLinks(False)
                lbl.linkActivated.connect(self._emit_link)
                lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                lbl.customContextMenuRequested.connect(
                    lambda _pos, l=lbl: self._emit_menu(l.mapToGlobal(_pos)))

    # ---------- 事件出口 ----------
    def _emit_link(self, url: str):
        if self._link_handler is not None:
            self._link_handler(url)

    def _emit_menu(self, pos: QPoint):
        if self._menu_handler is not None:
            self._menu_handler(pos)

    # ---------- 耗时徽章 ----------
    def _finalize_ribbon(self, cost: Optional[float], live: bool):
        """耗时徽章**常驻**：每个 AI 回合都必须有计时器样式（用户硬性要求）。

        - 进行中（live）：自任务起点实时刷新；
        - 已结束：优先用冻结/持久化的耗时；老会话没有该数据时退化为系统时间行的
          起止差（`cost_from_meta`）；两者都拿不到就显示占位符。
        此前「无耗时即 hide」会让重启加载的历史回合整块丢掉计时样式，与其他回合
        不一致 —— 现在任何情况下都不隐藏徽章。
        """
        if live:
            self._t0 = self._t0 or time.time()
            if not self._ribbon.is_live:
                self._ribbon.start(self._t0)   # 进行中：实时刷新（重复渲染不重启计时）
            self._ensure_inset(True)
            return
        if cost is None:
            cost = self._cost        # 已冻结的耗时保持稳定（重复渲染不闪烁）
        if cost is None:
            cost = cost_from_meta(self._sys.text())
        if cost is not None:
            self._ribbon.freeze(float(cost))
            self._cost = float(cost)
        else:
            self._ribbon.freeze_unknown()
        self._ensure_inset(True)
        self._place_ribbon()         # 徽章由隐藏转常驻：立即就位（resize 之前也要贴住虚线）

    def _ensure_inset(self, has_ribbon: bool):
        inset = RIBBON_H // 2 if has_ribbon else 0
        if inset == self._inset:
            return
        self._inset = inset
        self._box_lay.setContentsMargins(0, inset + AI_TURN_PAD_TOP, 0,
                                         AI_TURN_PAD_BOTTOM + AI_TURN_GAP)
        self._layer_dirty = True    # 边距变了：回合总额必须全量重算（增量会少算/多算边距）

    def relayout_heights(self, width: int = None, monotonic: bool = False,
                         dirty: list = None) -> int:
        """按给定宽度重算「每个块 + 整条回合」的最小高度（唯一入口）。

        这是整套高度的唯一入口，四条设计要点：
        1. **宽度由调用方给**（面板在加入/宽度变化后立刻重算）：测量永不用控件默认
           宽度，避免按偏大高度写回后在正文底部留下空白；
        2. **只写最小高度 + 2px 容差**：只钉下界不会被裁切（设上限才有裁切风险）；
           容差吃掉富文本换行取整造成的 ±1px 抖动，几何因此稳定不闪；
        3. `monotonic=True`（生成中）时高度只增不减：markdown 在流式期间被半解析
           （如代码围栏刚敲了三个反引号）会让换行数瞬间变少，一缩一涨就是可见抖动；
        4. `dirty` 给定时走**增量**路径：只重测这些区块并按高度差修正回合总额 ——
           长回合流式刷新（每 tick 只有增长块变化）把 O(N) 降到 O(1)，这是「区块一多
           就特别卡顿」的主因；结果仍写回 `_hfw_cache`，外层询问高度时直接命中。
        """
        w = int(width or self.width())
        if w <= 0:
            return 0
        m = self._box_lay.contentsMargins()
        # 内容实际铺在 `_box` 上，而 `_box` 又比回合窄一圈外层布局边距（实测左右各 9px）：
        # 按回合宽测量等于把每块都当成宽 18px 的块去测 —— 长文本会少算一行左右（≈15px），
        # 偏小的值经 QLabel（heightForWidth 受最小高钳制）传给外层布局就成了偏小的槽位，
        # 块自身又被 QWidget 钳回最小高 → 后一块骑到前一块上（遮挡/挤压的成因之一）。
        inner = max(1, w - self._outer_pad_w() - m.left() - m.right())
        incremental = (dirty is not None and self._hfw_cache is not None
                       and self._hfw_cache[0] == w)
        if incremental:
            total = self._hfw_cache[1]
            for ref in dirty:
                wdg = ref.widget
                h = 0 if wdg.isHidden() else self._measure_block(ref, inner, monotonic)
                total += h - ref.h
                ref.h = h
        else:
            total = self._outer_pad() + m.top() + m.bottom()
            for ref in self._items:
                wdg = ref.widget
                # 收起态下过程块控件保留但隐藏，跳过其高度计算；
                # 新建控件在父级布局生效前 isHidden() 恒为 True，但新建只发生在展开态
                # （过程块可见），因此不会被误跳。
                h = 0 if wdg.isHidden() else self._measure_block(ref, inner, monotonic)
                ref.h = h
                total += h + ref.spacer.sizeHint().height()
            if self._settled:   # 开关常驻（按 _settled 判定，与 heightForWidth 同口径）
                total += self._toggle.sizeHint().height()
            if not self._more.isHidden():   # 「继续显示」也是布局里的一项，漏算会让回合矮一截
                total += self._more.sizeHint().height()
            if not self._sys.isHidden():
                total += _widget_hfw(self._sys, inner)
        # 兜底用的「布局最小需求」：全量路径、或可见性刚变过（`_lay_min_dirty`）时重算。
        # `minimumSize()` 会遍历全部子项，流式每 tick 都无条件调会把单帧成本顶上去
        # （见 test_stream_emerge_cost），故只在需要时算。
        if (not incremental) or self._lay_min_dirty:
            # 用 `_box.sizeHint()` 而不是 `_box_lay.minimumSize()`：后者被 QLayout 内部
            # 缓存（只在 invalidate 后重算），刚钉完块高时读到的往往是上一轮的值。
            self._lay_min_h = self._box.sizeHint().height()
            self._lay_min_dirty = False
        self._layer_dirty = False
        # 兜底：回合高度必须 ≥ 内部布局的**真实**最小需求。
        # 逐块计账用的是各块的 `heightForWidth`（块自己算），而块内布局的 `minimumSize`
        # 会被**折叠态标签的 minimumHeight**（已钉到折叠上限）抬高 —— 少算时 QVBoxLayout
        # 只能让块溢出容器，视觉上就是「思考气泡 / 工具调用输出 / 命令输出 / 正文互相
        # 重叠」。这里直接以布局自己算出的最小尺寸为准：任何漏项（包括将来新增的行内
        # 元素）都不会再让回合变矮。
        total = max(total, self._outer_pad() + self._lay_min_h)
        # 只在**同一宽度**下沿用「只增不减」：宽度一变（滚动条出现/消失、面板缩放），
        # 旧高度是另一个换行宽度的结果，再拿来当上界就会把它永远钉住 ——
        # 长任务里滚动条随内容增长而出现时尤其明显，表现为正文上下不断有大片空白。
        # 另外小幅收缩（markdown 半解析抖动）继续按只增不减处理，真实收缩（掉超过
        # HEIGHT_SHRINK_TOL）必须放行，否则多余高度会一直挂在回合里变成大片空白。
        if monotonic and self._lay_w == w and self.minimumHeight() - total <= HEIGHT_SHRINK_TOL:
            total = max(total, self.minimumHeight())
        self._lay_w = w
        self._hfw_cache = (w, total)   # 刚算准：外层随后询问高度直接命中，不再全量重测
        wrote = _need_resize(self.minimumHeight(), total)
        if wrote:
            self.setMinimumHeight(total)
        # 外层包裹层（_TurnWrap，由面板接线）按**内容高度**自钉自身高度，必须每次重排后同步
        # （不能只在 wrote 时同步）：最小高度是「只钉下界」的语义，内容收缩时它并不会变小，
        # 只靠它同步的话包裹层在收起过程区后会残留展开态的高度。它若等到事件循环里的
        # LayoutRequest 才同步还会晚一帧，那一帧包裹层偏矮、把刚落地的那行字裁掉十几个像素。
        sync = getattr(self, "_wrap_sync", None)
        if sync is not None:
            sync()
        if _CHAT_DEBUG:
            det = []
            for r in self._items:
                lbl = getattr(r.widget, "_body", None)
                det.append((r.kind, r.widget.height(), r.widget.minimumHeight(),
                            r.widget.heightForWidth(inner),
                            lbl.heightForWidth(inner) if lbl is not None else None,
                            lbl.minimumHeight() if lbl is not None else None,
                            len(lbl.text()) if lbl is not None else None))
            _dbg(f"turn w={w} inner={inner} total={total} min={self.minimumHeight()} "
                 f"h={self.height()} wrote={wrote} live={self._live} "
                 f"blocks=[(kind,bh,bmin,bhfw,lhfw,lmin,ltextlen) ...]={det}")
        return total

    def _measure_block(self, ref: "_BlockRef", inner: int, monotonic: bool) -> int:
        """测量单块高度并按需钉住最小高度（先 pin 内部标签，再量块）。

        供全量/增量两条重排路径共用：增量路径只对内容变化的区块调用它，因此长回合
        流式刷新不再逐块重走一遍测量分派。
        """
        wdg = ref.widget
        pin = getattr(wdg, "_pin_wrapping", None)
        prev_pin_w = getattr(wdg, "_pin_w", -1)   # pin 前先记下上一次的钉定宽度
        if callable(pin):
            pin(inner)          # 先固定内部标签高度，再据此固定块高度
        h = _widget_hfw(wdg, inner)
        # 槽位不得小于块自己算出的最小尺寸：块内布局的 `minimumSize` 会被**折叠态标签的
        # minimumHeight**（已钉到折叠上限）抬高，可能大于 `heightForWidth` 的自算值。
        # 只按 heightForWidth 给槽位时，`setMinimumHeight` 会把块就地撑高而槽位不变 ——
        # 紧随其后的块便骑到它身上（用户看到的「块互相重叠」）。
        try:
            h = max(h, int(wdg.minimumSizeHint().height()))
        except Exception:
            pass
        # 只在**同一宽度**下沿用「只增不减」：宽度变了，旧最小高度是另一个换行宽度的
        # 结果，再拿来当上界就会把它永远钉住（正文上下持续留大片空白）。
        # 真实收缩（掉超过 HEIGHT_SHRINK_TOL）同样放行，只有小幅抖动才继续按只增不减处理。
        prev_min = wdg.minimumHeight()
        if monotonic and prev_pin_w == inner and prev_min - h <= HEIGHT_SHRINK_TOL:
            h = max(h, prev_min)
        # 钉**固定高度**（而不是最小高度）：QVBoxLayout 对「最小值 < 推荐值 < 最大值」的
        # 子项会按可用空间做弹性分配，槽位与实际高度可能对不上 —— 长回合里就表现为块
        # 互相重叠（思考气泡 / 工具输出 / 命令输出 / 正文叠在一起）。把最小与最大都钉成
        # 本块应有高度后，布局只能按该值精确分配。每次重排都会重钉，折叠态/内容变化仍
        # 即时生效。
        if (abs(wdg.minimumHeight() - h) >= _H_DELTA_EPS
                or abs(wdg.maximumHeight() - h) >= _H_DELTA_EPS):
            wdg.setFixedHeight(h)
        return h

    def _apply_live(self):
        """把流式开关下发给区块（三点动画 / 落字浮现都只在回合进行中播放）。

        未变则整段跳过：本方法在每 tick 的 render 里都会被调用，遍历全部区块纯属浪费。
        新块在 `_insert_blocks` 里已带上当时的开关，因此不会漏发。
        """
        if self._live_applied == self._live:
            return
        self._live_applied = self._live
        for ref in self._items:
            # 隐藏的过程块也要同步（否则收起时停在 live=True，展开后又按流式补动画）；
            # DotsLabel/浮现层自身在隐藏时不启动定时器，赋值不会有额外开销。
            if isinstance(ref.widget, _EmergeMixin):
                ref.widget.set_live(self._live)

    @property
    def timing_started(self) -> bool:
        """耗时计时是否已启动（面板据此决定要不要按任务起点起钟）"""
        return bool(self._t0)

    @property
    def started_at(self) -> float:
        """耗时计时起点（0 表示尚未起钟）"""
        return float(self._t0 or 0.0)

    def start_live(self, t0: float = None):
        """任务开始：启动耗时计时（徽章实时刷新）。

        `t0` 给定时用**任务起点**（发送那一刻）而不是「第一个内容事件到达」—— 慢首字、
        派发子 Agent 后长时间无输出这些静默期此前整段被漏掉，正是用户反馈的
        「子 Agent 调用时不计入时长」。
        """
        self._t0 = float(t0 or time.time())

    def finish(self) -> float:
        """任务结束：停止计时并冻结耗时徽章，返回总耗时（秒）。

        重复调用返回已冻结的值（幂等），避免任务收尾路径被多次触发时徽章反复重置。
        """
        if not self._t0:
            return float(self._cost or 0.0)
        seconds = max(0.0, time.time() - self._t0)
        self._t0 = 0.0
        self._cost = seconds
        self._ribbon.freeze(seconds)
        return seconds

    # ---------- 绘制：虚线分区 ----------
    def _dash_bottom_y(self) -> int:
        """下虚线位置：容器底边向上让出 AI_TURN_GAP（demo 的 margin-bottom 30px）"""
        return max(0, self.height() - AI_TURN_GAP - 1)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(_dashed_pen(self._style.dash))
        top = self._inset
        right = max(LEFT_GUTTER, self.width() - 1)
        p.drawLine(LEFT_GUTTER, top, right, top)
        bottom = self._dash_bottom_y()
        p.drawLine(LEFT_GUTTER, bottom, right, bottom)
        # 左侧竖向虚线（demo .vline：top 18px / bottom 8px）
        p.drawLine(0, top + AI_TURN_PAD_TOP, 0, max(top + AI_TURN_PAD_TOP, self.height() - 8))
        p.end()

    def _place_ribbon(self):
        self._ribbon.adjustSize()
        try:
            self._ribbon.move(0, 0)
            self._ribbon.raise_()
        except Exception:
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 宽度变化（面板 resize / 固定宽度变化）→ 按新宽度重算全部高度。
        # 用 _lay_w 去重：写回最小高度触发的 resize 不会再次进来（高度变化不改宽度）。
        w = self.width()
        if w > 0 and w != getattr(self, "_lay_w", -1):
            self._lay_w = w
            self.relayout_heights(w, monotonic=self._live)
        self._place_ribbon()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_ribbon()

    def sizeHint(self) -> QSize:
        """整条回合高度 = 内容在当前宽度下的真实高度（与 heightForWidth 一致）"""
        w = self.width()
        if w <= 0:
            return super().sizeHint()
        return QSize(w, self.heightForWidth(w))

    def heightForWidth(self, width: int) -> int:
        """整条回合在给定宽度下的高度（供消息区钉住最小高度）。

        回合级 (width) 缓存：外层 QVBoxLayout 在布局时会反复询问同一宽度（多轮试探
        + 每次 setMinimumHeight 后的重新布局），无缓存时 22 块被重复测量 9+ 次。
        缓存后同宽度直接返回，展开耗时进一步下降。
        各区块自身的测量走 _label_hfw 缓存：内容与宽度都没变的标签直接返回旧值，
        因此流式刷新时只有正在增长的那一块会真正重排富文本。
        """
        w = max(1, int(width))
        if self._hfw_cache is not None and self._hfw_cache[0] == w:
            return self._hfw_cache[1]
        m = self._box_lay.contentsMargins()
        # 与 relayout_heights 同口径：块的换行高度按 `_box` 的真实内容宽度测（见 _outer_pad_w）
        inner = max(1, w - self._outer_pad_w() - m.left() - m.right())
        h = self._outer_pad() + m.top() + m.bottom()
        for ref in self._items:
            if ref.widget.isHidden():
                continue
            h += _widget_hfw(ref.widget, inner) + ref.spacer.sizeHint().height()
        if self._settled:      # 开关一旦出现就常驻（不按 isHidden 判定，理由同上）
            h += self._toggle.sizeHint().height()
        if not self._more.isHidden():   # 同 `relayout_heights`：「继续显示」也是一项
            h += self._more.sizeHint().height()
        if not self._sys.isHidden():
            h += _widget_hfw(self._sys, inner)
        # 与 `relayout_heights` 同一条兜底，且**必须同口径**（都用 `_lay_min_h`）：
        # 若这里改用实时 `_box.sizeHint()`、那边用缓存值，二者会在流式期间短暂不一致，
        # 包裹层（_TurnWrap）按本方法自钉高度就会比回合高出几十像素（表现为「多出量漂移」）。
        if self._lay_min_h:
            h = max(h, self._outer_pad() + self._lay_min_h)
        self._hfw_cache = (w, h)
        return h

    def _outer_pad(self) -> int:
        """外层 `_lay` 的上下边距之和。

        **必须计入回合高度**：`_lay` 是 Qt 用默认样式边距建的 QVBoxLayout（实测 9/9/9/9），
        它把 `_box` 整体内缩，可高度计算此前只算 `_box_lay` 的边距 —— 于是回合真高比预算
        少一整份（18px），底部内容（最后一块正文、**系统时间行**）被压掉/裁切
        （用户反馈的「时间记录的文字被遮挡」）。宽度无关，故与宽度缓存无关，每次现取即可。
        """
        m = self._lay.contentsMargins()
        return m.top() + m.bottom()

    def _outer_pad_w(self) -> int:
        """外层 `_lay` 的左右边距之和（`_box` 因此比回合窄这一整份）。

        **测量必须扣掉它**：块的换行高度取决于其真实宽度（= 回合宽 − 该边距 − `_box_lay`
        边距），按回合宽去测会系统性偏小，长文本下每块少一行左右（≈15px）；偏小的测量值
        经 `QLabel.heightForWidth`（受最小高钳制）成为外层布局的槽位，而块自身被 QWidget
        钳回最小高 —— 后一块就骑到前一块上（遮挡/挤压的成因之一）。
        """
        m = self._lay.contentsMargins()
        return m.left() + m.right()
