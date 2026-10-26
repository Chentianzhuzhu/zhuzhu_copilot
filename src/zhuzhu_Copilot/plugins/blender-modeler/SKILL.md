---
name: blender-modeler
description: Blender 低模建模技能：真实驱动本机 Blender（headless 常驻实例 + bpy，无 mock），产出高质量低模——Q 版桌宠（猫/熊/兔）与电路板实物图；基础 PBR 材质、三点布光与深色棚拍、自动取景、Cycles/EEVEE 渲染并直接回图校对；支持 .blend 保存与 glb/fbx/obj/stl 导出。模板与调色板全部数据驱动（presets/*.json），可叠加、可改色、可加新模板。
---

# blender-modeler：Blender 低模建模

本插件把本机 Blender 变成可被 AI 逐步驱动的建模器：`server.py`（MCP stdio）负责发现
`blender.exe` 并拉起**常驻** `blender --background` 进程，所有建模/材质/灯光/渲染都是
**真实 bpy 调用**，没有 mock、没有假预览图。

> **铁律：改完必须渲染看图再交付。** `render_image` 会把 PNG 直接返回给你，
> 用眼睛核对比例、穿模、丝印方向、材质观感。不许「按想象描述结果」。

## 最短路径（先读这里）

| 想做的事 | 一步到位的做法 |
| --- | --- |
| 开工前确认环境 | `blender_status`（只读；`auto_start=true` 顺带拉起后台实例） |
| 从零开始一个干净场景 | `scene_new`（默认纯黑世界背景，符合交付风格） |
| 要一只桌宠 | `template_apply(template="pet", style="cat"/"bear"/"rabbit")` |
| 要一块电路板实物图 | `template_apply(template="pcb")` |
| 不知道模板有什么可调 | `template_list` |
| 加/改一个体块 | `object_add(kind=..., size=..., location=..., shade="flat")` |
| 批量挪/缩放/旋转 | `object_transform(names=[...], relative=true, ...)` |
| 左右对称部件 | `object_duplicate(name=..., mirror="X")` |
| 低模硬表面倒角 | `object_modifier(name=..., modifier="bevel", width=0.01~0.03, segments=2, apply=true)` |
| 电路板开孔、零件挖槽 | `object_boolean(target=..., operand=..., op="DIFFERENCE")` |
| 做材质 | `material_create` → `material_assign(targets=[...], material=...)` |
| 丝印 / 铭牌 | `text_add(body="U1", size=..., location=...)`（自动转网格，导出不丢） |
| 一键布光 | `light_rig(preset="product")` / `preset="dramatic"` |
| 摆相机 | `camera_frame(view="hero")`；渲染时 `render_image(view=...)` 会自己取景 |
| 看图 | `render_image(name=..., resolution=..., samples=...)` |
| 存工程 | `scene_save(name=...)` |
| 交付模型 | `export_model(name=..., format="glb")` |
| 任务结束 | `blender_stop`（释放内存；闲置 30 分钟也会自动退出） |

## 标准流程

1. `blender_status(auto_start=true)` —— 确认版本与输出目录。
2. `scene_new` —— 干净场景（纯黑背景），避免继承残留对象。
3. 建模：`template_apply` 打底（桌宠/电路板），再 `object_add` / `object_transform` /
   `object_modifier` / `object_boolean` 精修；每改 3~5 步就 `scene_info(detail="brief")`
   核对对象名、尺寸、三角面数，不要凭记忆。
4. 材质：`material_create` → `material_assign`。
5. 灯光 + 渲染：`light_rig` → `render_image` → **读图** → 修 → 再渲染。
6. 交付：`scene_save`（.blend 工程）+ `export_model(format="glb")`。
7. `blender_stop`（可选，闲置会自动退出）。

**不要每一步都 `blender_start(restart=true)`**：常驻实例保留场景、材质、相机与渲染缓存，
重启一次就丢全部进度；`restart` 只在确实要推倒重来时用。

## 低模建模要点

- 单位是**米**，**Z 轴朝上**；`size` 是整体最大尺寸（球=直径、立方体=边长、圆柱=高度）。
- 低模用**低分段**：球 `segments` 12~16、圆柱 `vertices` 12~16、圆环 `segments` 16~20。
- 默认 `shade="flat"` 保持硬朗的低模面感；只有曲面零件（球体、圆环）才考虑 `smooth`。
- 硬表面边角加 `bevel`（宽 0.01~0.03 m、2 段），轮廓立刻干净不刮眼。
- 命名用英文短名并统一前缀（如 `pet_`、`pcb_`），后续 `object_transform` / `object_join`
  才好按名批量操作。
- 交付前把同材质的多块合并（`object_join`）能显著降低导出文件的对象数与 draw call。
- 丝印文字**平放在板面**（z 比板面高 0.005~0.01，避免 z-fighting），**不要绕 Z 轴转 180°**
  —— 那会让文字从正面反读；`top` / `hero` 视角本来就是正向的。
- 用 `scene_info(detail="full")` 看包围盒；相机/灯光别手动猜位置，优先 `camera_frame` +
  `light_rig`，它们按包围盒自动缩放。

## 材质（Principled BSDF）

- 颜色一律 `#RRGGBB`；整体色板走**纯黑背景 + 淡灰/白主体 + 深蓝点缀**，禁止 emoji 装饰。
- 哑光塑料/皮肤：`metallic=0`，`roughness=0.55~0.7`。
- 金手指/焊锡：`metallic=1.0`，`roughness=0.25~0.35`。
- 阻焊板：`roughness≈0.5`，深绿 `#123D2B` 一类低饱和色。
- 发光件（LED、屏幕）：`emission_color` + `emission_strength` 3~8，别开太高以免过曝。
- 需要渐变/贴图时才用多材质分区，低模优先「一块一色」。

## 灯光与渲染

- `light_rig(preset="product")`：柔和产品图（三点布光 + 深色哑光地面），默认就用它。
  `dramatic` 是硬边高对比，适合强调轮廓。
- 迭代阶段**低分辨率快出图**：`render_image(resolution=480~640, samples=16)`，
  十几秒一张，看形体和构图。
- 定稿再 `resolution=960~1280, samples=32~64`。
- 默认 `CYCLES`（CPU）+ 降噪；要更快可以 `engine="BLENDER_EEVEE"`。
- 视角：`hero`（右前上方，产品图默认）/ `iso` / `front` / `back` / `left` / `right` /
  `top` / `bottom`；`current` 表示沿用当前相机不动。
- 背景要透明就 `transparent=true`（PNG 带 alpha）。
- 渲染慢时先降采样与分辨率，不要靠模糊去掩盖模型问题。

## 模板：数据驱动，不写死

模板 = `presets/<name>.json`，`template_apply` 的 `template` 参数就是文件名。新增模板
**只加 JSON，不改代码**：

```json
{
  "name": "cup",
  "description": "低模马克杯",
  "root": "CupRoot",
  "scale": 1.0,
  "palette": {"body": "#D8DDE4", "accent": "#1F3A5F"},
  "materials": {"body": {"roughness": 0.65}, "accent": {"roughness": 0.4}},
  "parts": [
    {"name": "body", "kind": "cylinder", "size": 1.0, "loc": [0, 0, 0.5],
     "segments": 16, "bevel": {"width": 0.02, "segments": 2}, "mat": "body"},
    {"name": "handle", "kind": "torus", "size": 0.45, "loc": [0.55, 0, 0.55],
     "rotation": [90, 0, 0], "segments": 16, "mat": "body"},
    {"name": "logo", "kind": "text", "body": "ZHIZHU", "size": 0.12,
     "loc": [0, -0.5, 0.55], "rotation": [90, 0, 0], "mat": "accent"}
  ],
  "styles": {
    "tall": {"parts": [{"name": "extra", "kind": "cylinder", "size": 0.8,
                        "loc": [0, 0, 1.0], "segments": 16, "mat": "body"}]}
  }
}
```

- 支持的 `kind`：`cube / uv_sphere / ico_sphere / cylinder / cone / torus / plane / capsule / text / empty`。
- `repeat: {count, step:[dx,dy,dz]}` 阵列复制（引脚、排针）；`bevel` 倒角；
  `boolean: {target, op}` 开孔/挖槽（operand 用完即删）；`scale` 非等比压扁。
- `styles.<name>.parts` 追加在基础 `parts` 之后；`template_apply(style=...)` 选风格。
- `template_apply(colors={"body": "#8899AA"})` 覆盖调色板；`size` 整体缩放；
  `prefix` 给对象名加前缀（同场景放两只桌宠时必用，否则根对象重名会被顶掉）。
- 内置模板：`pet`（styles: cat / bear / rabbit）、`pcb`。

## 交付与验收

- 输出目录默认 `<桌面>/zhuzhu Copilot/blender`（可用插件配置 `blender_output_dir` 改）。
- 一次完整交付 = 渲染图（看效果）+ `.blend`（可继续改）+ `.glb`（可直接进引擎/网页）。
- 验收清单：比例协调、无穿模、无悬空、丝印正向、材质不过曝、主体在画面内且留白合适。
- 三角面数用 `scene_info` 看；低模单个道具控制在几千面以内，别用 `subsurf` 堆面数。

## 禁则

- 不许伪造渲染结果、不许用文字描述代替看图。
- 不许为了好看堆高模细节（性能 > 美观）：优先低分段、flat 着色、倒角代替细分。
- 不许在脚本/模板里写死路径与颜色（路径走配置，颜色走 `palette`）。
- 不许留 emoji 或装饰性贴图；风格统一为纯黑 + 淡灰 + 白 + 深蓝的简素质感。
