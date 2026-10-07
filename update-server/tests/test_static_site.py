#!/usr/bin/env python3
"""官网静态回归校验（零依赖，标准库 unittest）。

覆盖容易在迭代中悄悄坏掉的一致性约束：
  1. Thymeleaf 模板标签配平、片段引用存在；
  2. 前端 JS 引用的 DOM id 在模板 / 页面里真实存在；
  3. 后台字段模式（admin.js SCHEMAS）与页面 data-editor / data-add 一一对应；
  4. 字段模式与后端默认内容结构一致（ContentService.defaults）；
  5. 四色设计规范（不允许出现规范外的十六进制颜色）与「禁止 emoji」；
  6. SEO 资源齐备：favicon / manifest / 分享图 / robots 与 sitemap 端点。

运行：
  python -m unittest discover -s tests -v      （在 update-server 目录下）
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "src/main/resources"
TEMPLATES = RES / "templates"
STATIC = RES / "static"
JAVA = ROOT / "src/main/java/com/zhuzhu/update"

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
# 允许省略闭合标签的块级元素（HTML 规范），模板里偶尔会用到
OPTIONAL_END = {"p", "li", "td", "th", "tr", "option", "dt", "dd", "thead", "tbody"}

# 色系允许的颜色（冷调中性色阶 + 蓝/青双主色 + 三个语义色）
# 设计原则（2026-10 重构）：
#   1. 中性色带极轻的冷蓝倾向，不用纯灰也不用纯黑 —— 纯黑纯灰在大面积暗色上
#      显得死板，且两者在真实显示器上都无法还原，视觉上必然发闷。
#   2. 主色为电光蓝，强调色为青绿；两者构成冷色双轴，强调色只用于 10% 的
#      关键处（焦点环、状态、点缀），滥用会失去力量。
#   3. 语义色（绿/琥珀/玫瑰）仅用于状态表达，不做整块背景，避免界面失焦。
ALLOWED_HEX = {
    # 表面层级：越亮越高（暗色模式靠表面色差而非阴影制造深度）
    "#0a0c11", "#11141c", "#181c26", "#202634",
    # 描边与分割用的深色
    "#0b0d12", "#0a0b0d", "#0b0c0f",
    # 文字四级
    "#f0f3f9", "#a8b2c6", "#7c879e",
    # 主色（蓝）与强调色（青）
    "#3d7dff", "#1e4fd0", "#7df9e4",
    # 语义色：成功 / 警告 / 错误
    "#4ade80", "#fbbf24", "#fb7185",
    # 中性（弹层、遮罩、纯白前景）
    "#ffffff", "#fff", "#000000", "#000",
}
# 大写形式同样放行（不同来源的十六进制写法大小写混用）
ALLOWED_HEX |= {c.upper() for c in list(ALLOWED_HEX)}

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u20E3]"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def template_files() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


class TagBalanceParser(HTMLParser):
    """用栈检查标签是否配平；不追求浏览器级的容错。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        while self.stack:
            open_tag, line = self.stack.pop()
            if open_tag == tag:
                return
            if open_tag in OPTIONAL_END:
                continue
            self.errors.append(f"第 {self.getpos()[0]} 行 </{tag}> 与第 {line} 行 <{open_tag}> 不匹配")
            return
        self.errors.append(f"第 {self.getpos()[0]} 行出现多余的 </{tag}>")

    def close_report(self) -> list[str]:
        for tag, line in self.stack:
            self.errors.append(f"第 {line} 行 <{tag}> 未闭合")
        return self.errors


class TestTemplates(unittest.TestCase):
    def test_templates_exist(self) -> None:
        names = {p.name for p in template_files()}
        for expected in ("index.html", "gallery.html", "download.html", "faq.html", "error.html"):
            self.assertIn(expected, names, f"缺少模板 {expected}")

    def test_tags_balanced(self) -> None:
        for path in template_files():
            parser = TagBalanceParser()
            parser.feed(read(path))
            errors = parser.close_report()
            self.assertEqual(errors, [], f"{path.relative_to(ROOT)} 标签不配平：{errors}")

    def test_fragment_references_resolve(self) -> None:
        # 收集每个模板文件中定义的片段名
        defined: dict[str, set[str]] = {}
        for path in template_files():
            content = read(path)
            names = set(re.findall(r'th:fragment="([A-Za-z0-9_]+)', content))
            defined[str(path.relative_to(TEMPLATES).with_suffix("")).replace("\\", "/")] = names

        refs = []
        for path in template_files():
            for ref in re.findall(r"~\{\s*([A-Za-z0-9_/]+)\s*::\s*([A-Za-z0-9_]+)", read(path)):
                refs.append((str(path.relative_to(TEMPLATES)), ref[0], ref[1]))
        self.assertTrue(refs, "未解析到任何片段引用，检查正则是否失效")
        for src, file_key, frag in refs:
            self.assertIn(file_key, defined, f"{src} 引用了不存在的模板 {file_key}")
            self.assertIn(frag, defined[file_key], f"{src} 引用了 {file_key} 中不存在的片段 {frag}")

    def test_static_assets_referenced_exist(self) -> None:
        # 由服务端动态生成的路径（不落地为静态文件）
        dynamic = {"/site.webmanifest", "/sitemap.xml", "/robots.txt"}
        controller = read(JAVA / "web/SitePageController.java")

        wanted = ["/css/site.css", "/js/site.js", "/favicon.ico", "/favicon.svg",
                  "/apple-touch-icon.png", "/site.webmanifest"]
        all_templates = "\n".join(read(p) for p in template_files())
        for asset in wanted:
            self.assertIn(asset, all_templates, f"模板未引用 {asset}")
            if asset in dynamic:
                self.assertIn(f'"{asset}"', controller, f"{asset} 未由控制器提供")
                continue
            self.assertTrue((STATIC / asset.lstrip("/")).exists(), f"静态资源缺失：{asset}")

    def test_js_referenced_ids_exist(self) -> None:
        markup = "\n".join(read(p) for p in template_files())
        markup += read(STATIC / "admin/index.html")
        markup_ids = set(re.findall(r'\bid="([^"]+)"', markup))

        missing = []
        for js_name in ("js/site.js", "admin/admin.js"):
            js = read(STATIC / js_name)
            patterns = [r"""\$\('#([A-Za-z0-9_-]+)'""", r"""\$\(\s*["']([A-Za-z0-9_-]+)["']\s*\)"""]
            for pattern in patterns:
                for found in re.findall(pattern, js):
                    if found not in markup_ids:
                        missing.append(f"{js_name} → #{found}")
        self.assertEqual(missing, [], f"脚本引用了不存在的元素 id：{sorted(set(missing))}")

    def test_scroll_spy_targets_exist(self) -> None:
        nav = read(TEMPLATES / "fragments/nav.html")
        self.assertIn("data-spy", nav, "导航缺少滚动监听锚点")
        index = read(TEMPLATES / "index.html")
        blocks = read(TEMPLATES / "fragments/blocks.html")
        ids = set(re.findall(r'id="([^"]+)"', index + blocks))
        # 锚点值在 Java 侧下发，这里校验对应 section 确实存在
        for target in ("features", "gallery", "videos", "stats", "advantages", "faq"):
            self.assertIn(target, ids, f"首页缺少可锚定区块 #{target}")


class TestAdminSchema(unittest.TestCase):
    """后台字段模式与页面、后端结构的一致性。"""

    def setUp(self) -> None:
        self.js = read(STATIC / "admin/admin.js")
        self.html = read(STATIC / "admin/index.html")

    def test_editors_have_schema(self) -> None:
        editors = set(re.findall(r'data-editor="([A-Za-z]+)"', self.html))
        self.assertTrue(editors, "后台页面没有任何列表编辑器")
        for kind in editors:
            self.assertRegex(
                self.js, rf"\b{kind}: \[",
                f"admin.js 的 SCHEMAS 缺少 {kind} 的字段模式",
            )

    def test_add_buttons_have_schema(self) -> None:
        for kind in set(re.findall(r'data-add="([A-Za-z]+)"', self.html)):
            self.assertRegex(self.js, rf"\b{kind}: \[", f"data-add={kind} 没有对应字段模式")

    def test_editor_order_covers_all_schemas(self) -> None:
        order = re.search(r"EDITOR_ORDER\s*=\s*\[([^\]]+)\]", self.js)
        self.assertIsNotNone(order, "未找到 EDITOR_ORDER")
        listed = set(re.findall(r'"([A-Za-z]+)"', order.group(1)))

        schemas_block = re.search(r"var SCHEMAS = \{(.*?)\n  \};", self.js, re.S)
        self.assertIsNotNone(schemas_block, "未找到 SCHEMAS 定义")
        schemas = set(re.findall(r"^    ([A-Za-z]+):\s*\[", schemas_block.group(1), re.M))
        self.assertTrue(schemas, "未能解析出任何字段模式")
        self.assertEqual(schemas - listed, set(), f"字段模式未纳入渲染顺序：{schemas - listed}")
        self.assertEqual(listed - schemas, set(), f"渲染顺序引用了未定义的模式：{listed - schemas}")

    def test_schema_keys_match_backend_defaults(self) -> None:
        content_service = read(JAVA / "service/ContentService.java")
        for key in ("features", "gallery", "videos", "advantages", "faq", "stats", "requirements", "sections"):
            self.assertIn(f'c.put("{key}"', content_service, f"默认内容缺少 {key}")

    def test_media_fields_declare_kind(self) -> None:
        for kind in re.findall(r'data-media-field="([A-Za-z]+)" data-kind="([a-z]+)"', self.html):
            self.assertIn(kind[1], ("image", "video", "auto"), f"未知媒体类型 {kind[1]}")


class TestDesignRules(unittest.TestCase):
    """设计规范：四色体系 + 禁止 emoji。"""

    def test_no_emoji_in_frontend(self) -> None:
        targets = list(template_files()) + [
            STATIC / "css/site.css", STATIC / "js/site.js",
            STATIC / "admin/index.html", STATIC / "admin/admin.js", STATIC / "admin/admin.css",
        ]
        offenders = []
        for path in targets:
            for match in EMOJI_RE.finditer(read(path)):
                offenders.append(f"{path.name}: {match.group(0)!r}")
        self.assertEqual(offenders, [], f"界面文案中不允许出现 emoji：{offenders}")

    def test_site_palette_only(self) -> None:
        css = read(STATIC / "css/site.css")
        colors = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", css))
        illegal = sorted(c for c in colors if c.lower() not in {a.lower() for a in ALLOWED_HEX})
        self.assertEqual(illegal, [], f"site.css 出现四色体系之外的颜色：{illegal}")

    def test_no_native_dialogs(self) -> None:
        """用户偏好页面内提示，禁止浏览器原生 alert / confirm / prompt。"""
        for name in ("js/site.js", "admin/admin.js"):
            code = re.sub(r"//.*", "", read(STATIC / name))
            for bad in ("alert(", "window.prompt(", "confirm("):
                if bad == "confirm(":
                    # 允许自定义确认弹窗函数 confirmDialog / closeConfirm
                    hits = re.findall(r"(?<![A-Za-z])confirm\(", code)
                    self.assertEqual(hits, [], f"{name} 使用了原生 confirm")
                    continue
                self.assertNotIn(bad, code, f"{name} 使用了原生 {bad.rstrip('(')}")


class TestProductRedesign(unittest.TestCase):
    def test_decorative_render_loops_removed(self) -> None:
        """动效体系（2026-10 起）的红线。

        动效本身被鼓励，但实现方式受限：只走 transform / opacity 合成层。
        下列属性与循环会把动画从合成层拖回主线程（重排 / 栅格化 / 逐帧计算），
        一旦引入，移动端滚动就会掉帧，因此由本用例长期看住。
        """
        js = read(STATIC / "js/site.js")
        for removed in ("initStars", "initCursorGlow", "initTilt", "initMagnetic", "initHeroTitle"):
            self.assertNotIn(removed, js, f"重新引入了高成本装饰动效：{removed}")
        css = read(STATIC / "css/site.css")
        for removed in ("backdrop-filter", "text-shadow", "will-change", "radial-gradient"):
            self.assertNotIn(removed, css, f"CSS 引入了性能杀手：{removed}")

    def test_motion_stays_on_compositor(self) -> None:
        """入场动画只允许动 transform / opacity；改 height/top/left 会引起重排。"""
        css = read(STATIC / "css/site.css")
        anchor = "   动效体系\n"
        self.assertIn(anchor, css, "site.css 缺少动效体系区块")
        motion = css[css.index(anchor):]
        for banned in ("transition: height", "transition: top", "transition: left",
                       "transition: width", "transition: all"):
            self.assertNotIn(banned, motion, f"动效体系里出现会触发重排的声明：{banned}")

    def test_ripple_respects_reduced_motion_and_cleans_up(self) -> None:
        """按压涟漪必须响应「减少动效」，且不留下常驻 DOM 节点。"""
        js = read(STATIC / "js/site.js")
        self.assertIn("function initRipple", js)
        body = js[js.index("function initRipple"):]
        body = body[:body.index("\n  function ")]
        self.assertIn("if (REDUCED) { return; }", body, "涟漪未响应减少动效偏好")
        self.assertIn("pointerdown", body, "涟漪应监听 pointerdown（比 click 快半拍）")
        self.assertIn("passive: true", body, "pointerdown 未标记 passive")
        self.assertIn("dot.remove()", body, "涟漪元素未清理，会持续累积 DOM")
        self.assertIn("safe('ripple'", js, "涟漪未接入异常隔离初始化")

    def test_type_scale_is_centralized(self) -> None:
        """字阶必须走令牌，且正文不得小于 16px。

        混用 13/14/15/16px 这类相邻字号会让层级糊成一团（muddy hierarchy），
        WCAG 也要求正文字号不低于 16px。数值展示类（版本号、统计值）可例外。
        """
        css = read(STATIC / "css/site.css")
        for token in ("--text-xs:", "--text-sm:", "--text-base:", "--text-lg:", "--text-xl:", "--text-hero:"):
            self.assertIn(token, css, f"缺少字阶令牌 {token}")
        body = css[css.index("* {"):]
        # 除数据展示类外，正文区不允许出现 15px 及以下
        for raw in re.findall(r"font-size: (\d+)px", body):
            if int(raw) <= 15:
                # 仅允许出现在明确的数据/版本展示选择器上
                line_start = body.rfind("\n", 0, body.index(f"font-size: {raw}px"))
                line = body[line_start:body.index("\n", body.index(f"font-size: {raw}px"))]
                self.assertRegex(
                    line, r"\.(num|stat-value|cl-version|dl-num|dl-stat-value|version|code|pre|kbd)\b",
                    f"正文区出现 {raw}px，应改用字阶令牌：{line.strip()[:60]}",
                )

    def test_micro_interactions_present(self) -> None:
        """活力微交互必须在样式表里真实存在，且都带过渡。"""
        css = read(STATIC / "css/site.css")
        for name, sel in (("按压涟漪", ".ripple"), ("链接下划线展开", "background-size"),
                          ("图标微动", ".btn:hover .ico"), ("焦点环", ":focus-visible")):
            self.assertIn(sel, css, f"缺少微交互：{name}")

    def test_motion_degrades_without_js_or_on_reduced_motion(self) -> None:
        """动效绝不能以牺牲可读性为代价。

        入场动画的初始隐藏态必须限定在 html.js 作用域内：脚本未执行时
        head 的兜底会撤回 .js，内容立即恢复可见。reduced-motion 下同样全关。
        """
        css = read(STATIC / "css/site.css")
        self.assertRegex(css, r"html\.js\s+\.reveal", "初始隐藏态未限定在 html.js 作用域，无 JS 时正文会不可见")
        self.assertIn("prefers-reduced-motion", css, "动效体系未响应减少动态效果偏好")
        # prefers-reduced-motion 段落必须把揭示类元素强制拉回可见。
        tail = css[css.rindex("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("opacity: 1 !important", tail, "reduced-motion 下未强制恢复可见度")
        self.assertIn("animation: none !important", tail, "reduced-motion 下未关闭动画")
        js = read(STATIC / "js/site.js")
        self.assertIn("prefers-reduced-motion", js, "JS 侧未响应减少动态效果偏好")

    def test_reveal_observer_unobserves_after_trigger(self) -> None:
        """揭示观察器触发一次后必须 unobserve，否则回调常驻滚动路径。"""
        js = read(STATIC / "js/site.js")
        self.assertIn("io.unobserve(en.target)", js, "揭示观察器未在触发后停止观察")

    def test_parallax_is_limited_to_capable_viewports(self) -> None:
        """微视差必须自行降级：窄屏与触摸指针直接不注册监听。

        移动端滚动时逐帧写 transform 是掉帧的主要来源，视差属于锦上添花，
        因此在低算力场景必须整段关闭，而不是靠 CSS 兜底。
        """
        js = read(STATIC / "js/site.js")
        if "initParallax" not in js:
            return
        body = js[js.index("function initParallax"):]
        body = body[:body.index("\n  function ")]
        self.assertIn("max-width: 800px", body, "视差未在窄屏关闭")
        self.assertIn("pointer: coarse", body, "视差未在触摸设备关闭")
        self.assertIn("passive: true", body, "scroll 监听未标记 passive，会阻塞滚动")

    def test_hero_has_real_content_not_fake_status(self) -> None:
        index = read(TEMPLATES / "index.html")
        self.assertRegex(index, r'<h1[^>]+th:text="\$\{site.slogan\}"')
        for removed in ("迁移引擎就绪", "沙盒防护开启", "starfield", "cursorGlow"):
            self.assertNotIn(removed, index)
        self.assertIn('class="product-overview"', index)
        self.assertIn("${features}", index)

    def test_dialog_keyboard_and_background_isolation(self) -> None:
        js = read(STATIC / "js/site.js")
        self.assertIn("function lockLayer(", js)
        self.assertIn("el.inert = true", js)
        self.assertIn("e.key !== 'Tab'", js)
        self.assertIn("lastFocus.focus()", js)
        self.assertIn("document.createElement('button')", js)
        blocks = read(TEMPLATES / "fragments/blocks.html")
        self.assertRegex(blocks, r'id="lightbox"[^>]+role="dialog"')
        self.assertNotRegex(blocks, r'id="lbStrip"[^>]+aria-hidden="true"')

    def test_palette_has_single_source_in_css(self) -> None:
        """配色只能有一处来源：site.css 的 :root。

        历史上有过「后端默认值 + head.html 内联注入」两处并存的情况。两者都是
        :root 且 head 的 <style> 后加载，会静默覆盖样式表 —— 表现为后台改了配色
        而线上毫无变化。这个用例锁死单一来源，防止悄悄退回两处并存。
        """
        head = read(TEMPLATES / "fragments/head.html")
        css = read(STATIC / "css/site.css")
        # head 里不得再有动态注入配色变量的内联样式块
        self.assertNotIn("th:inline", head, "head.html 又开始内联注入配色，会覆盖 site.css")
        for token in ("--theme-color:", "--bg-color:", "--card-bg:", "--text-color:"):
            self.assertNotIn(token, head, f"head.html 出现配色变量 {token}，配色来源必须唯一")
        # 反之，样式表必须自带完整主色，否则页面会退化成浏览器默认配色
        for token in ("--theme-color:", "--bg-color:", "--card-bg:", "--text-color:",
                      "--surface-3:", "--ok-color:", "--err-color:"):
            self.assertIn(token, css, f"site.css 缺少设计令牌 {token}")
        # theme-color 必须与样式表的底色同源，不能再读后台字段
        self.assertIn('<meta name="theme-color" content="#0A0C11">', head)

    def test_no_bounce_easing_curves(self) -> None:
        """禁用回弹/弹性缓动。

        真实物体停下时是平滑减速，overshoot 曲线（两点式 cubic-bezier 中
        任一控制点超出 0..1）会把注意力吸引到动画本身而非内容，2015 年后
        已被主流设计规范判为廉价感。这里静态拦截。
        """
        css = read(STATIC / "css/site.css")
        for bad in ("ease-spring", "ease-bounce", "cubic-bezier(.34,1.56", "cubic-bezier(.68,-.6",
                    "backwards", "elastic"):
            self.assertNotIn(bad, css, f"出现了回弹/弹性缓动：{bad}")
        # 允许的缓动必须单调（控制点均在 0..1 内）
        for m in re.finditer(r"cubic-bezier\(([^)]+)\)", css):
            for part in m.group(1).split(","):
                try:
                    v = float(part.strip())
                except ValueError:
                    continue
                self.assertTrue(0.0 <= v <= 1.0, f"缓动控制点 {v} 越界，会产生回弹：{m.group(0)}")

    def test_feedback_announces_errors_and_respects_reduced_motion(self) -> None:
        html = read(TEMPLATES / "feedback.html")
        self.assertRegex(html, r'id="feedbackError"[^>]+role="alert"')
        self.assertRegex(html, r'id="queryError"[^>]+role="alert"')
        js = read(STATIC / "js/feedback.js")
        self.assertIn("prefers-reduced-motion", js)
        self.assertIn("aria-busy", js)


class TestSeoAssets(unittest.TestCase):
    def test_manifest_is_generated_from_content(self) -> None:
        """清单由服务端按库内站名生成，避免站名改动后清单与实际不符。"""
        controller = read(JAVA / "web/SitePageController.java")
        self.assertIn('"/site.webmanifest"', controller, "未注册清单路由")
        self.assertIn('m.put("name", str(site.get("title")', controller)
        self.assertFalse((STATIC / "site.webmanifest").exists(),
                         "清单已改为服务端生成，不应再保留静态文件")

    def test_manifest_icons_exist_on_disk(self) -> None:
        controller = read(JAVA / "web/SitePageController.java")
        icons = re.findall(r'icon\("(/[^"]+)"', controller)
        self.assertTrue(icons, "清单未声明任何图标")
        for src in icons:
            self.assertTrue((STATIC / src.lstrip("/")).exists(), f"清单引用的图标不存在：{src}")

    def test_svg_favicon_is_svg(self) -> None:
        svg = read(STATIC / "favicon.svg")
        self.assertIn("<svg", svg)
        self.assertIn("viewBox", svg)

    def test_og_cover_size(self) -> None:
        path = STATIC / "og/og-cover.png"
        self.assertTrue(path.exists(), "缺少社交分享图")
        # PNG 头部：IHDR 宽高位于固定偏移
        head = path.read_bytes()[:24]
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        self.assertEqual((width, height), (1200, 630), f"分享图尺寸异常：{width}x{height}")

    def test_robots_and_sitemap_endpoints(self) -> None:
        controller = read(JAVA / "web/SitePageController.java")
        self.assertIn('"/robots.txt"', controller)
        self.assertIn('"/sitemap.xml"', controller)
        seo = read(JAVA / "web/SeoSupport.java")
        self.assertIn("Sitemap:", seo, "robots.txt 未声明 Sitemap 地址")
        self.assertIn("urlset", seo, "sitemap 未使用标准 urlset 结构")

    def test_ssr_pages_registered(self) -> None:
        controller = read(JAVA / "web/SitePageController.java")
        for path in ('"/"', '"/gallery"', '"/download"', '"/faq"'):
            self.assertIn(path, controller, f"未注册页面路由 {path}")

    def test_sitemap_entries_match_routes(self) -> None:
        controller = read(JAVA / "web/SitePageController.java")
        block = re.search(r"List<SeoSupport\.Entry>\s+entries\s*=\s*List\.of\((.*?)\);", controller, re.S)
        self.assertIsNotNone(block, "未找到 sitemap 条目定义")
        listed = set(re.findall(r'new SeoSupport\.Entry\("([^"]+)"', block.group(1)))

        # 页面路由取自真实的 @GetMapping 注解：sitemap 里不能出现根本没注册的地址
        registered = set()
        for chunk in re.findall(r"@GetMapping\(([^)]*)\)", controller):
            registered.update(re.findall(r'"([/][^"{]*)"', chunk))
        self.assertEqual(listed - registered, set(), "sitemap 含未注册的页面路由")
        # 已注册的页面路由必须全部被收录（/about 曾经漏收）
        self.assertEqual({"/", "/gallery", "/download", "/faq", "/about"} - listed, set(),
                         "存在未收录到 sitemap 的页面路由")

    def test_html_sitemap_page_registered(self) -> None:
        """站点地图要有一个**给人看**的 HTML 页（XML 只给爬虫）。

        背景：原先页脚的「站点地图」直接指向 /sitemap.xml —— 点开是一屏原始 XML。
        不能靠给 XML 套 XSL 美化：Chromium 已弃用并将在 Chrome 158（2026-11-17）
        移除 XSLT，Firefox / WebKit 也宣布跟进。故改为独立 HTML 页。
        """
        controller = read(JAVA / "web/SitePageController.java")
        self.assertIn('@GetMapping("/sitemap")', controller, "未注册 HTML 站点地图路由")
        self.assertIn('model.addAttribute("sitePages"', controller, "未向模板下发条目清单")
        self.assertTrue((TEMPLATES / "sitemap.html").exists(), "缺少 sitemap.html 模板")

    def test_html_sitemap_paths_match_xml_entries(self) -> None:
        """HTML 站点地图与 XML 的路径集合必须一致。

        两边漂移就会出现「页面在人看的索引里、却对爬虫不可见」（或反之），
        而两个清单分别在 Java 的两个地方维护，必须由测试钉住。
        """
        controller = read(JAVA / "web/SitePageController.java")
        html_paths = set(re.findall(r'new SitePage\("([^"]+)"', controller))
        self.assertTrue(html_paths, "未解析到 HTML 站点地图条目（正则可能已失效）")
        block = re.search(r"List<SeoSupport\.Entry>\s+entries\s*=\s*List\.of\((.*?)\);", controller, re.S)
        xml_paths = set(re.findall(r'new SeoSupport\.Entry\("([^"]+)"', block.group(1)))
        self.assertEqual(html_paths, xml_paths,
                         f"HTML 与 XML 站点地图路径不一致：仅 HTML={html_paths - xml_paths}，"
                         f"仅 XML={xml_paths - html_paths}")

    def test_sitemap_links_point_to_html_page(self) -> None:
        """页脚与错误页的「站点地图」入口要指向 HTML 页，而不是原始 XML。"""
        for name in ("fragments/blocks.html", "error.html"):
            self.assertIn('href="/sitemap"', read(TEMPLATES / name),
                          f"{name} 的站点地图链接未指向 HTML 页")

    def test_head_declares_og_image_metadata(self) -> None:
        """分享卡片需要宽高与替代文本；宽高只在品牌封面下声明，且必须与封面实际尺寸一致。"""
        head = read(TEMPLATES / "fragments/head.html")
        self.assertRegex(head, r'property="og:image:width" content="(\d+)"')
        self.assertRegex(head, r'property="og:image:height" content="(\d+)"')
        self.assertIn('property="og:image:alt"', head, "缺少分享图替代文本")

        declared = (
            int(re.search(r'property="og:image:width" content="(\d+)"', head).group(1)),
            int(re.search(r'property="og:image:height" content="(\d+)"', head).group(1)),
        )
        raw = (STATIC / "og/og-cover.png").read_bytes()[:24]
        actual = (int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"))
        self.assertEqual(declared, actual, f"声明的分享图尺寸与文件不符：{declared} vs {actual}")

    def test_head_declares_sitemap(self) -> None:
        head = read(TEMPLATES / "fragments/head.html")
        self.assertIn('rel="sitemap"', head, "head 未声明站点地图，抓取方需要额外发现路径")

    def test_default_seo_covers_both_brands(self) -> None:
        """默认 SEO 内容必须同时覆盖两个品牌名，否则只改名不换词根就等于放弃一半流量。"""
        content = read(JAVA / "service/ContentService.java")
        keywords = re.search(r'm\.put\("keywords",\s*"([^"]+)"\)', content)
        self.assertIsNotNone(keywords, "默认内容缺少 seo.keywords")
        for brand in ("zhuzhu Copilot", "WinAppMigrator"):
            self.assertIn(brand, keywords.group(1), f"默认关键词未覆盖品牌 {brand}")
        aliases = re.search(r'm\.put\("alternateNames",\s*"([^"]*)"\)', content)
        self.assertIsNotNone(aliases, "默认内容缺少 seo.alternateNames（结构化数据的副品牌名）")
        self.assertIn("WinAppMigrator", aliases.group(1))


class TestSiteTimestamps(unittest.TestCase):
    """站点公开时间戳：内容 / 版本 / 媒体库三个口径必须同时出现在页脚与 /api/site。

    三个口径分散在三张表 / 目录里，最容易被「只改一处」破坏 ——
    例如页面加了一项而 API 没加，或媒体库把安装包也算成媒体。这里逐条钉住。
    """

    ATTRS = ("stampContentUpdated", "stampLatestRelease", "stampMediaUpdated")

    def test_site_api_exposes_timestamps(self) -> None:
        controller = read(JAVA / "web/SiteController.java")
        self.assertIn('out.put("timestamps"', controller, "/api/site 未暴露站点时间戳")
        self.assertIn("SiteTimestampService", controller, "接口未接入统一时间戳服务")

    def test_timestamps_come_from_single_source(self) -> None:
        service = read(JAVA / "service/SiteTimestampService.java")
        for key in ("siteContentUpdatedAt", "latestReleaseDate", "mediaLibraryUpdatedAt"):
            self.assertIn(f'"{key}"', service, f"时间戳快照缺少对外字段 {key}")
        # 三个口径都必须来自既有服务，而不是在时间戳服务里各自重算一遍
        for dep in ("contentService.updatedAt()", "updateService.latest()",
                    "storageService.latestMediaUpdate()"):
            self.assertIn(dep, service, f"时间戳服务未接入 {dep}")

    def test_footer_shows_three_stamps(self) -> None:
        blocks = read(TEMPLATES / "fragments/blocks.html")
        # 标签刻意不含「最新版本」——下载页区块标题同名，重复会被渲染用例判为标题重复
        for label in ("站点内容更新", "版本发布日期", "媒体库更新"):
            self.assertIn(label, blocks, f"页脚缺少时间戳「{label}」")
        for attr in self.ATTRS:
            self.assertIn(attr, blocks, f"页脚未引用 {attr}")

    def test_page_controller_supplies_stamps(self) -> None:
        controller = read(JAVA / "web/SitePageController.java")
        for attr in self.ATTRS:
            self.assertIn(f'model.addAttribute("{attr}"', controller,
                          f"页面模型未下发 {attr}，页脚会整条消失")

    def test_media_library_excludes_packages(self) -> None:
        """媒体库更新时间只统计对外展示的图片与视频；安装包不是媒体。"""
        storage = read(JAVA / "service/StorageService.java")
        self.assertRegex(storage, r"MEDIA_KINDS\s*=\s*List\.of\(Kind\.IMAGE,\s*Kind\.VIDEO\)",
                         "媒体分类必须显式限定为图片 + 视频")

    def test_stamps_reuse_footer_palette(self) -> None:
        """时间戳样式必须复用页脚既有排版语言，不引入新的色值。"""
        css = read(STATIC / "css/site.css")
        self.assertIn(".footer-stamps", css, "site.css 未定义 .footer-stamps")

    def test_stamp_labels_avoid_section_title_conflict(self) -> None:
        """页脚标签不得包含任何区块标题。

        渲染用例要求「同一标题在正文中只出现一次」；页脚标签一旦包含区块标题
        （例如「最新版本」+「发布」），计数就会 +1 而被判为重复。
        """
        content = read(JAVA / "service/ContentService.java")
        titles = set(re.findall(r'section\("[^"]*",\s*"([^"]+)"', content))
        self.assertTrue(titles, "未解析到区块标题（正则可能已失效）")

        blocks = read(TEMPLATES / "fragments/blocks.html")
        labels = re.findall(r'class="stamp-label">([^<]+)<', blocks)
        self.assertEqual(len(labels), 3, f"应恰好有三个时间戳标签，实际：{labels}")
        for label in labels:
            for title in titles:
                self.assertNotIn(title, label,
                                 f"时间戳标签「{label}」包含区块标题「{title}」，会导致标题重复计数")


class TestSecurityGuards(unittest.TestCase):
    def test_media_path_traversal_blocked(self) -> None:
        storage = read(JAVA / "service/StorageService.java")
        self.assertIn("resolveSafe", storage)
        self.assertIn("startsWith(root)", storage, "缺少目录穿越防护")

    def test_upload_extension_whitelist(self) -> None:
        storage = read(JAVA / "service/StorageService.java")
        for kind in ("IMAGE", "VIDEO", "PACKAGE"):
            self.assertIn(kind + "(", storage, f"缺少 {kind} 分类")
        self.assertIn("allowed(", storage)

    def test_admin_endpoints_require_auth(self) -> None:
        config = read(JAVA / "config/WebConfig.java")
        self.assertIn('"/admin/api/**"', config, "后台接口未纳入鉴权拦截范围")


class TestEncoding(unittest.TestCase):
    """源文件必须是 UTF-8：Windows 下的编辑器 / 工具很容易把中文写成 GBK，
    一旦混入就会让编译、模板渲染或页面文案出现乱码，因此在 CI 里直接挡住。"""

    TEXT_SUFFIXES = {".java", ".py", ".js", ".css", ".html", ".json", ".yml", ".xml", ".sql", ".md"}
    SKIP_DIRS = {"target", "node_modules", ".git"}

    def test_all_text_sources_are_utf8(self) -> None:
        offenders = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self.TEXT_SUFFIXES:
                continue
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            raw = path.read_bytes()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as e:
                offenders.append(f"{path.relative_to(ROOT)}（{e.start} 字节处）")
        self.assertEqual(offenders, [], f"以下文件不是 UTF-8 编码：{offenders}")

    def test_no_mojibake_markers(self) -> None:
        """UTF-8 被按 GBK 重复解码后会产生「锛」「鍚」「鐢」这类典型乱码字。"""
        markers = ("锛", "鍚", "鐢", "锠", "鈥", "锟斤拷")
        offenders = []
        for name in ("src/main/resources/templates/index.html",
                     "src/main/resources/static/js/site.js",
                     "src/main/resources/static/admin/admin.js",
                     "src/main/resources/static/css/site.css"):
            content = read(ROOT / name)
            for marker in markers:
                if marker in content:
                    offenders.append(f"{name}: {marker}")
        self.assertEqual(offenders, [], f"检测到疑似编码乱码：{offenders}")


class TestCacheCoherence(unittest.TestCase):
    """缓存一致性：线上「样式/脚本失效、整屏空白」的根因就是 HTML 与资源版本错配。

    这组用例把保证机制钉住：
      * 官网 CSS/JS 必须带内容哈希版本号（内容一变 URL 就变）；
      * 后台是静态 HTML 无法拼版本号，因此必须不缓存；
      * 脚本若未执行，正文必须能自动恢复可见（不能留下一屏空白）。
    """

    def test_site_assets_carry_version(self) -> None:
        html = "\n".join(read(p) for p in template_files())
        self.assertIn("/css/site.css?v=' + ${assetCss}", html, "样式未使用内容哈希版本号")
        self.assertIn("/js/site.js?v=' + ${assetJs}", html, "脚本未使用内容哈希版本号")

        bare = re.findall(r'"((?:/css|/js)/[^"?]*)"', html)
        self.assertEqual(bare, [], f"存在不带版本号的静态资源引用：{sorted(set(bare))}")

    def test_templates_render_without_web_context(self) -> None:
        """@{} 链接表达式需要 IWebContext，会让模板无法离线渲染与测试。

        只检查真正的标记，注释里提到该语法不算问题。
        """
        offenders = []
        for path in template_files():
            content = re.sub(r"<!--.*?-->", "", read(path), flags=re.S)
            for line_no, line in enumerate(content.splitlines(), 1):
                if "@{" in line:
                    offenders.append(f"{path.name}:{line_no}")
        self.assertEqual(offenders, [], f"模板中不允许使用 @{{}} 链接表达式：{offenders}")

    def test_admin_assets_are_not_cached(self) -> None:
        cfg = read(JAVA / "config/WebConfig.java")
        self.assertIn('"/admin/**"', cfg, "后台静态资源未单独配置缓存策略")
        self.assertIn("noStore", cfg, "后台静态资源必须不缓存，否则旧 admin.js 会让页面空白")

    def test_resource_locations_match_pattern_prefix(self) -> None:
        """addResourceHandler("/css/**") 会把 /css/ 前缀匹配掉，location 必须指到子目录。

        指向 classpath:/static/ 会让 /css/site.css 去 static/site.css 找文件 → 全站样式 404。
        """
        cfg = read(JAVA / "config/WebConfig.java")
        for sub in ("css", "js", "admin"):
            self.assertIn(f'"classpath:/static/{sub}/"', cfg,
                          f"资源处理器 location 未指到 static/{sub}/ 子目录")
        self.assertNotIn('addResourceLocations("classpath:/static/")', cfg,
                         "location 缺少子目录，会导致该前缀下的资源全部 404")

    def test_missing_script_restores_content_visibility(self) -> None:
        head = read(TEMPLATES / "fragments/head.html")
        js = read(STATIC / "js/site.js")
        self.assertIn("__wmSiteReady", head, "缺少脚本未加载时的可见性兜底")
        self.assertIn("classList.remove('js')", head, "兜底必须撤回 js 标记才能恢复正文可见")
        self.assertIn("__wmSiteReady = true", js, "脚本未声明已执行标记，兜底会误触发")

    def test_admin_script_failure_is_explained(self) -> None:
        html = read(STATIC / "admin/index.html")
        js = read(STATIC / "admin/admin.js")
        self.assertIn("__wmAdminReady", html, "后台缺少脚本未加载的提示")
        self.assertIn("<noscript>", html, "后台缺少禁用 JS 时的说明")
        self.assertIn("__wmAdminReady = true", js, "后台脚本未声明已执行标记")
        self.assertIn("admin.js", html, "提示中应指明是哪个脚本未加载")

    def test_page_html_is_not_cached(self) -> None:
        cfg = read(JAVA / "config/WebConfig.java")
        self.assertIn('"Cache-Control", "no-cache"', cfg, "官网页面 HTML 必须不缓存")
        for path in ('"/gallery"', '"/download"', '"/faq"'):
            self.assertIn(path, cfg, f"页面 {path} 未纳入不缓存范围")

    def test_per_element_init_is_isolated(self) -> None:
        """单个模块初始化异常不得让后续模块（尤其滚动揭示）失效。"""
        js = read(STATIC / "js/site.js")
        self.assertIn("function safe(", js, "缺少初始化异常隔离")
        self.assertIn("safe('reveal'", js, "滚动揭示未做异常隔离")


class TestOverlayVisibility(unittest.TestCase):
    """弹层显隐：作者样式里的 display 会盖掉浏览器默认的 [hidden]{display:none}。

    灯箱与视频弹层是铺满全屏的黑底元素，一旦被误显示就是一层「关不掉的黑色遮罩」，
    页面上除它以外什么都操作不了 —— 必须由全局 [hidden] 规则兜住。
    """

    def test_hidden_attribute_always_wins(self) -> None:
        css = read(STATIC / "css/site.css")
        self.assertRegex(css, r"\[hidden\]\s*\{\s*display:\s*none\s*!important",
                         "site.css 缺少 [hidden] 强制隐藏规则")

    def test_overlays_start_hidden(self) -> None:
        blocks = read(TEMPLATES / "fragments/blocks.html")
        for ident in ("lightbox", "videoModal"):
            tag = re.search(rf'<div class="[^"]*"\s+id="{ident}"[^>]*>', blocks)
            self.assertIsNotNone(tag, f"未找到 #{ident} 弹层")
            self.assertIn("hidden", tag.group(0), f"#{ident} 必须以 hidden 属性初始隐藏")

    def test_hidden_class_has_style(self) -> None:
        """反向情况：模板若用 hidden 类，样式表必须定义它，否则永远显示。"""
        templates = "\n".join(read(p) for p in template_files())
        if re.search(r'class="[^"]*\bhidden\b', templates):
            self.assertIn(".hidden", read(STATIC / "css/site.css"),
                          "模板使用了 hidden 类，但 site.css 未定义 .hidden")

    def test_admin_assets_use_absolute_paths(self) -> None:
        """/admin 是内部转发，浏览器地址栏仍是 /admin：
        相对路径会被解析成 /admin.css 而 404，整个后台样式失效。"""
        html = read(STATIC / "admin/index.html")
        relative = []
        for ref in re.findall(r'(?:href|src)="([^"]+)"', html):
            if ref.startswith(("#", "/", "http://", "https://", "//", "mailto:", "data:")):
                continue
            relative.append(ref)
        self.assertEqual(relative, [], f"后台资源必须用绝对路径：{relative}")

    def test_admin_entry_points_registered(self) -> None:
        cfg = read(JAVA / "config/WebConfig.java")
        for path in ('"/admin"', '"/admin/"'):
            self.assertIn(path, cfg, f"未注册后台入口 {path}")

    def test_admin_hidden_class_has_important(self) -> None:
        """后台同样靠 .hidden 切面板，必须是 !important 才能压过弹层的 display: flex。"""
        css = read(STATIC / "admin/admin.css")
        self.assertRegex(css, r"\.hidden\s*\{\s*display:\s*none\s*!important",
                         "admin.css 的 .hidden 必须是 !important")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
