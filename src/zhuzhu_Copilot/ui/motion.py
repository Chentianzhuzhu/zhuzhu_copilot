"""动效性格注册表（客户端 UI/UX 第 5 招：按性格分配动效）

口诀「拖拽瞬间咬死」的落地：不同交互有不同**性格**，曲线与时长必须配对，
不能全站一条 OutCubic 打天下 —— 那是「机关枪乱晃的塑料感」的来源之一。

四种性格（按交互性质选，不按控件类型选）：

| 性格   | 曲线                              | 时长  | 用在哪                                    |
|--------|-----------------------------------|-------|-------------------------------------------|
| 微反馈 | OutCubic（快出慢停，无过冲）       | 110ms | hover 变色 / 透明度微反馈 / 箭头旋转        |
| 咬合   | OutBack（overshoot≈1.05，微弹）    | 240ms | 开关拨动 / 拖拽释放就位 / 徽章弹入          |
| 落定   | OutCubic                          | 240ms | 弹层滑入滑出 / 面板展开收起                 |
| 消退   | InCubic（慢起快走）                | 160ms | 浮层关闭 / 淡出                            |

两条禁令：
1. **重活不弹跳**：大面板/长列表/窗口级形变禁用 OutBack/OutBounce —— 过冲会把
   布局拽出再生回，观感是「晃」不是「弹」；弹跳只留给小控件与点睛时刻（如恢复窗口）。
2. **时长不超 320ms**：超过就是「等动画」，交互反馈一律 ≤240ms；唯一例外是
   流式水墨晕染（agent_chat_bubbles.EMERGE_MS，属内容呈现而非交互反馈）。
"""

from PyQt6.QtCore import QEasingCurve

# ---- 时长（ms）----
HOVER_MS = 110        # 微反馈：颜色 / 透明度
SNAP_MS = 240         # 咬合：开关 / 拖拽释放（含微弹过冲）
SETTLE_MS = 240       # 落定：弹层 / 面板滑入滑出
FADE_OUT_MS = 160     # 消退：浮层关闭淡出
INK_MS = 760          # 水墨晕染（与 agent_chat_bubbles.EMERGE_MS 同值，唯一内容级动效）

# ---- 曲线 ----
# OutBack 过冲量：1.0 + overshoot。1.05 ≈ 末端 5% 回弹 —— 足够「咬死」又不「晃」。
_SNAP_OVERSHOOT = 1.05


def hover() -> QEasingCurve:
    """微反馈性格：快出慢停，无过冲。hover 变色、箭头旋转。"""
    return QEasingCurve(QEasingCurve.Type.OutCubic)


def snap() -> QEasingCurve:
    """咬合性格：微弹落定。开关拨动、拖拽释放就位。"""
    c = QEasingCurve(QEasingCurve.Type.OutBack)
    c.setOvershoot(_SNAP_OVERSHOOT)
    return c


def settle() -> QEasingCurve:
    """落定性格：平滑减速到位。弹层滑入、面板展开。"""
    return QEasingCurve(QEasingCurve.Type.OutCubic)


def fade_out() -> QEasingCurve:
    """消退性格：慢起快走。浮层关闭、淡出。"""
    return QEasingCurve(QEasingCurve.Type.InCubic)


def by_name(name: str) -> QEasingCurve:
    """按名字取曲线（配置/测试用）；未知名字回落「落定」。"""
    return {"hover": hover, "snap": snap, "settle": settle,
            "fade_out": fade_out}.get(str(name), settle)()
