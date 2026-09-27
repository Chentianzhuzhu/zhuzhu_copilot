package com.zhuzhu.update.web;

import java.util.List;
import java.util.regex.Pattern;

/**
 * SEO 辅助：站点绝对地址推导、robots.txt、sitemap.xml 生成。
 *
 * <p>站点地址优先取环境变量 {@code SITE_BASE_URL}；未配置时按反向代理透传的
 * {@code X-Forwarded-Proto / X-Forwarded-Host} 推导，最后回落到请求本身。
 * 域名一律做格式校验，非法值回落到 localhost，避免 Host 头污染 canonical 与 JSON-LD。
 */
public final class SeoSupport {

    /** 合法 host：域名或 IP，可选端口 */
    private static final Pattern HOST =
            Pattern.compile("^[A-Za-z0-9]([A-Za-z0-9\\-.]*[A-Za-z0-9])?(:\\d{1,5})?$");

    private static final String FALLBACK_HOST = "localhost";

    private SeoSupport() {
    }

    /** sitemap 条目 */
    public record Entry(String path, String lastmod, String changefreq, double priority) {
    }

    /** 推导站点根地址，形如 https://example.com（不带结尾斜杠） */
    public static String baseUrl(String proto, String host, String port) {
        String scheme = "https".equalsIgnoreCase(trim(proto)) ? "https" : "http";
        String h = trim(host);
        if (h.isEmpty() || !HOST.matcher(h).matches()) {
            h = FALLBACK_HOST;
        }
        if (h.contains(":") || port == null || trim(port).isEmpty()) {
            return scheme + "://" + h;
        }
        String p = trim(port);
        boolean isDefault = ("https".equals(scheme) && "443".equals(p)) || ("http".equals(scheme) && "80".equals(p));
        return scheme + "://" + h + (isDefault ? "" : ":" + p);
    }

    /** 页面规范化链接 */
    public static String canonical(String baseUrl, String path) {
        String b = trim(baseUrl);
        if (b.endsWith("/")) {
            b = b.substring(0, b.length() - 1);
        }
        String p = trim(path);
        if (p.isEmpty() || "/".equals(p)) {
            return b + "/";
        }
        return b + (p.startsWith("/") ? p : "/" + p);
    }

    /** robots.txt：放行全站，屏蔽后台与接口，并声明 sitemap */
    public static String robots(String baseUrl) {
        return String.join("\n",
                "User-agent: *",
                "Allow: /",
                "",
                "# 管理后台与内部接口不参与收录",
                "Disallow: /admin",
                "Disallow: /admin/",
                "Disallow: /api/",
                "",
                "Sitemap: " + canonical(baseUrl, "/sitemap.xml"),
                "");
    }

    /** sitemap.xml */
    public static String sitemap(String baseUrl, List<Entry> entries) {
        StringBuilder sb = new StringBuilder();
        sb.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        sb.append("<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n");
        for (Entry e : entries) {
            sb.append("  <url>\n");
            sb.append("    <loc>").append(xml(canonical(baseUrl, e.path()))).append("</loc>\n");
            if (e.lastmod() != null && !e.lastmod().isBlank()) {
                sb.append("    <lastmod>").append(xml(e.lastmod())).append("</lastmod>\n");
            }
            if (e.changefreq() != null && !e.changefreq().isBlank()) {
                sb.append("    <changefreq>").append(xml(e.changefreq())).append("</changefreq>\n");
            }
            sb.append("    <priority>").append(String.format(java.util.Locale.ROOT, "%.1f", e.priority())).append("</priority>\n");
            sb.append("  </url>\n");
        }
        sb.append("</urlset>\n");
        return sb.toString();
    }

    /** XML 文本转义 */
    public static String xml(String s) {
        return String.valueOf(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&apos;");
    }

    /** JSON-LD 转义（防止 </script> 提前闭合） */
    public static String jsonForScript(String json) {
        return String.valueOf(json).replace("<", "\\u003C").replace(">", "\\u003E");
    }

    private static String trim(String s) {
        return s == null ? "" : s.trim();
    }
}
