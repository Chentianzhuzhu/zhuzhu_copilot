package com.zhuzhu.market.web;

import com.zhuzhu.market.model.User;
import com.zhuzhu.market.repo.UserRepo;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.servlet.HandlerInterceptor;

import java.security.MessageDigest;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;

/** 会话令牌：登录签发 UUID 存 Redis（TTL 7 天），请求头 Authorization: Bearer <token>。 */
public final class Sessions {
    private static final String KEY = "pm:token:";

    private Sessions() {}

    public static String issue(StringRedisTemplate redis, String username) {
        String token = UUID.randomUUID().toString().replace("-", "");
        redis.opsForValue().set(KEY + token, username, Duration.ofDays(7));
        return token;
    }

    public static String user(StringRedisTemplate redis, String token) {
        return (token == null || token.isBlank()) ? null
                : redis.opsForValue().get(KEY + token);
    }

    public static void revoke(StringRedisTemplate redis, String token) {
        redis.delete(KEY + token);
    }

    /** HandlerInterceptor：需要登录的接口在 Controller 里调用 require(redis, req)。 */
    public static User require(StringRedisTemplate redis, UserRepo repo,
                               HttpServletRequest req) throws AuthException {
        String h = req.getHeader("Authorization");
        String token = (h != null && h.startsWith("Bearer ")) ? h.substring(7) : h;
        String username = user(redis, token);
        if (username == null) throw new AuthException("需要登录（先 POST /api/auth/login 获取 token）");
        return repo.findByUsername(username).orElseThrow(AuthException::new);
    }

    public static class AuthException extends RuntimeException {
        public AuthException() { super("需要登录"); }
        public AuthException(String m) { super(m); }
    }

    public static String sha256(byte[] data) {
        try {
            var md = MessageDigest.getInstance("SHA-256");
            var sb = new StringBuilder();
            for (byte b : md.digest(data)) sb.append("%02x".formatted(b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    public static Map<String, Object> publicUser(User u) {
        return Map.of("username", u.username,
                "developer", u.developer == null ? u.username : u.developer);
    }
}
