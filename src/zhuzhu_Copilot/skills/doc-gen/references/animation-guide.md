# PPT 动画与切换 参数速查（doc-gen 附册）

> 本文件是 doc-gen skill 的参考素材：AI 生成 PPT 定制动画前应熟读本文档中的示例，
> 字段以 create_pptx 工具的 schema 为准。所有效果名支持中文别名（如 "淡入"=fade）。

## 1. 效果名（入场 / 退场通用）

| 效果名 | 中文 | 观感 |
|---|---|---|
| fade | 淡入/淡出 | 通用、克制，默认轮换池成员 |
| wipe | 擦除 | 轻快，适合要点逐条（可加方向 _down/_up/_left/_right，如 wipe_down） |
| fly | 飞入 | 从下方飞入（_bottom=自下，_top=自上，_left=自左，_right=自右），"fly" 同 fly_bottom |
| zoom | 缩放 | 由小放大（"放大"同义） |
| faded_zoom | 缩放淡入 | 缩放 + 淡入，柔和大图出现 |
| grow_turn | 旋转增长 | 边旋转边放大，发布会感 |
| pinwheel | 风车 | 旋转扫出（"飞旋"同义） |
| swivel | 回旋 | 翻转出现，适合图形 |
| float_up / float_down | 浮入 | 缓慢平移浮现，优雅 |
| float_left / float_right | 平移浮入 | 自左/自右水平平滑滑入（配合 fade 更柔），适合横排要点流 |
| box | 方盒 | 大方块展开，适合图形容器（"揭开"同义） |
| circle | 圆形扩展 | 柔和聚焦，适合卡片/图表（"光圈"同义） |
| diamond | 菱形 | 发布会感 |
| dissolve | 溶解 | 元素从颗粒浮现，适合大图 |
| plus | 加号 | 十字扩展 |
| checkerboard | 棋盘 | 棋盘格逐格显现，活泼 |
| randombar | 随机线条 | 条形扫过，适合数据块 |
| blinds | 百叶窗 | 横向帘幕，适合章节切换感 |
| wedge | 楔入 | 楔形切入 |
| wheel | 风车 | 转轮展开 |
| split | 劈裂 | 从中间裂开（圆角矩形等） |
| strips_upleft | 左上条纹 | 斜向条纹拉开 |
| appear | 出现 | 无过渡直接显示（用于全自动放映） |

- 关闭/不使用某类动画：效果名传 "none"/"off" 或不写该键。
- 同一页不建议超过 2 种不同效果；整套 PPT 效果随页序自动轮换已足够丰富。
- 新效果（fly/zoom/float/grow_turn 等）走 PowerPoint 原生 motion 结构（offset/scale/rotate），
  渲染最流畅；未知效果名会自动回退到 fade，不会报错。

## 2. 三层元素分组（引擎按此自动分层）

| 分组 | 覆盖元素 | 典型顺序 |
|---|---|---|
| title   | 页标题（顶部大标题/封面标题） | 最先 |
| graphics | 卡片底、表格壳、图表柱/图、图片、面板底色 | 其次（先于文字浮层） |
| text    | 正文要点、栏标题、艺术字、图内文字 | 最后 |

默认触发（style.anim_trigger 缺省 "with"）：放映时点一次，标题→图形→正文自动流畅级联
（组内每个元素再级联 anim_stagger=160ms）；想看"逐次点击"传统节奏就设 style.anim_trigger="click"，
"上一次动画结束后自动接"设 "after"。每页动画元素上限 anim.max（默认 12）超出部分直接显示。

## 2.5 退场（清空页面）与节奏调节

| 场景 | 参数 |
|---|---|
| 每页元素按逆序一次点击清场 | style.exit_effect = "fly_bottom" / "fade" / "zoom" … |
| 单页定制退场 | slide.anim.exit = "fade"（或用别名 exit_effect） |
| 退场触发方式 | style.exit_trigger = "click"/"with"/"after"（默认跟随 anim_trigger） |
| 级联间隔 | style.anim_stagger = 120（更快）/ 200（更从容），单位 ms |
| 每页元素上限 | slide.anim.max = 8（页面元素多时避免动画过长） |

退场元素顺序与入场相反（后出现的先走），结构为「animEffect(transition=out) + set(hidden, delay=dur-1)」，
可在 PowerPoint「动画窗格」看到 exit 组；不需要清场的普通内容页建议不加 exit。

## 3. JSON 调用示例

### 3.1 默认开启，仅对个别页关闭
```json
{"path": "out.pptx",
 "title": "发布会",
 "style": {"theme": "black-gold", "transition": "push"},
 "slides": [
   {"title": "目录", "bullets": ["一、回顾", "二、新品"], "anim": false},
   {"title": "三大升级", "cards": [
       {"title": "更快", "desc": "响应时间降低 50%"},
       {"title": "更稳", "desc": "可用性 99.99%"}],
    "anim": {"graphics": "circle"}}
 ]}
```

### 3.2 关键小结页：入场 + 收尾退场
```json
{"title": "本章小结", "anim": {"text": "wipe", "exit": "fade"},
 "bullets": ["统一平台，一套数据", "AI 贯穿高频事务", "三期交付，风险可控"]}
```

### 3.3 图表页逐柱/逐卡链式入场
```json
{"title": "增长趋势",
 "chart": {"type": "column", "labels": ["Q1","Q2","Q3","Q4"],
           "values": [120, 180, 240, 320]},
 "anim": {"graphics": "dissolve"}}
```
引擎会把同一图形组的多个形状在同一"步"内逐个链式显现，形成数据逐项浮出的效果。

### 3.4 全自动放映 / 商务保守场合
```json
"style": {"animation": false}
```
或全部页面仅用 appear 无过渡：
```json
"slides": [{"title": "x", "bullets": ["..."], "anim": {"title": "appear", "text": "appear"}}]
```

### 3.5 发布会感：流畅级联 + 飞入/缩放 + 退场
```json
{"title": "产品亮点",
 "style": {"theme": "dark", "transition": "zoom",
           "animation_effect": "fly_bottom",
           "anim_trigger": "with", "anim_stagger": 140,
           "exit_effect": "fade"},
 "slides": [
   {"title": "三大突破",
    "cards": [{"title": "更快", "desc": "响应时间降低 50%"},
              {"title": "更稳", "desc": "可用性 99.99%"}],
    "anim": {"graphics": "zoom", "text": "wipe_down", "max": 10}}
 ]}
```
说明：animation_effect=fly_bottom 让全篇统一"飞入"；anim_stagger=140 让组内元素每 140ms 级联；
exit_effect=fade 让每页元素按逆序一次点击淡出清场；单页 graphics/text 可再局部覆盖。

## 4. 切换动画一览（style.transition）

fade 淡入 / push 推入 / wipe 擦除 / split 劈裂 / cover 覆盖 / pull 拉出 /
zoom 缩放 / dissolve 溶解 / circle 圆形 / diamond 菱形 / blinds 百叶窗 /
checker 棋盘 / wheel 风车 / comb 梳理 / plus 加号 / newsflash 新闻快报 /
cut 切入 / wedge 楔入 / random 随机。

建议：封面→正文首章用 push 或 zoom；正文页统一 wipe/fade；末章致谢 fade 或 dissolve。
