package com.zhuzhu.update.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 官网内容合并语义：老数据自动补齐新字段，但后台的清空操作必须被尊重。 */
class ContentServiceTest {

    @Test
    @DisplayName("默认内容包含官网所有区块键")
    void defaultsContainAllSections() {
        Map<String, Object> d = ContentService.defaults();
        for (String key : List.of("title", "slogan", "description", "heroImage", "sections",
                "features", "gallery", "videos", "advantages", "faq", "stats",
                "requirements", "seo", "footer")) {
            assertTrue(d.containsKey(key), "默认内容缺少键：" + key);
        }
        assertTrue(d.get("sections") instanceof Map);
        assertEquals(8, ((Map<?, ?>) d.get("sections")).size(), "sections 应覆盖 8 个区块");
        assertFalse(ContentService.defaults().isEmpty());
    }

    @Test
    @DisplayName("库中缺失的键由默认值补齐（老数据无需迁移）")
    void mergeFillsMissingKeys() {
        Map<String, Object> stored = new LinkedHashMap<>();
        stored.put("title", "我的站点");

        Map<String, Object> merged = ContentService.merge(ContentService.defaults(), stored);

        assertEquals("我的站点", merged.get("title"));
        assertTrue(merged.containsKey("gallery"), "新字段 gallery 应被补齐");
        assertTrue(merged.containsKey("videos"));
        assertTrue(merged.containsKey("faq"));
    }

    @Test
    @DisplayName("库中清空的列表不会被默认值复填")
    void mergeKeepsEmptyList() {
        Map<String, Object> stored = new LinkedHashMap<>();
        stored.put("features", new ArrayList<>());

        Map<String, Object> merged = ContentService.merge(ContentService.defaults(), stored);

        assertTrue(((List<?>) merged.get("features")).isEmpty(),
                "后台删空特性列表后不应被默认值填回");
    }

    @Test
    @DisplayName("库中的列表整体覆盖默认列表")
    void mergeReplacesList() {
        Map<String, Object> stored = new LinkedHashMap<>();
        stored.put("stats", List.of(Map.of("label", "自定义", "value", "1")));

        Map<String, Object> merged = ContentService.merge(ContentService.defaults(), stored);
        List<?> stats = (List<?>) merged.get("stats");

        assertEquals(1, stats.size());
        assertEquals("自定义", ((Map<?, ?>) stats.get(0)).get("label"));
    }

    @Test
    @DisplayName("嵌套对象（sections / seo / footer）逐键合并")
    void mergeNestedMaps() {
        Map<String, Object> stored = new LinkedHashMap<>();
        Map<String, Object> sections = new LinkedHashMap<>();
        sections.put("features", Map.of("title", "只看这一块"));
        stored.put("sections", sections);
        stored.put("seo", Map.of("keywords", "自定义关键词"));

        Map<String, Object> merged = ContentService.merge(ContentService.defaults(), stored);
        Map<?, ?> mergedSections = (Map<?, ?>) merged.get("sections");
        Map<?, ?> features = (Map<?, ?>) mergedSections.get("features");

        assertEquals("只看这一块", features.get("title"));
        assertTrue(features.containsKey("tag"), "子对象缺失的键应由默认值补齐");
        assertEquals(8, mergedSections.size(), "未覆盖的区块应保留默认值");
        assertEquals("自定义关键词", ((Map<?, ?>) merged.get("seo")).get("keywords"));
    }

    @Test
    @DisplayName("库中多出的键被保留，便于字段演进")
    void mergeKeepsUnknownKeys() {
        Map<String, Object> stored = new LinkedHashMap<>();
        stored.put("futureField", "保持不动");

        Map<String, Object> merged = ContentService.merge(ContentService.defaults(), stored);

        assertEquals("保持不动", merged.get("futureField"));
    }

    @Test
    @DisplayName("空库与 null 输入不抛异常")
    void mergeHandlesNullAndEmpty() {
        Map<String, Object> defaults = ContentService.defaults();
        assertEquals(defaults, ContentService.merge(defaults, null));
        assertEquals(defaults, ContentService.merge(defaults, Map.of()));
    }
}
