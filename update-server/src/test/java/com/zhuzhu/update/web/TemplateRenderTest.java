package com.zhuzhu.update.web;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.zhuzhu.update.service.ContentService;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.thymeleaf.TemplateEngine;
import org.thymeleaf.context.Context;
import org.thymeleaf.templatemode.TemplateMode;
import org.thymeleaf.templateresolver.FileTemplateResolver;
import org.thymeleaf.spring6.dialect.SpringStandardDialect;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.fail;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 模板渲染冒烟测试。
 *
 * <p>Thymeleaf 的语法 / 表达式错误只会在真实渲染时暴露，一旦上线就是整站 500。
 * 这里用真实模板目录离线渲染全部页面，确保：
 * <ul>
 *   <li>每个页面都能渲染成功；</li>
 *   <li>输出里没有残留的 th: 指令或未解析的 ${...}；</li>
 *   <li>关键 SEO 元素（title / description / canonical / JSON-LD）确实产出。</li>
 * </ul>
 */
class TemplateRenderTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private static TemplateEngine engine;
    private static Path templates;

    @BeforeAll
    static void setUp() {
        templates = Paths.get("src/main/resources/templates").toAbsolutePath();
        assertTrue(Files.isDirectory(templates), "找不到模板目录：" + templates);

        FileTemplateResolver resolver = new FileTemplateResolver();
        resolver.setPrefix(templates.toString() + "/");
        resolver.setSuffix(".html");
        resolver.setTemplateMode(TemplateMode.HTML);
        resolver.setCharacterEncoding("UTF-8");
        resolver.setCacheable(false);

        engine = new TemplateEngine();
        engine.setTemplateResolver(resolver);
        engine.setDialect(new SpringStandardDialect());
    }

    /** 页面模型的三个动态数据（内容 / 总下载量 / 最新版本） */
    private record Model(Map<String, Object> site, long totalDownloads, Map<String, Object> latest) {
    }

    /**
     * 组装页面数据。
     * 指定 -Dpreview.content=&lt;线上 /api/site 抓取结果&gt; 时使用真实线上内容（含老数据结构合并），
     * 否则使用默认内容，供断言使用。
     */
    private static Model buildModel() {
        Map<String, Object> site = ContentService.defaults();
        long totalDownloads = 1234L;
        Map<String, Object> latest = null;

        String liveContent = System.getProperty("preview.content");
        if (liveContent != null && !liveContent.isBlank() && Files.exists(Paths.get(liveContent))) {
            try {
                Map<String, Object> payload = MAPPER.readValue(Files.readString(Paths.get(liveContent)),
                        new TypeReference<Map<String, Object>>() {
                        });
                @SuppressWarnings("unchecked")
                Map<String, Object> stored = (Map<String, Object>) payload.getOrDefault("content", Map.of());
                site = ContentService.merge(ContentService.defaults(), stored);
                totalDownloads = ((Number) payload.getOrDefault("totalDownloads", 1234L)).longValue();
                @SuppressWarnings("unchecked")
                Map<String, Object> liveLatest = (Map<String, Object>) payload.get("latest");
                if (liveLatest != null) {
                    latest = new LinkedHashMap<>(liveLatest);
                    latest.put("notesLines", SitePageController.noteLines(str(liveLatest.get("notes"))));
                    latest.put("sizeText", SitePageController.sizeText(
                            ((Number) liveLatest.getOrDefault("size", 0L)).longValue()));
                }
            } catch (Exception e) {
                throw new IllegalStateException("预览内容解析失败：" + liveContent, e);
            }
        }
        if (latest == null) {
            latest = new LinkedHashMap<>();
            latest.put("id", 1L);
            latest.put("version", "1.0.0");
            latest.put("notes", "- 首个版本\n- 支持应用迁移");
            latest.put("notesLines", SitePageController.noteLines("- 首个版本\n- 支持应用迁移"));
            latest.put("size", 1024L * 1024 * 48);
            latest.put("sizeText", "48.0 MB");
            latest.put("force", false);
            latest.put("url", "/api/update/download/1");
        }
        return new Model(site, totalDownloads, latest);
    }

    /** 该页对应的区块文案（与 SitePageController 的映射一致） */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> section(String pagePath) {
        String key = switch (pagePath) {
            case "/gallery" -> "gallery";
            case "/download" -> "changelog";
            case "/faq" -> "faq";
            default -> "features";
        };
        Object sections = buildModel().site().get("sections");
        if (!(sections instanceof Map<?, ?> map)) {
            return Map.of();
        }
        Object value = map.get(key);
        return value instanceof Map<?, ?> sec ? (Map<String, Object>) sec : Map.of();
    }

    private static String str(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    /**
     * 构造页面模型，取值方式与 SitePageController 保持一致：
     * 页面标题区的标签 / 标题 / 副标题都来自该页对应的区块文案，
     * 这样测试才能真正发现「同一标题在页面上出现两次」之类的问题。
     */
    private static Context context(String pagePath, String seoTitle) {
        Model model = buildModel();
        Map<String, Object> site = model.site();
        Map<String, Object> latest = model.latest();
        long totalDownloads = model.totalDownloads();

        // 注意：版本列表由 versionList() 提供，每个条目都带 date；首条即最新版本
        List<Map<String, Object>> versions = new ArrayList<>();
        versions.add(versionEntry("1.0.0", "48.0 MB", "2026-09-25", false, "1"));
        versions.add(versionEntry("0.9.0", "40.0 MB", "2026-01-01", true, "2"));

        // 示例展示位：优先复用真实主视觉图，避免预览里出现取不到的图片
        String shot = str(site.get("heroImage"));
        if (shot.isBlank()) {
            shot = "/uploads/img/a.png";
        }
        Map<String, Object> gallery = new LinkedHashMap<>();
        gallery.put("url", shot);
        gallery.put("title", str(site.get("title")));
        gallery.put("desc", "产品界面实拍");

        Map<String, Object> video = new LinkedHashMap<>();
        video.put("type", "file");
        video.put("url", "/uploads/video/demo.mp4");
        video.put("poster", shot);
        video.put("title", "演示视频");
        video.put("desc", "三分钟上手");

        Context ctx = new Context();
        ctx.setVariable("site", site);
        ctx.setVariable("base", "https://example.com");
        ctx.setVariable("assetCss", "testcss1");
        ctx.setVariable("assetJs", "testjs01");
        ctx.setVariable("canonical", "https://example.com" + ("/".equals(pagePath) ? "/" : pagePath));
        ctx.setVariable("pagePath", pagePath);
        ctx.setVariable("pageTitle", seoTitle);
        Map<String, Object> pageSection = section(pagePath);
        // 与控制器一致：副标题取区块 sub，标签取区块 tag
        ctx.setVariable("pageDesc", str(pageSection.get("sub")));
        ctx.setVariable("pageSection", str(pageSection.get("title")));
        // 面包屑用独立短名，避免与区块标题同值（线上：面包屑「常见问题」/ 标题「你可能想问」）
        ctx.setVariable("pageCrumb", "测试页");
        ctx.setVariable("pageTag", str(pageSection.get("tag")));
        ctx.setVariable("ogImage", "https://example.com/og/og-cover.png");
        ctx.setVariable("keywords", "关键词");
        ctx.setVariable("iconPaths", iconPaths());
        ctx.setVariable("features", SitePageController.normalizeIcons(site.get("features")));
        ctx.setVariable("navItems", navItems(pagePath));
        ctx.setVariable("latest", latest);
        ctx.setVariable("versions", versions);
        // 首页展示位：优先用真实内容，为空时用示例条目撑起模板分支（渲染测试需要覆盖到）
        ctx.setVariable("galleryPreview", orSample(site.get("gallery"), List.of(gallery)));
        ctx.setVariable("videoPreview", orSample(site.get("videos"), List.of(video)));
        ctx.setVariable("faqPreview", site.get("faq"));
        ctx.setVariable("downloads", totalDownloads);
        ctx.setVariable("downloadsText", String.format(java.util.Locale.US, "%,d", totalDownloads));
        ctx.setVariable("jsonLd", "[]");
        return ctx;
    }

    /** 列表为空时返回示例条目，保证模板分支被渲染到 */
    private static List<?> orSample(Object value, List<?> sample) {
        return value instanceof List<?> list && !list.isEmpty() ? list : sample;
    }

    private static Map<String, String> iconPaths() {
        Map<String, String> m = new LinkedHashMap<>();
        for (String name : Icons.names()) {
            m.put(name, Icons.of(name));
        }
        return m;
    }

    /** 与 SitePageController.versionList() 输出结构一致的版本条目 */
    private static Map<String, Object> versionEntry(String version, String sizeText, String date,
                                                   boolean force, String id) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", Long.valueOf(id));
        m.put("version", version);
        m.put("fileName", "WinAppMigrator-" + version + ".exe");
        m.put("sizeText", sizeText);
        m.put("force", force);
        m.put("date", date);
        m.put("notes", List.of("更新说明一", "更新说明二"));
        m.put("notesLines", List.of("更新说明一", "更新说明二"));
        m.put("url", "/api/update/download/" + id);
        return m;
    }

    private static List<Map<String, String>> navItems(String activePath) {
        List<Map<String, String>> items = new ArrayList<>();
        for (String[] spec : new String[][]{
                {"首页", "/", "", "hero"}, {"功能特性", "/", "#features", "features"},
                {"界面实拍", "/gallery", "", ""}, {"下载安装", "/download", "", ""},
                {"常见问题", "/faq", "", ""}}) {
            Map<String, String> m = new LinkedHashMap<>();
            m.put("label", spec[0]);
            m.put("href", spec[1] + spec[2]);
            m.put("spy", spec[3]);
            m.put("active", spec[2].isEmpty() && spec[1].equals(activePath) ? "active" : "");
            items.add(m);
        }
        return items;
    }

    private static String render(String template, Context ctx) {
        return engine.process(template, ctx);
    }

    /** Thymeleaf 指令形态：th:xxx= ；不能直接搜 "th:"，否则 CSS 里的 width: 会误判 */
    private static final Pattern TH_DIRECTIVE = Pattern.compile("\\bth:[a-zA-Z-]+\\s*=");

    private static void assertCleanOutput(String html, String label) {
        assertNoThymeleafDirectives(html, label);
        assertFalse(html.contains("${"), label + " 输出中残留未解析的表达式" + snippet(html, "${"));
        assertFalse(html.contains("[["), label + " 输出中残留未解析的内联表达式" + snippet(html, "[["));
        assertTrue(html.contains("</html>"), label + " 输出结构不完整");
    }

    /** 匹配完整指令形态而非裸 "th:"：CSS 里的 width: 会让裸匹配误判 */
    private static void assertNoThymeleafDirectives(String html, String label) {
        Matcher m = TH_DIRECTIVE.matcher(html);
        if (!m.find()) {
            return;
        }
        int from = Math.max(0, m.start() - 60);
        int to = Math.min(html.length(), m.end() + 60);
        fail(label + " 输出中残留 Thymeleaf 指令；位置 " + m.start()
                + " 附近：" + html.substring(from, to).replace("\n", " "));
    }

    /** 指出残留内容的位置与上下文，避免只报一句"断言失败" */
    private static String snippet(String html, String needle) {
        int i = html.indexOf(needle);
        if (i < 0) {
            return "";
        }
        int from = Math.max(0, i - 70);
        int to = Math.min(html.length(), i + 70);
        return "；位置 " + i + " 附近：" + html.substring(from, to).replace("\n", " ");
    }

    @Test
    @DisplayName("首页可渲染且产出完整 SEO 头部")
    void indexRenders() {
        String html = render("index", context("/", "WinAppMigrator — 标语"));

        assertCleanOutput(html, "index");
        assertTrue(html.contains("<title>WinAppMigrator — 标语</title>"), "缺少 title");
        assertTrue(html.contains("name=\"description\""), "缺少 description");
        assertTrue(html.contains("rel=\"canonical\""), "缺少 canonical");
        assertTrue(html.contains("application/ld+json"), "缺少结构化数据");
        assertTrue(html.contains("property=\"og:image\""), "缺少 Open Graph 图片");
        assertTrue(html.contains("id=\"featuresGrid\""), "缺少功能特性区块");
        assertTrue(html.contains("id=\"galleryGrid\""), "缺少图片墙");
        assertTrue(html.contains("id=\"videoWall\""), "缺少视频墙");
        assertTrue(html.contains("id=\"lightbox\""), "缺少灯箱");
        assertTrue(html.contains("id=\"videoModal\""), "缺少视频弹层");
        // 样式与脚本必须带内容哈希版本号，否则 CDN 缓存会导致新旧版本混用
        assertTrue(html.contains("/css/site.css?v=testcss1"), "样式未带版本号");
        assertTrue(html.contains("/js/site.js?v=testjs01"), "脚本未带版本号");
        String downloads = String.format(java.util.Locale.US, "%,d", buildModel().totalDownloads());
        assertTrue(html.contains(downloads), "下载量未注入，期望：" + downloads);
    }

    @Test
    @DisplayName("画廊页渲染全部图片与视频")
    void galleryRenders() {
        Context ctx = context("/gallery", "界面实拍 — WinAppMigrator");
        // 给画廊页塞入真实条目
        @SuppressWarnings("unchecked")
        Map<String, Object> site = (Map<String, Object>) ctx.getVariable("site");
        site.put("gallery", List.of(Map.of("url", "/uploads/img/a.png", "title", "主界面", "desc", "迁移面板")));
        site.put("videos", List.of(Map.of("type", "embed", "url", "https://player.example/1",
                "poster", "", "title", "演示", "desc", "外链演示")));

        String html = render("gallery", ctx);

        assertCleanOutput(html, "gallery");
        assertTrue(html.contains("/uploads/img/a.png"), "画廊未渲染图片");
        assertTrue(html.contains("外链播放"), "视频来源标记未按类型渲染");
        assertTrue(html.contains("主界面"), "图片标题未渲染");
    }

    @Test
    @DisplayName("下载页渲染版本时间线")
    void downloadRenders() {
        Context ctx = context("/download", "下载与更新日志 — WinAppMigrator");
        String html = render("download", ctx);

        assertCleanOutput(html, "download");
        assertTrue(html.contains("v1.0.0"), "未渲染最新版本号");
        assertTrue(html.contains("v0.9.0"), "未渲染历史版本");
        assertTrue(html.contains("48.0 MB"), "未渲染安装包体积");
        assertTrue(html.contains("运行环境"), "未渲染系统要求");
        assertTrue(html.contains("强制"), "未渲染强制更新标记");
    }

    @Test
    @DisplayName("FAQ 页渲染全部问答")
    void faqRenders() {
        String html = render("faq", context("/faq", "常见问题 — WinAppMigrator"));

        assertCleanOutput(html, "faq");
        assertTrue(html.contains("<details"), "问答未使用可用性更好的 details 结构");
        assertTrue(html.contains("迁移应用会丢失我的设置和数据吗"), "问答内容未渲染");
    }

    @Test
    @DisplayName("错误页不依赖主模型即可渲染")
    void errorRenders() {
        Context ctx = new Context();
        ctx.setVariable("status", 404);
        ctx.setVariable("title", "页面不存在");
        ctx.setVariable("message", "找不到该地址");
        String html = render("error", ctx);

        assertTrue(html.contains("404"), "错误码未渲染");
        assertTrue(html.contains("页面不存在"));
        assertNoThymeleafDirectives(html, "error");
        // 错误页必须自包含：出错时外部样式与脚本本身可能不可用
        assertFalse(html.contains("<link rel=\"stylesheet\""), "错误页不应依赖外部样式表");
        assertFalse(html.contains("<script src"), "错误页不应依赖外部脚本");
    }

    /**
     * 可选：把四个页面渲染结果落盘，供 tools/preview_server.py 做本地真实预览。
     * 未指定 -Dpreview.dir 时跳过，不影响 CI。
     * -Dpreview.content 指向线上 /api/site 的抓取结果时，预览即真实线上内容。
     */
    @Test
    @DisplayName("子页面的标签 / 标题 / 副标题各只出现一次")
    void pageHeadingsAppearOnce() {
        // 子页面的标题区由 pageHero 承担，页内内容区块不得再输出同一份文案
        assertPageHeadOnce("faq", "/faq", "常见问题 — WinMigrator");
        assertPageHeadOnce("gallery", "/gallery", "界面实拍 — WinMigrator");
        assertPageHeadOnce("download", "/download", "下载与更新日志 — WinMigrator");

        String faqPage = render("faq", context("/faq", "常见问题 — WinMigrator"));
        assertTrue(faqPage.contains("page-title"), "子页面缺少唯一标题元素");
        assertTrue(faqPage.contains("crumb-current"), "子页面缺少面包屑");
        assertEquals(1, countOf(faqPage, "测试页"), "面包屑重复出现");
    }

    private static void assertPageHeadOnce(String view, String path, String seoTitle) {
        Map<String, Object> sec = section(path);
        String body = bodyOf(render(view, context(path, seoTitle)));
        for (String key : new String[]{"tag", "title", "sub"}) {
            String text = str(sec.get(key));
            if (!text.isBlank()) {
                assertEquals(1, countOf(body, text),
                        path + " 正文中区块「" + key + "」出现次数应为 1：" + text);
            }
        }
    }

    /** 只统计可见正文：head 里的 description / og 等重复出现是正常的，HTML 注释也不算 */
    private static String bodyOf(String html) {
        int start = html.indexOf("<body");
        int end = html.lastIndexOf("</body>");
        String body = start >= 0 && end > start ? html.substring(start, end) : html;
        return body.replaceAll("(?s)<!--.*?-->", "");
    }

    @Test
    @DisplayName("首页每个区块的标题各出现一次")
    void homeSectionHeadingsAppearOnce() {
        String home = bodyOf(render("index", context("/", "WinMigrator")));

        for (String title : new String[]{"为迁移与优化而生", "看见真实的样子", "三分钟看懂它", "为什么选择它", "你可能想问"}) {
            assertEquals(1, countOf(home, title), "首页区块标题重复：" + title);
        }
    }

    private static int countOf(String haystack, String needle) {
        int count = 0;
        int index = haystack.indexOf(needle);
        while (index >= 0) {
            count++;
            index = haystack.indexOf(needle, index + needle.length());
        }
        return count;
    }

    @Test
    @DisplayName("生成本地预览产物（需 -Dpreview.dir）")
    void dumpPreview() throws Exception {
        String dir = System.getProperty("preview.dir");
        Assumptions.assumeTrue(dir != null && !dir.isBlank(), "未指定 preview.dir，跳过预览产物生成");

        Path out = Paths.get(dir);
        Files.createDirectories(out);
        Model model = buildModel();

        String[][] pages = {
                {"index", "/", "", ""},
                {"gallery", "/gallery", "界面实拍", "界面实拍"},
                {"download", "/download", "下载与更新日志", "最新版本"},
                {"faq", "/faq", "常见问题", "常见问题"},
        };
        for (String[] page : pages) {
            // 标题按 SitePageController 的规则拼装，保证预览与线上一致
            String name = str(model.site().get("title"));
            String title = page[0].equals("index")
                    ? name + " — " + str(model.site().get("slogan"))
                    : page[2] + " — " + name;
            Context ctx = context(page[1], title);
            Files.writeString(out.resolve(page[0] + ".html"), render(page[0], ctx),
                    java.nio.charset.StandardCharsets.UTF_8);
        }
        Context errCtx = new Context();
        errCtx.setVariable("status", 404);
        errCtx.setVariable("title", "页面不存在");
        errCtx.setVariable("message", "请求的地址不存在。");
        Files.writeString(out.resolve("404.html"), render("error", errCtx),
                java.nio.charset.StandardCharsets.UTF_8);

        // 供本地预览服务回放 /api/site（真实结构，避免前端拿不到实时数据）
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("content", model.site());
        payload.put("totalDownloads", model.totalDownloads());
        payload.put("latest", model.latest());
        Files.writeString(out.resolve("preview-site.json"), MAPPER.writerWithDefaultPrettyPrinter()
                .writeValueAsString(payload), java.nio.charset.StandardCharsets.UTF_8);

        System.out.println("[preview] 已写入 " + out.toAbsolutePath());
    }
}
