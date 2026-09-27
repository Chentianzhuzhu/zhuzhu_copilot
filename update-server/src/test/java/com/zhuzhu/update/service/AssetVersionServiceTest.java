package com.zhuzhu.update.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 静态资源版本号：这是防止「HTML 已更新、CDN 仍给旧 CSS/JS」的关键机制，
 * 因此必须保证版本号真的随内容变化、且取值稳定可用。
 */
class AssetVersionServiceTest {

    private final AssetVersionService service = new AssetVersionService();

    @Test
    @DisplayName("真实资源返回「内容哈希-部署标识」版本号")
    void realAssetReturnsHash() {
        String css = service.of("/css/site.css");
        String js = service.of("/js/site.js");

        assertTrue(css.matches("[0-9a-f]{8}-[0-9a-f]+"), "样式版本号格式异常：" + css);
        assertTrue(js.matches("[0-9a-f]{8}-[0-9a-f]+"), "脚本版本号格式异常：" + js);
        assertNotEquals(css, js, "不同内容的版本号不应相同");
        assertTrue(css.endsWith("-" + service.buildStamp()), "版本号应带上部署标识");
    }

    @Test
    @DisplayName("部署标识非空且一次运行内稳定（同一版本 URL 不变）")
    void buildStampIsStable() {
        assertFalse(service.buildStamp().isBlank(), "部署标识不应为空");
        assertEquals(service.of("/css/site.css"), service.of("/css/site.css"));
    }

    @Test
    @DisplayName("同一路径重复取值结果稳定（可缓存）")
    void stableAcrossCalls() {
        assertEquals(service.of("/css/site.css"), service.of("/css/site.css"));
    }

    @Test
    @DisplayName("资源不存在或路径非法时回落到 1，不抛异常")
    void missingAssetFallsBack() {
        assertEquals(AssetVersionService.FALLBACK, service.of("/css/not-exists.css"));
        assertEquals(AssetVersionService.FALLBACK, service.of(null));
        assertEquals(AssetVersionService.FALLBACK, service.of("   "));
    }

    @Test
    @DisplayName("内容变化必然产生不同版本号（缓存失效的根本保证）")
    void contentChangeChangesHash() {
        String a = AssetVersionService.hashOfContent("body{color:#000}");
        String b = AssetVersionService.hashOfContent("body{color:#000}/* 改动 */");

        assertNotEquals(a, b);
        assertEquals(a, AssetVersionService.hashOfContent("body{color:#000}"));
        assertEquals(8, a.length());
        assertFalse(a.isBlank());
    }
}
