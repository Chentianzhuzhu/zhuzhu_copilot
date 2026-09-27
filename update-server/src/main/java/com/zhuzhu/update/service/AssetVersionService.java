package com.zhuzhu.update.service;

import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.concurrent.ConcurrentHashMap;
import java.util.Map;

/**
 * 静态资源版本号（内容哈希 + 部署标识）。
 *
 * <p>解决的是线上最容易翻车的一类问题：**HTML 已更新，而 CDN / 浏览器仍在用旧版 CSS、JS**，
 * 于是新旧不匹配 —— 轻则样式错乱，重则整屏内容停在动画首帧（不可见）。
 *
 * <p>做法：启动时读入资源内容并取 MD5 前 8 位作为版本号，模板以 {@code ?v=xxxx} 引用；
 * 内容一变 URL 就变，新 URL 在 CDN 上必然未命中而回源，无需人工清缓存。
 *
 * <p>版本号里再拼一段**部署标识**（运行产物的修改时间）：这样即使某次部署期间
 * 资源短暂返回过 404（404 按 HTTP 规范是可缓存的），下次部署的 URL 也一定是新的，
 * 客户端不会卡在坏响应上——不需要用户手动清缓存。
 */
@Service
public class AssetVersionService {

    /** 版本号长度（短哈希，够用且 URL 简洁） */
    static final int HASH_LEN = 8;

    /** 资源查找失败时的兜底版本号 */
    static final String FALLBACK = "1";

    private final Map<String, String> cache = new ConcurrentHashMap<>();

    /** 部署标识：运行产物（jar / classes 目录）的最后修改时间，每次部署都会变化 */
    private final String buildStamp = resolveBuildStamp();

    /**
     * 取静态资源的版本号。
     *
     * @param path 以 / 开头的站点路径，如 {@code /css/site.css}
     * @return 形如 {@code 8位内容哈希-部署标识}；资源不存在时返回 {@link #FALLBACK}
     */
    public String of(String path) {
        if (path == null || path.isBlank()) {
            return FALLBACK;
        }
        String hash = cache.computeIfAbsent(path, this::hashOf);
        // 资源不存在时保持简洁的兜底值，不拼部署标识
        return FALLBACK.equals(hash) ? FALLBACK : hash + "-" + buildStamp;
    }

    /** 供测试与日志使用 */
    String buildStamp() {
        return buildStamp;
    }

    private static String resolveBuildStamp() {
        try {
            URL location = AssetVersionService.class.getProtectionDomain().getCodeSource().getLocation();
            Path artifact = Paths.get(location.toURI());
            return Long.toHexString(Files.getLastModifiedTime(artifact).toMillis());
        } catch (Exception e) {
            return "0";
        }
    }

    private String hashOf(String path) {
        String resource = "static" + (path.startsWith("/") ? path : "/" + path);
        ClassPathResource res = new ClassPathResource(resource);
        if (!res.exists()) {
            return FALLBACK;
        }
        try (InputStream in = res.getInputStream()) {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
            }
            StringBuilder sb = new StringBuilder(32);
            for (byte b : md.digest()) {
                sb.append(String.format("%02x", b));
            }
            return sb.substring(0, HASH_LEN);
        } catch (Exception e) {
            return FALLBACK;
        }
    }

    /** 便于测试：按内容直接算版本号 */
    static String hashOfContent(String content) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(String.valueOf(content).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(32);
            for (byte b : digest) {
                sb.append(String.format("%02x", b));
            }
            return sb.substring(0, HASH_LEN);
        } catch (Exception e) {
            return FALLBACK;
        }
    }
}
