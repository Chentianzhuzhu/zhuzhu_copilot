package com.zhuzhu.update.web;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 页面渲染辅助逻辑：更新说明拆分、体积格式化、列表截取、图标归一化、分享图选取。 */
class SitePageControllerTest {

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
}
