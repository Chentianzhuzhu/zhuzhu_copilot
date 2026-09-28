package com.zhuzhu.update.web;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.service.ContentService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 页面渲染辅助逻辑：更新说明拆分、体积格式化、列表截取、图标归一化、分享图选取、结构化数据。 */
class SitePageControllerTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String BASE = "https://chentian.dpdns.org";

    @Test
    @DisplayName("noteLines 逐行拆分并去掉项目符号")
    void noteLines() {
        List<String> lines = SitePageController.noteLines("- 修复迁移卡顿\n• 新增视频展示位\n\n  优化界面  ");
        assertEquals(List.of("修复迁移卡顿", "新增视频展示位", "优化界面"), lines);
        assertTrue(SitePageController.noteLines(null).isEmpty());
        assertTrue(SitePageController.noteLines("").isEmpty());
    }

    @Test
    @DisplayName("sizeText 输出可读体积")
    void sizeText() {
        assertEquals("", SitePageController.sizeText(0));
        assertEquals("1.0 MB", SitePageController.sizeText(1024 * 1024));
        assertEquals("2.00 GB", SitePageController.sizeText(2L * 1024 * 1024 * 1024));
    }

    @Test
    @DisplayName("preview 截取前 N 条且不改动原列表")
    void preview() {
        List<Integer> src = List.of(1, 2, 3, 4, 5, 6, 7);

        assertEquals(List.of(1, 2, 3), SitePageController.preview(src, 3));
        assertEquals(src, SitePageController.preview(src, 99));
        assertTrue(SitePageController.preview(null, 3).isEmpty());
        assertTrue(SitePageController.preview(Map.of(), 3).isEmpty());
    }

    @Test
    @DisplayName("历史内容缺少图标字段时回落到默认图标，且不修改原对象")
    void normalizeIconsFillsMissingIcon() {
        Map<String, Object> legacy = new LinkedHashMap<>();
        legacy.put("title", "应用迁移");
        legacy.put("desc", "描述");
        List<Object> source = List.of(legacy, Map.of("icon", "不存在的图标"), "非法条目");

        List<Map<String, Object>> out = SitePageController.normalizeIcons(source);

        assertEquals(2, out.size(), "非 Map 条目应被忽略");
        assertEquals(Icons.DEFAULT, out.get(0).get("icon"), "缺失图标应回落默认值");
        assertEquals("应用迁移", out.get(0).get("title"), "其余字段应保留");
        assertEquals(Icons.DEFAULT, out.get(1).get("icon"), "非法图标名应回落默认值");
        assertFalse(legacy.containsKey("icon"), "不应修改传入的原对象");
    }

    @Test
    @DisplayName("normalizeIcons 对空值与未知类型安全")
    void normalizeIconsHandlesEmpty() {
        assertTrue(SitePageController.normalizeIcons(null).isEmpty());
        assertTrue(SitePageController.normalizeIcons("不是列表").isEmpty());
        assertTrue(SitePageController.normalizeIcons(List.of()).isEmpty());
    }

    @Test
    @DisplayName("分享图默认取 1200×630 品牌封面，后台配置优先")
    void ogImageSelection() {
        Map<String, Object> site = new LinkedHashMap<>();
        site.put("heroImage", "/uploads/img/huge-screenshot.png");

        assertEquals(SitePageController.DEFAULT_OG_IMAGE, SitePageController.ogImage(site),
                "未配置时应使用品牌封面，而不是体积巨大的主视觉截图");

        Map<String, Object> seo = new LinkedHashMap<>();
        seo.put("ogImage", "/uploads/img/custom-cover.png");
        site.put("seo", seo);
        assertEquals("/uploads/img/custom-cover.png", SitePageController.ogImage(site));

        seo.put("ogImage", "   ");
        assertEquals(SitePageController.DEFAULT_OG_IMAGE, SitePageController.ogImage(site),
                "空白配置应回落到品牌封面");
    }

    @Test
    @DisplayName("别名按中英文逗号切分、去空白、去重")
    void alternateNamesParsing() {
        assertEquals(List.of("WinAppMigrator", "zhuzhu Copilot"),
                SitePageController.alternateNames(site(" ", " WinAppMigrator ，zhuzhu Copilot,,WinAppMigrator ")));
        assertTrue(SitePageController.alternateNames(site("k", "")).isEmpty(), "未配置别名时不应产生节点");
    }

    @Test
    @DisplayName("结构化数据同时声明两个品牌名，单个别名用字符串、多个用数组")
    void jsonLdDeclaresBothBrands() {
        Map<String, Object> single = node(graph("/", site("k", "WinAppMigrator")), "SoftwareApplication");
        assertEquals("WinAppMigrator", single.get("alternateName"));

        Map<String, Object> site = site("k", "WinAppMigrator,zhuzhu Copilot");
        for (String type : List.of("SoftwareApplication", "WebSite")) {
            assertEquals(List.of("WinAppMigrator", "zhuzhu Copilot"),
                    node(graph("/", site), type).get("alternateName"),
                    type + " 未把两个品牌名绑在一起");
        }
    }

    @Test
    @DisplayName("未配置别名时不产出 alternateName 字段")
    void jsonLdOmitsAlternateNameWhenUnset() {
        Map<String, Object> app = node(graph("/", site("k", "")), "SoftwareApplication");
        assertFalse(app.containsKey("alternateName"), "空别名不应写入结构化数据");
    }

    @Test
    @DisplayName("站点信息与软件信息都产出，面包屑只出现在子页面")
    void jsonLdSiteAndBreadcrumb() {
        List<Map<String, Object>> home = graph("/", site("k", ""));
        assertNotNull(node(home, "WebSite"), "缺少站点级结构化数据");
        assertNull(node(home, "BreadcrumbList"), "首页不需要面包屑");

        Map<String, Object> crumb = node(graph("/faq", site("k", "")), "BreadcrumbList");
        assertNotNull(crumb, "子页面缺少面包屑");
        assertEquals(2, ((List<?>) crumb.get("itemListElement")).size());
    }

    @Test
    @DisplayName("FAQ 结构化数据只出现在常见问题页，逐条取库内问答")
    void jsonLdFaqPage() {
        Map<String, Object> site = site("k", "");
        int expected = ((List<?>) site.get("faq")).size();

        assertNull(node(graph("/", site), "FAQPage"), "首页不应声明 FAQPage，会与页面内容不符");

        Map<String, Object> faq = node(graph("/faq", site), "FAQPage");
        assertNotNull(faq, "常见问题页缺少 FAQPage 结构化数据");
        assertEquals(expected, ((List<?>) faq.get("mainEntity")).size(), "问答条数与库内不一致");
        assertEquals(BASE + "/faq", faq.get("url"));
    }

    @Test
    @DisplayName("问答为空时不产出空壳 FAQPage")
    void jsonLdSkipsEmptyFaq() {
        Map<String, Object> site = site("k", "");
        site.put("faq", List.of());
        assertNull(node(graph("/faq", site), "FAQPage"));
    }

    @Test
    @DisplayName("已发布版本号写入软件结构化数据")
    void jsonLdSoftwareVersion() {
        AppVersion version = new AppVersion();
        version.setVersion("5.1.2");
        String json = SitePageController.jsonLd(MAPPER, BASE, "/", site("k", ""), "标题", "描述", version);
        assertTrue(json.contains("\"softwareVersion\":\"5.1.2\""), "结构化数据未带版本号：" + json);
    }

    /* ---------------- 结构化数据测试小工具 ---------------- */

    /** 默认内容 + 指定的关键词与副品牌别名 */
    private static Map<String, Object> site(String keywords, String aliases) {
        Map<String, Object> site = ContentService.defaults();
        Map<String, Object> seo = new LinkedHashMap<>();
        seo.put("keywords", keywords);
        seo.put("alternateNames", aliases);
        seo.put("lang", "zh-CN");
        site.put("seo", seo);
        return site;
    }

    private static List<Map<String, Object>> graph(String path, Map<String, Object> site) {
        String json = SitePageController.jsonLd(MAPPER, BASE, path, site, "页面标题", "页面描述", null);
        try {
            return MAPPER.readValue(json, new TypeReference<List<Map<String, Object>>>() {
            });
        } catch (Exception e) {
            throw new IllegalStateException("结构化数据不是合法 JSON：" + json, e);
        }
    }

    private static Map<String, Object> node(List<Map<String, Object>> graph, String type) {
        return graph.stream().filter(item -> type.equals(item.get("@type"))).findFirst().orElse(null);
    }
}
