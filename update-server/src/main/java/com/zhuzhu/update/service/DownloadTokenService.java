package com.zhuzhu.update.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 下载链接令牌（token 化）。
 *
 * <p>明文自增 id 暴露版本数量且容易被遍历，改用一次性签名 URL：
 * 把 versionId 与过期时间序列化为 JSON，对 {@code versionId + "." + expiry} 做
 * HMAC-SHA256 签名后整体 Base64URL 编码。篡改版本号或过期都会导致签名校验失败。
 *
 * <p>HMAC 密钥优先取环境变量 {@code DOWNLOAD_TOKEN_SECRET}；
 * 未设置时回退到代码内置串（仅开发期可用，生产环境必须通过环境变量覆盖）。
 */
@Service
public class DownloadTokenService {

    /** 令牌有效期 1 小时 */
    private static final long TTL_SECONDS = 3600;

    /** 开发期回退密钥：生产环境务必设置环境变量 DOWNLOAD_TOKEN_SECRET 覆盖 */
    private static final String FALLBACK_SECRET = "zhuzhu-download-token-insecure-fallback-9f3k2j8h7d2j6s1k4m";

    private final SecretKeySpec key;
    private final ObjectMapper mapper;

    public DownloadTokenService(ObjectMapper mapper) {
        this.mapper = mapper;
        String secret = System.getenv("DOWNLOAD_TOKEN_SECRET");
        if (secret == null || secret.isBlank()) {
            secret = FALLBACK_SECRET;
        }
        this.key = new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
    }

    /** 为指定版本生成 1 小时有效的下载令牌 */
    public String generateToken(Long versionId) {
        long exp = Instant.now().plusSeconds(TTL_SECONDS).getEpochSecond();
        String sig = hmacHex(versionId + "." + exp);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("v", versionId);
        payload.put("e", exp);
        payload.put("s", sig);
        try {
            String json = mapper.writeValueAsString(payload);
            return Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(json.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException("下载令牌生成失败", e);
        }
    }

    /** 校验令牌签名与有效期，返回 versionId；篡改或过期均返回 null */
    public Long validateToken(String token) {
        if (token == null || token.isBlank()) {
            return null;
        }
        try {
            String json = new String(Base64.getUrlDecoder().decode(token), StandardCharsets.UTF_8);
            @SuppressWarnings("unchecked")
            Map<String, Object> p = mapper.readValue(json, Map.class);
            long versionId = ((Number) p.get("v")).longValue();
            long exp = ((Number) p.get("e")).longValue();
            String sig = String.valueOf(p.get("s"));
            String expected = hmacHex(versionId + "." + exp);
            if (!expected.equals(sig)) {
                return null;
            }
            if (Instant.now().getEpochSecond() > exp) {
                return null;
            }
            return versionId;
        } catch (Exception e) {
            // 非法格式 / 非数字字段 / JSON 损坏：一律视为无效令牌
            return null;
        }
    }

    private String hmacHex(String payload) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(key);
            byte[] raw = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(raw.length * 2);
            for (byte b : raw) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException("HMAC 计算失败", e);
        }
    }
}
