"""Design Tokens：与主题无关的几何 / 字阶 / 间距规格。

颜色不属于本模块：由主题色板统一管理（styles.PALETTE / agent_panel._THEMES）。
本模块只定义「不随主题变化」的圆角、字阶与间距常量，任何控件 QSS 均从这里取值，
禁止散落 6px / 10px / 17px 等魔法数值。

第一批接入范围（后续按此映射逐步收敛存量魔法值）：
- 圆角：按钮/输入/列表项 8 → RADIUS_SM；面板/弹出视图 12 → RADIUS_MD；
        聊天气泡 16 → RADIUS_LG；胶囊按钮（高度一半，如 42px 圆钮 21）不属几何档位，保留原值
- 字阶：CAPTION 11 / SMALL 12 / BODY 13 / BASE 14 / TITLE 16 / LARGE 24 / HERO 30
- 间距：XS 4 / SM 8 / MD 12 / LG 16 / XL 24

新增 QSS 一律 import 本模块取值；既有内联样式按上表逐处替换。
"""

# 圆角（px）
RADIUS_XS = 6      # 小型控件：徽标 / 标签 / 勾选框
RADIUS_SM = 8      # 按钮 / 输入框 / 列表项 / 卡片内元素
RADIUS_MD = 12     # 面板 / 弹出视图 / 卡片
RADIUS_LG = 16     # 聊天气泡 / 大圆角容器
RADIUS_PILL = 999  # 胶囊：高度一半以上即视为全圆（按钮 / tag / 徽章）

# 事件流聊天气泡几何（气泡设计规范值，仅几何不含颜色）
BUBBLE_RADIUS_USER = 18     # .msg 非对称圆角：左上/右上/左下 18
BUBBLE_RADIUS_USER_TAIL = 6  # .msg 右下「小尾巴」角 6
BUBBLE_RADIUS_THINK_TAIL = 6  # .think-bubble 左上「小尾巴」角 6
RADIUS_CMD = 10             # .cmd 命令块
RADIUS_TILE = 9             # .tc-icon 图标壳
RADIUS_CHIP = 7             # .kv em 参数 chip
THINK_FOLD_LINES = 5        # 思考过程正文超过该行数即自动折叠
OUT_FOLD_LINES = 12         # 工具/命令的执行结果超过该行数即自动折叠（可「展开全部」）
DASH_PATTERN = (4, 4)       # 虚线线段的「实 4 / 空 4」节奏（px）

# 字阶（px）
FONT_CAPTION = 11  # 最小辅助文字（角标）
FONT_SMALL = 12    # 状态条 / tooltip / 次级说明
FONT_BODY = 13     # 默认控件文字
FONT_BASE = 14     # 正文基线
FONT_TITLE = 16    # 顶栏标题 / 分区标题
FONT_LARGE = 24    # 主窗口大标题
FONT_HERO = 30     # 欢迎页主标题

# 间距（px）
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

# 动效时长（ms）：hover / 弹层的过渡节奏，统一避免各处手感不一
ANIM_FAST = 120    # 颜色 / 透明度微反馈
ANIM_NORMAL = 200  # 开关 / 滑入滑出
ANIM_SLOW = 320    # 弹层展开 / 大幅位移