package com.zhuzhu.update.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * 上传文件落盘与媒体库管理。
 *
 * <p>目录约定：{@code uploads/img}（官网图片）、{@code uploads/video}（官网视频）、
 * {@code uploads/pkg}（安装包）。所有对外方法都只接受「子目录 + 纯文件名」，
 * 不接受任意路径，避免目录穿越。
 */
@Service
public class StorageService {

    /** 上传资源分类：决定子目录与允许的扩展名 */
    public enum Kind {
        IMAGE("img", List.of("jpg", "jpeg", "png", "webp", "gif", "avif")),
        VIDEO("video", List.of("mp4", "webm", "ogg", "mov")),
        PACKAGE("pkg", List.of("exe", "msi", "zip", "7z", "jar"));

        private final String dir;
        private final List<String> extensions;

        Kind(String dir, List<String> extensions) {
            this.dir = dir;
            this.extensions = extensions;
        }

        public String dir() {
            return dir;
        }

        public List<String> extensions() {
            return extensions;
        }

        /** 宽松解析，未知取值返回 null（由调用方决定如何报错） */
        public static Kind parse(String value) {
            if (value == null) {
                return null;
            }
            for (Kind k : values()) {
                if (k.name().equalsIgnoreCase(value.trim())) {
                    return k;
                }
            }
            return null;
        }
    }

    /** 媒体库条目 */
    public record MediaItem(String name, String url, long size, long updatedAt) {
    }

    /** 落盘结果 */
    public record Stored(String relPath, String md5) {
    }

    /** 上传根目录（绝对路径或相对工作目录），由环境变量 UPLOAD_DIR 覆盖 */
    @Value("${upload.dir:./uploads}")
    private String baseDir;

    /** 保存文件并计算 MD5，返回相对路径与 MD5；扩展名不在白名单时抛异常 */
    public Stored save(MultipartFile file, Kind kind) throws IOException {
        String name = sanitize(file.getOriginalFilename());
        if (!allowed(name, kind)) {
            throw new IllegalArgumentException("不支持的文件类型：" + name
                    + "，仅支持 " + String.join(" / ", kind.extensions()));
        }
        Path dir = dirOf(kind);
        Files.createDirectories(dir);
        Path target = resolveSafe(dir, UUID.randomUUID().toString().replace("-", "") + "_" + name);
        file.transferTo(target.toFile());
        return new Stored(kind.dir() + "/" + target.getFileName(), md5(target));
    }

    /** 媒体库列表（按修改时间倒序） */
    public List<MediaItem> list(Kind kind) throws IOException {
        Path dir = dirOf(kind);
        if (!Files.isDirectory(dir)) {
            return List.of();
        }
        List<MediaItem> items = new ArrayList<>();
        try (var stream = Files.list(dir)) {
            for (Path p : stream.filter(Files::isRegularFile).toList()) {
                String name = p.getFileName().toString();
                items.add(new MediaItem(name, url(kind, name), Files.size(p),
                        Files.getLastModifiedTime(p).toInstant().toEpochMilli()));
            }
        }
        items.sort(Comparator.comparingLong(MediaItem::updatedAt).reversed());
        return items;
    }

    /** 删除媒体库文件；文件非法或不存在时返回 false */
    public boolean delete(Kind kind, String name) throws IOException {
        String clean = sanitize(name);
        if (!clean.equals(name) || !allowed(clean, kind)) {
            return false;
        }
        return Files.deleteIfExists(resolveSafe(dirOf(kind), clean));
    }

    /** 按「子目录/文件名」相对路径解析为磁盘文件（供安装包下载使用） */
    public Path resolve(String relPath) {
        return resolveSafe(Paths.get(baseDir).toAbsolutePath().normalize(), relPath);
    }

    /** 媒体对外访问地址 */
    public String url(Kind kind, String name) {
        return "/uploads/" + kind.dir() + "/" + name;
    }

    /** 官网媒体库（图片 + 视频）的最后更新时间，epoch 毫秒；无媒体时返回 0 */
    public long latestMediaUpdate() {
        return latestUpdateIn(Paths.get(baseDir), MEDIA_KINDS);
    }

    /** 官网媒体库包含的分类：只算对外展示的图片与视频，安装包不算「媒体」 */
    public static final List<Kind> MEDIA_KINDS = List.of(Kind.IMAGE, Kind.VIDEO);

    /**
     * 纯函数：给定上传根目录，取指定分类下最新一个文件的修改时间（epoch 毫秒）。
     *
     * <p>单分类目录不存在或不可读时跳过该分类，不影响另一分类的结果；
     * 全部为空时返回 0 —— 调用方据此判断「媒体库尚未有内容」。
     */
    public static long latestUpdateIn(Path uploadRoot, List<Kind> kinds) {
        long latest = 0L;
        if (uploadRoot == null || kinds == null) {
            return latest;
        }
        for (Kind kind : kinds) {
            Path dir = uploadRoot.resolve(kind.dir()).toAbsolutePath().normalize();
            if (!Files.isDirectory(dir)) {
                continue;
            }
            try (var stream = Files.list(dir)) {
                for (Path p : stream.filter(Files::isRegularFile).toList()) {
                    latest = Math.max(latest, Files.getLastModifiedTime(p).toMillis());
                }
            } catch (IOException ignored) {
                // 目录不可读（权限 / 并发删除）时跳过，不因单个分类失败而丢掉整体结果
            }
        }
        return latest;
    }

    /* ---------------- 纯函数（便于单元测试） ---------------- */

    /** 取小写扩展名，无扩展名时返回空串 */
    public static String extensionOf(String fileName) {
        String n = fileName == null ? "" : fileName;
        int dot = n.lastIndexOf('.');
        return dot < 0 ? "" : n.substring(dot + 1).toLowerCase(Locale.ROOT);
    }

    /** 扩展名是否在给定分类的白名单内 */
    public static boolean allowed(String fileName, Kind kind) {
        return kind != null && kind.extensions().contains(extensionOf(fileName));
    }

    /**
     * 在指定目录下安全解析文件名：拒绝绝对路径、层级穿越与非法字符。
     *
     * <p>注意跨平台差异：Windows 把反斜杠当分隔符，Linux 不认；
     * 因此这里**显式**拒绝反斜杠与盘符前缀，避免同一份输入在两个平台得到不同结论。
     *
     * @throws IllegalArgumentException 解析结果逃出 base 目录时抛出
     */
    public static Path resolveSafe(Path base, String relative) {
        Path root = base.toAbsolutePath().normalize();
        String rel = relative == null ? "" : relative;

        if (rel.startsWith("/") || rel.startsWith("\\") || rel.matches("^[A-Za-z]:.*")) {
            throw new IllegalArgumentException("非法路径（不允许绝对路径）：" + relative);
        }
        // 统一按 '/' 作为唯一分隔符，反斜杠一律视为非法
        if (rel.indexOf('\\') >= 0) {
            throw new IllegalArgumentException("非法路径（不允许反斜杠）：" + relative);
        }

        Path target = root.resolve(rel).normalize();
        if (!target.startsWith(root)) {
            throw new IllegalArgumentException("非法路径：" + relative);
        }
        return target;
    }

    /* ---------------- 内部工具 ---------------- */

    private Path dirOf(Kind kind) {
        return Paths.get(baseDir).resolve(kind.dir()).toAbsolutePath().normalize();
    }

    /** 去除路径分隔符与非法字符，仅保留文件名 */
    private String sanitize(String name) {
        if (name == null || name.isBlank()) {
            return "file";
        }
        String base = name.replace('\\', '/');
        base = base.substring(base.lastIndexOf('/') + 1);
        return base.replaceAll("[^\\w.\\-]", "_");
    }

    private String md5(Path p) throws IOException {
        MessageDigest md;
        try {
            md = MessageDigest.getInstance("MD5");
        } catch (Exception e) {
            throw new IOException("MD5 不可用", e);
        }
        try (InputStream in = Files.newInputStream(p)) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
            }
        }
        StringBuilder sb = new StringBuilder(32);
        for (byte b : md.digest()) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }
}
