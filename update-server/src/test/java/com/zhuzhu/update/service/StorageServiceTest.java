package com.zhuzhu.update.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.FileTime;

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

    @Test
    @DisplayName("媒体库更新时间取图片与视频中最新的一个文件")
    void latestUpdateInPicksNewestAcrossKinds(@TempDir Path root) throws Exception {
        Files.createDirectories(root.resolve("img"));
        Files.createDirectories(root.resolve("video"));
        Files.createDirectories(root.resolve("pkg"));
        Path older = root.resolve("img/older.png");
        Path newer = root.resolve("video/newer.mp4");
        // 安装包目录更晚，但「媒体库」只统计图片与视频，不应把它算进来
        Path newestPkg = root.resolve("pkg/setup.exe");
        Files.writeString(older, "a");
        Files.writeString(newer, "b");
        Files.writeString(newestPkg, "c");
        Files.setLastModifiedTime(older, FileTime.fromMillis(1_000_000L));
        Files.setLastModifiedTime(newer, FileTime.fromMillis(2_000_000L));
        Files.setLastModifiedTime(newestPkg, FileTime.fromMillis(9_000_000L));

        long latest = StorageService.latestUpdateIn(root, StorageService.MEDIA_KINDS);

        assertEquals(2_000_000L, latest, "应取媒体分类（img/video）的最大修改时间");
    }

    @Test
    @DisplayName("媒体库目录不存在或为空时返回 0")
    void latestUpdateInReturnsZeroWhenEmpty(@TempDir Path root) {
        assertEquals(0L, StorageService.latestUpdateIn(root, StorageService.MEDIA_KINDS));
        assertEquals(0L, StorageService.latestUpdateIn(null, StorageService.MEDIA_KINDS));
    }

    @Test
    @DisplayName("媒体库只含图片与视频分类，安装包不算媒体")
    void mediaKindsExcludePackage() {
        assertEquals(2, StorageService.MEDIA_KINDS.size());
        assertTrue(StorageService.MEDIA_KINDS.contains(StorageService.Kind.IMAGE));
        assertTrue(StorageService.MEDIA_KINDS.contains(StorageService.Kind.VIDEO));
        assertFalse(StorageService.MEDIA_KINDS.contains(StorageService.Kind.PACKAGE));
    }
}
