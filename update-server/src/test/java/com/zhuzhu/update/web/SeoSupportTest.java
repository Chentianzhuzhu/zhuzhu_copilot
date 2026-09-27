package com.zhuzhu.update.web;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** SEO 产物的正确性：站点地址推导、sitemap 结构、转义与注入防护。 */
class SeoSupportTest {

    @Test
    @DisplayName("按反向代理头推导站点根地址")
    void baseUrlFromProxy() {
        assertEquals("https://example.com", SeoSupport.baseUrl("https", "example.com", "443"));
        assertEquals("http://example.com", SeoSupport.baseUrl("http", "example.com", "80"));
        assertEquals("https://example.com:8443", SeoSupport.baseUrl("https", "example.com", "8443"));
        assertEquals("https://chentian.dpdns.org", SeoSupport.baseUrl("HTTPS", "chentian.dpdns.org", "443"));
    }

    @Test
    @DisplayName("host 已带端口时不再拼接端口")
    void baseUrlWithPortInHost() {
        assertEquals("https://example.com:8443", SeoSupport.baseUrl("https", "example.com:8443", "443"));
    }

    @Test
    @DisplayName("非法 host 回落 localhost，避免 Host 头污染 canonical")
    void baseUrlRejectsIllegalHost() {
        assertEquals("https://localhost", SeoSupport.baseUrl("https", "evil host/../x", "443"));
        assertEquals("https://localhost", SeoSupport.baseUrl("https", "\"><script>", "443"));
        assertEquals("http://localhost", SeoSupport.baseUrl(null, null, null));
    }

    @Test
    @DisplayName("规范化链接：根路径保留结尾斜杠")
    void canonical() {
        assertEquals("https://example.com/", SeoSupport.canonical("https://example.com", "/"));
        assertEquals("https://example.com/faq", SeoSupport.canonical("https://example.com/", "/faq"));
        assertEquals("https://example.com/faq", SeoSupport.canonical("https://example.com", "faq"));
    }

    @Test
    @DisplayName("robots.txt 放行全站、屏蔽后台与接口、声明 sitemap")
    void robots() {
        String txt = SeoSupport.robots("https://example.com");

        assertTrue(txt.contains("User-agent: *"));
        assertTrue(txt.contains("Allow: /"));
        assertTrue(txt.contains("Disallow: /admin"));
        assertTrue(txt.contains("Disallow: /api/"));
        assertTrue(txt.contains("Sitemap: https://example.com/sitemap.xml"));
    }

    @Test
    @DisplayName("sitemap.xml 结构合法且包含全部条目")
    void sitemap() {
        String xml = SeoSupport.sitemap("https://example.com", List.of(
                new SeoSupport.Entry("/", "2026-09-25", "weekly", 1.0),
                new SeoSupport.Entry("/gallery", "2026-09-25", "weekly", 0.8)));

        assertTrue(xml.startsWith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"));
        assertTrue(xml.contains("<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"));
        assertTrue(xml.contains("<loc>https://example.com/</loc>"));
        assertTrue(xml.contains("<loc>https://example.com/gallery</loc>"));
        assertTrue(xml.contains("<priority>0.8</priority>"));
        assertTrue(xml.trim().endsWith("</urlset>"));
        assertEquals(2, xml.split("<url>").length - 1);
    }

    @Test
    @DisplayName("XML 特殊字符被转义")
    void xmlEscaping() {
        assertEquals("a&amp;b&lt;c&gt;d&quot;e&apos;f", SeoSupport.xml("a&b<c>d\"e'f"));
        String xml = SeoSupport.sitemap("https://example.com", List.of(
                new SeoSupport.Entry("/a?x=1&y=2", null, "daily", 0.5)));
        assertTrue(xml.contains("/a?x=1&amp;y=2"));
    }

    @Test
    @DisplayName("lastmod 为空时省略该标签")
    void sitemapOmitsEmptyLastmod() {
        String xml = SeoSupport.sitemap("https://example.com", List.of(
                new SeoSupport.Entry("/", "", "weekly", 1.0)));
        assertFalse(xml.contains("<lastmod>"));
    }

    @Test
    @DisplayName("JSON-LD 中的尖括号被转义，防止提前闭合 script 标签")
    void jsonForScript() {
        String out = SeoSupport.jsonForScript("{\"a\":\"</script><img>\"}");
        assertFalse(out.contains("</script>"));
        assertFalse(out.contains("<img>"));
        assertTrue(out.contains("\\u003C"));
    }
}
