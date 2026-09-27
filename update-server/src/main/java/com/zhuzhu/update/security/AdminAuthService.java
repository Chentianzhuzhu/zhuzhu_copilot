package com.zhuzhu.update.security;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.UUID;

/** 管理后台认证：密码来自环境变量 ADMIN_PASSWORD，登录签发 Redis token（24h） */
@Service
public class AdminAuthService {

    private static final String TOKEN_PREFIX = "auth:admin:";
    private static final Duration TOKEN_TTL = Duration.ofHours(24);

    private final StringRedisTemplate redis;

    @Value("${admin.password:}")
    private String adminPassword;

    public AdminAuthService(StringRedisTemplate redis) {
        this.redis = redis;
    }

    /** 密码匹配则签发 token，否则返回 null */
    public String login(String password) {
        String expect = adminPassword == null ? "" : adminPassword.trim();
        if (expect.isEmpty() || !expect.equals(password == null ? "" : password)) {
            return null;
        }
        String token = UUID.randomUUID().toString().replace("-", "");
        try {
            redis.opsForValue().set(TOKEN_PREFIX + token, "1", TOKEN_TTL);
        } catch (Exception e) {
            // Redis 不可用则拒绝登录（安全优先）
            return null;
        }
        return token;
    }

    /** token 是否有效 */
    public boolean valid(String token) {
        if (token == null || token.isBlank()) {
            return false;
        }
        try {
            return Boolean.TRUE.equals(redis.hasKey(TOKEN_PREFIX + token));
        } catch (Exception e) {
            return false;
        }
    }
}
