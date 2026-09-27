package com.zhuzhu.update.web;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 特性图标白名单：保证后台只能选到注册表内的图标，模板不会渲染出任意 SVG。 */
class IconsTest {

    @Test
    @DisplayName("注册表非空且默认图标可用")
    void registryUsable() {
        assertTrue(Icons.names().size() >= 10, "图标太少，后台下拉框会很难用");
        assertTrue(Icons.supports(Icons.DEFAULT));
        assertTrue(Icons.names().contains("ai"));
    }

    @Test
    @DisplayName("未知图标名回落到默认图标，不会输出注入内容")
    void unknownFallsBack() {
        String evil = Icons.of("<script>alert(1)</script>");
        assertEquals(Icons.of(Icons.DEFAULT), evil);
        assertFalse(evil.contains("script"));

        assertEquals(Icons.of(Icons.DEFAULT), Icons.of(null));
        assertEquals(Icons.of(Icons.DEFAULT), Icons.of("不存在的图标"));
    }

    @Test
    @DisplayName("合法图标返回 SVG 路径片段")
    void knownIconReturnsPaths() {
        String svg = Icons.of("shield");
        assertNotNull(svg);
        assertTrue(svg.contains("<path"));
        assertFalse(svg.contains("<script"));
    }

    @Test
    @DisplayName("图标名列表不可变，避免被外部修改")
    void namesImmutable() {
        assertThrows(UnsupportedOperationException.class, () -> Icons.names().add("hack"));
    }
}
