package com.zhuzhu.update.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.service.AssetVersionService;
import com.zhuzhu.update.service.ContentService;
import com.zhuzhu.update.service.StatsService;
import com.zhuzhu.update.service.UpdateService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 官网页面（服务端渲染）。
 *
 * <p>页面由真实库内内容渲染，搜索引擎无需执行 JS 即可抓到完整正文；
 * 前端脚本只负责刷新「下载量 / 版本」这类实时数字与交互特效。
 */
@Controller
public class SitePageController {

    /** 站点根地址覆盖项；留空则按请求头推导（反向代理场景更准确） */
    @Value("${site.base-url:}")
    private String configuredBaseUrl;

    /** 站点样式与脚本的版本号（内容哈希），避免 CDN 缓存造成新旧错配 */
    private static final String CSS_PATH = "/css/site.css";
    private static final String JS_PATH = "/js/site.js";

    private final ContentService contentService;
    private final StatsService statsService;
    private final UpdateService updateService;
    private final AssetVersionService assets;
    private final ObjectMapper mapper;

    public SitePageController(ContentService contentService, StatsService statsService,
                              UpdateService updateService, AssetVersionService assets,
                              ObjectMapper mapper) {
        this.contentService = contentService;
        this.statsService = statsService;
        this.updateService = updateService;
        this.assets = assets;
        this.mapper = mapper;
    }

    /* ---------------- 页面 ---------------- */

    @GetMapping({"/", "/index.html"})
    public String index(HttpServletRequest req, Model model) {
        Map<String, Object> site = contentService.get();
        String name = str(site.get("title"), "WinAppMigrator");
        String slogan = str(site.get("slogan"), "");
        String desc = str(site.get("description"), slogan);
        return render(req, model, "index", "/", name, "features",
                joinTitle(name, slogan), desc);
    }

    @GetMapping("/gallery")
    public String gallery(HttpServletRequest req, Model model) {
        Map<String, Object> site = contentService.get();
        return render(req, model, "gallery", "/gallery", "界面实拍", "gallery",
                joinTitle("界面实拍", str(site.get("title"), "")),
                sectionSub(site, "gallery", str(site.get("description"), "")));
    }

    @GetMapping("/download")
    public String download(HttpServletRequest req, Model model) {
        Map<String, Object> site = contentService.get();
        return render(req, model, "download", "/download", "下载与更新日志", "changelog",
                joinTitle("下载与更新日志", str(site.get("title"), "")),
                sectionSub(site, "changelog", str(site.get("description"), "")));
    }

    @GetMapping("/faq")
    public String faq(HttpServletRequest req, Model model) {
        Map<String, Object> site = contentService.get();
        return render(req, model, "faq", "/faq", "常见问题", "faq",
                joinTitle("常见问题", str(site.get("title"), "")),
                sectionSub(site, "faq", str(site.get("description"), "")));
    }

    @GetMapping("/about")
    public String about(HttpServletRequest req, Model model) {
        Map<String, Object> site = contentService.get();
        return render(req, model, "about", "/about", "关于我们", "about",
                joinTitle("关于我们", str(site.get("title"), "")),
                "团队介绍与联系方式");
    }

    /* ---------------- SEO 文件 ---------------- */

    @GetMapping(value = "/robots.txt", produces = "text/plain;charset=UTF-8")
    @ResponseBody
    public String robots(HttpServletRequest req) {
        return SeoSupport.robots(baseUrl(req));
    }

    @GetMapping(value = "/sitemap.xml", produces = "application/xml;charset=UTF-8")
    @ResponseBody
    public String sitemap(HttpServletRequest req) {
        String base = baseUrl(req);
        String lastmod = LocalDate.now().toString();
        List<SeoSupport.Entry> entries = List.of(
                new SeoSupport.Entry("/", lastmod, "weekly", 1.0),
                new SeoSupport.Entry("/gallery", lastmod, "weekly", 0.8),
                new SeoSupport.Entry("/download", lastmod, "daily", 0.9),
                new SeoSupport.Entry("/faq", lastmod, "monthly", 0.6),
                new SeoSupport.Entry("/about", lastmod, "monthly", 0.5));
        return SeoSupport.sitemap(base, entries);
    }

    /**
     * PWA 清单：名称与描述取自库内内容，避免站名改动后清单与实际不符。
     * 图标是构建期产物，按固定路径声明。
     */
    @GetMapping(value = "/site.webmanifest", produces = "application/manifest+json;charset=UTF-8")
    @ResponseBody
    public Map<String, Object> manifest() {
        Map<String, Object> site = contentService.get();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("name", str(site.get("title"), "WinAppMigrator"));
        m.put("short_name", str(site.get("title"), "WinAppMigrator"));
        m.put("description", str(site.get("description"), ""));
        m.put("lang", str(seo(site).get("lang"), "zh-CN"));
        m.put("start_url", "/");
        m.put("scope", "/");
        m.put("display", "standalone");
        m.put("background_color", "#000000");
        m.put("theme_color", "#000000");
        List<Map<String, Object>> icons = new ArrayList<>();
        icons.add(icon("/icon-192.png", "192x192", "image/png"));
        icons.add(icon("/icon-512.png", "512x512", "image/png"));
        icons.add(icon("/favicon.svg", "any", "image/svg+xml"));
        m.put("icons", icons);
        return m;
    }

    private static Map<String, Object> icon(String src, String sizes, String type) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("src", src);
        m.put("sizes", sizes);
        m.put("type", type);
        m.put("purpose", "any");
        return m;
    }

    /* ---------------- 渲染与数据装配 ---------------- */

    /**
     * 渲染官网页面。
     *
     * <p>页面标题区（标签 / 标题 / 副标题）统一由 pageHero 片段输出，取值来自该页对应的
     * 区块文案；页内内容区块不再重复输出标题，因此同一标题在页面上只出现一次。
     *
     * @param crumb      面包屑短名（描述性，如「常见问题」）
     * @param sectionKey 该页对应的内容区块键，用于取区块标签 / 标题 / 副标题
     */
    private String render(HttpServletRequest req, Model model, String view, String path,
                          String crumb, String sectionKey, String pageTitle, String pageDesc) {
        Map<String, Object> site = contentService.get();
        String base = baseUrl(req);
        AppVersion latest = updateService.latest();
        long downloads = statsService.totalDownloads();

        model.addAttribute("site", site);
        model.addAttribute("base", base);
        model.addAttribute("assetCss", assets.of(CSS_PATH));
        model.addAttribute("assetJs", assets.of(JS_PATH));
        model.addAttribute("canonical", SeoSupport.canonical(base, path));
        model.addAttribute("pagePath", path);
        model.addAttribute("pageTitle", pageTitle);
        model.addAttribute("pageDesc", pageDesc);
        model.addAttribute("pageCrumb", crumb);
        model.addAttribute("pageTag", sectionTag(site, sectionKey));
        model.addAttribute("pageSection", sectionTitle(site, sectionKey, crumb));
        model.addAttribute("ogImage", absImage(base, ogImage(site)));
        model.addAttribute("keywords", str(seo(site).get("keywords"), ""));
        model.addAttribute("iconPaths", iconPaths());
        model.addAttribute("navItems", navItems(path));
        model.addAttribute("features", normalizeIcons(site.get("features")));
        model.addAttribute("latest", latest == null ? null : SiteController.versionView(latest));
        model.addAttribute("versions", versionList());
        // 首页只展示前若干条，避免长页；完整内容在各自子页面
        model.addAttribute("galleryPreview", preview(site.get("gallery"), 6));
        model.addAttribute("videoPreview", preview(site.get("videos"), 4));
        model.addAttribute("faqPreview", preview(site.get("faq"), 5));
        model.addAttribute("downloads", downloads);
        model.addAttribute("downloadsText", String.format(java.util.Locale.US, "%,d", downloads));
        model.addAttribute("jsonLd", jsonLd(base, path, site, pageTitle, pageDesc, latest));
        return view;
    }

    /** 取列表前 n 条（非列表或空列表返回空列表） */
    static List<?> preview(Object value, int n) {
        if (value instanceof List<?> list) {
            return list.size() <= n ? list : new ArrayList<>(list.subList(0, n));
        }
        return List.of();
    }

    /**
     * 特性图标归一化。
     *
     * <p>历史内容里没有 icon 字段（或填了已下线的图标名），直接交给模板会取到 null。
     * 这里统一回落到白名单内的默认图标，模板侧只需按名取值。
     */
    static List<Map<String, Object>> normalizeIcons(Object features) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (!(features instanceof List<?> list)) {
            return out;
        }
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> map)) {
                continue;
            }
            Map<String, Object> copy = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                copy.put(String.valueOf(e.getKey()), e.getValue());
            }
            Object icon = copy.get("icon");
            String name = icon == null ? "" : String.valueOf(icon);
            copy.put("icon", Icons.supports(name) ? name : Icons.DEFAULT);
            out.add(copy);
        }
        return out;
    }

    /** 全部已发布版本（版本号降序），供下载页时间线使用 */
    private List<Map<String, Object>> versionList() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (AppVersion v : updateService.allSorted()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", v.getId());
            m.put("version", v.getVersion());
            m.put("fileName", v.getFileName());
            m.put("sizeText", sizeText(v.getFileSize()));
            m.put("force", v.isForceUpdate());
            m.put("date", v.getCreatedAt() == null ? "" : v.getCreatedAt().toLocalDate().toString());
            m.put("notes", noteLines(v.getNotes()));
            m.put("url", "/api/update/download/" + v.getId());
            out.add(m);
        }
        return out;
    }

    /** 字节数转可读体积 */
    public static String sizeText(long bytes) {
        if (bytes <= 0) {
            return "";
        }
        double mb = bytes / 1024d / 1024d;
        return mb >= 1024 ? String.format(java.util.Locale.ROOT, "%.2f GB", mb / 1024) : String.format(java.util.Locale.ROOT, "%.1f MB", mb);
    }

    /** 站点根地址：配置优先，其次反向代理请求头，最后请求本身 */    private String baseUrl(HttpServletRequest req) {
        if (configuredBaseUrl != null && !configuredBaseUrl.isBlank()) {
            String v = configuredBaseUrl.trim();
            return v.endsWith("/") ? v.substring(0, v.length() - 1) : v;
        }
        String proto = header(req, "X-Forwarded-Proto", req.getScheme());
        String host = header(req, "X-Forwarded-Host", req.getServerName());
        String port = header(req, "X-Forwarded-Port", String.valueOf(req.getServerPort()));
        return SeoSupport.baseUrl(proto, host, port);
    }

    private static String header(HttpServletRequest req, String name, String fallback) {
        String v = req.getHeader(name);
        if (v == null || v.isBlank() || "null".equalsIgnoreCase(v)) {
            return fallback;
        }
        // 反向代理链可能给出 a, b，取第一个
        int comma = v.indexOf(',');
        return (comma < 0 ? v : v.substring(0, comma)).trim();
    }

    private List<Map<String, String>> navItems(String activePath) {
        List<Map<String, String>> items = new ArrayList<>();
        items.add(nav("首页", "/", "", "hero", activePath));
        items.add(nav("功能特性", "/", "#features", "features", activePath));
        items.add(nav("界面实拍", "/gallery", "", "", activePath));
        items.add(nav("下载安装", "/download", "", "", activePath));
        items.add(nav("常见问题", "/faq", "", "", activePath));
        items.add(nav("关于我们", "/about", "", "", activePath));
        return items;
    }

    private static Map<String, String> nav(String label, String path, String hash, String spy, String activePath) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("href", path + hash);
        m.put("spy", spy);
        // 仅「当前页面本身」高亮：锚点条目不属于独立页面
        m.put("active", hash.isEmpty() && path.equals(activePath) ? "active" : "");
        return m;
    }

    private Map<String, String> iconPaths() {
        Map<String, String> m = new LinkedHashMap<>();
        for (String name : Icons.names()) {
            m.put(name, Icons.of(name));
        }
        return m;
    }

    /** 结构化数据：软件信息 + 子页面包屑 */
    private String jsonLd(String base, String path, Map<String, Object> site,
                          String pageTitle, String pageDesc, AppVersion latest) {
        String name = str(site.get("title"), "WinAppMigrator");
        Map<String, Object> app = new LinkedHashMap<>();
        app.put("@context", "https://schema.org");
        app.put("@type", "SoftwareApplication");
        app.put("name", name);
        app.put("description", str(site.get("description"), pageDesc));
        app.put("applicationCategory", "UtilitiesApplication");
        app.put("operatingSystem", "Windows 10, Windows 11");
        app.put("url", SeoSupport.canonical(base, "/"));
        app.put("inLanguage", str(seo(site).get("lang"), "zh-CN"));
        if (latest != null && latest.getVersion() != null) {
            app.put("softwareVersion", latest.getVersion());
        }
        List<String> shots = galleryUrls(base, site);
        if (!shots.isEmpty()) {
            app.put("screenshot", shots);
        }
        Map<String, Object> logo = new LinkedHashMap<>();
        logo.put("@type", "ImageObject");
        logo.put("url", SeoSupport.canonical(base, "/favicon.svg"));
        Map<String, Object> publisher = new LinkedHashMap<>();
        publisher.put("@type", "Organization");
        publisher.put("name", name);
        publisher.put("logo", logo);
        app.put("publisher", publisher);

        List<Object> graph = new ArrayList<>();
        graph.add(app);
        if (!"/".equals(path)) {
            List<Object> crumbs = new ArrayList<>();
            crumbs.add(crumb(SeoSupport.canonical(base, "/"), "首页", 1));
            crumbs.add(crumb(SeoSupport.canonical(base, path), pageTitle, 2));
            Map<String, Object> bc = new LinkedHashMap<>();
            bc.put("@context", "https://schema.org");
            bc.put("@type", "BreadcrumbList");
            bc.put("itemListElement", crumbs);
            graph.add(bc);
        }
        try {
            return SeoSupport.jsonForScript(mapper.writeValueAsString(graph));
        } catch (Exception e) {
            return "[]";
        }
    }

    private static Map<String, Object> crumb(String url, String name, int position) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("@type", "ListItem");
        m.put("position", position);
        m.put("name", name);
        m.put("item", url);
        return m;
    }

    @SuppressWarnings("unchecked")
    private static List<String> galleryUrls(String base, Map<String, Object> site) {
        List<String> out = new ArrayList<>();
        Object gallery = site.get("gallery");
        if (gallery instanceof List<?> list) {
            for (Object o : list) {
                if (o instanceof Map<?, ?> m) {
                    String url = str(((Map<String, Object>) m).get("url"), "");
                    if (!url.isBlank()) {
                        out.add(absImage(base, url));
                    }
                }
            }
        }
        return out;
    }

    /**
     * 分享主图：后台配置的 seo.ogImage 优先，否则使用 1200×630 的品牌封面。
     *
     * <p>刻意不去拿主视觉截图兜底：截图体积大且比例不固定，在社交卡片里会被裁切或加载很慢。
     */
    static String ogImage(Map<String, Object> site) {
        String og = str(seo(site).get("ogImage"), "");
        return og.isBlank() ? DEFAULT_OG_IMAGE : og;
    }

    /** 品牌分享图（构建期生成，后台可用 seo.ogImage 覆盖） */
    static final String DEFAULT_OG_IMAGE = "/og/og-cover.png";

    private static String absImage(String base, String url) {
        if (url == null || url.isBlank()) {
            return SeoSupport.canonical(base, DEFAULT_OG_IMAGE);
        }
        if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("//")) {
            return url;
        }
        return base + (url.startsWith("/") ? url : "/" + url);
    }

    /* ---------------- 内容取值小工具 ---------------- */

    @SuppressWarnings("unchecked")
    private static Map<String, Object> subMap(Map<String, Object> site, String key) {
        Object v = site.get(key);
        return v instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
    }

    private static Map<String, Object> seo(Map<String, Object> site) {
        return subMap(site, "seo");
    }

    private static String sectionTitle(Map<String, Object> site, String key, String fallback) {
        return str(subMap(subMap(site, "sections"), key).get("title"), fallback);
    }

    /** 区块标签，如「// 06 — 常见问题」 */
    private static String sectionTag(Map<String, Object> site, String key) {
        return str(subMap(subMap(site, "sections"), key).get("tag"), "");
    }

    private static String sectionSub(Map<String, Object> site, String key, String fallback) {
        return str(subMap(subMap(site, "sections"), key).get("sub"), fallback);
    }

    private static String joinTitle(String a, String b) {
        String x = a == null ? "" : a.trim();
        String y = b == null ? "" : b.trim();
        if (x.isEmpty()) {
            return y;
        }
        return y.isEmpty() || x.equals(y) ? x : x + " — " + y;
    }

    private static String str(Object v, String fallback) {
        return v == null ? fallback : String.valueOf(v);
    }

    /** 供下载页直接输出纯文本更新日志用 */
    public static List<String> noteLines(String notes) {
        List<String> out = new ArrayList<>();
        for (String line : String.valueOf(notes == null ? "" : notes).split("\\r?\\n")) {
            String t = line.trim().replaceAll("^[-•·\\s]+", "");
            if (!t.isEmpty()) {
                out.add(t);
            }
        }
        return out;
    }
}
