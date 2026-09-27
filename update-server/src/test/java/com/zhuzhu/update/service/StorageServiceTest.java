package com.zhuzhu.update.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.nio.file.Paths;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 上传类型白名单与目录穿越防护——安全边界必须有测试兜住。 */
class StorageServiceTest {

    @Test
    @DisplayName("扩展名解析大小写无关且无扩展名返回空串")
    void extensionOf() {
        assertEquals("png", StorageService.extensionOf("shot.PNG"));
        assertEquals("mp4", StorageService.extensionOf("demo.mp4"));
        assertEquals("", StorageService.extensionOf("README"));
        assertEquals("", StorageService.extensionOf(null));
    }

    @Test
    @DisplayName("仅允许白名单内的图片与视频类型")
    void allowedExtensions() {
        assertTrue(StorageService.allowed("a.png", StorageService.Kind.IMAGE));
        assertTrue(StorageService.allowed("a.webp", StorageService.Kind.IMAGE));
        assertTrue(StorageService.allowed("a.mp4", StorageService.Kind.VIDEO));
        assertTrue(StorageService.allowed("setup.exe", StorageService.Kind.PACKAGE));
        // SVG 可内嵌脚本，明确不在图片白名单内
        assertFalse(StorageService.allowed("evil.svg", StorageService.Kind.IMAGE));
        assertFalse(StorageService.allowed("run.exe", StorageService.Kind.IMAGE));
        assertFalse(StorageService.allowed("clip.mp4", StorageService.Kind.IMAGE));
        assertFalse(StorageService.allowed("script.sh", StorageService.Kind.PACKAGE));
        assertFalse(StorageService.allowed("a.png", null));
    }

    @Test
    @DisplayName("Kind 解析兼容大小写，未知值返回 null")
    void kindParse() {
        assertEquals(StorageService.Kind.IMAGE, StorageService.Kind.parse("image"));
        assertEquals(StorageService.Kind.VIDEO, StorageService.Kind.parse("Video"));
        assertEquals(StorageService.Kind.PACKAGE, StorageService.Kind.parse(" package "));
        assertEquals(null, StorageService.Kind.parse("shell"));
        assertEquals(null, StorageService.Kind.parse(null));
    }

    @Test
    @DisplayName("拒绝目录穿越与绝对路径")
    void resolveSafeRejectsEscape() {
        Path base = Paths.get("/tmp/uploads/img");

        assertThrows(IllegalArgumentException.class,
                () -> StorageService.resolveSafe(base, "../../../etc/passwd"));
        assertThrows(IllegalArgumentException.class,
                () -> StorageService.resolveSafe(base, "..\\..\\windows\\system32\\cmd.exe"));
        assertThrows(IllegalArgumentException.class,
                () -> StorageService.resolveSafe(base, "/etc/shadow"));
    }

    @Test
    @DisplayName("正常文件名解析后仍位于根目录之内")
    void resolveSafeKeepsInside() {
        Path base = Paths.get("/tmp/uploads/img");
        Path resolved = StorageService.resolveSafe(base, "abc_shot.png");

        assertTrue(resolved.startsWith(base.toAbsolutePath().normalize()));
        assertEquals("abc_shot.png", resolved.getFileName().toString());
    }
}
