package com.zhuzhu.market.web;

import com.zhuzhu.market.model.User;
import com.zhuzhu.market.repo.UserRepo;
import org.springframework.data.redis.core.StringRedisTemplate;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.security.SecureRandom;
import java.time.Duration;
import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final UserRepo users;
    private final StringRedisTemplate redis;
    private static final SecureRandom RNG = new SecureRandom();
    private static final String RATE = "pm:rate:";

    public AuthController(UserRepo users, StringRedisTemplate redis) {
        this.users = users;
        this.redis = redis;
    }

    private String hash(String password, String salt) {
        try {
            var md = java.security.MessageDigest.getInstance("SHA-256");
            md.update(salt.getBytes());
            var d = md.digest(password.getBytes());
            var sb = new StringBuilder();
            for (byte b : d) sb.append("%02x".formatted(b));
            return salt + "$" + sb;
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @PostMapping("/register")
    public ResponseEntity<?> register(@RequestBody Map<String, String> body) {
        String username = (body.getOrDefault("username", "")).trim().toLowerCase();
        String password = body.getOrDefault("password", "");
        String developer = body.getOrDefault("developer", "").trim();
        if (!username.matches("[a-z0-9_]{3,32}") || password.length() < 8) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "用户名 3-32 位（小写字母/数字/下划线），密码至少 8 位"));
        }
        if (users.findByUsername(username).isPresent()) {
            return ResponseEntity.badRequest().body(Map.of("error", "用户名已存在"));
        }
        User u = new User();
        u.username = username;
        u.developer = developer.isBlank() ? username : developer;
        String salt = Long.toHexString(RNG.nextLong());
        u.passwordHash = hash(password, salt);
        users.save(u);
        String token = Sessions.issue(redis, username);
        return ResponseEntity.ok(Map.of("token", token, "user", Sessions.publicUser(u)));
    }

    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody Map<String, String> body) {
        String username = (body.getOrDefault("username", "")).trim().toLowerCase();
        String password = body.getOrDefault("password", "");
        // 限流：每 IP 每分钟最多 10 次登录尝试（Redis INCR + EXPIRE）
        String ip = reqIp();
        Long n = redis.opsForValue().increment(RATE + "login:" + ip);
        if (n != null && n == 1) redis.expire(RATE + "login:" + ip, Duration.ofMinutes(1));
        if (n != null && n > 10) {
            return ResponseEntity.status(429).body(Map.of("error", "尝试过于频繁，请稍后再试"));
        }
        User u = users.findByUsername(username).orElse(null);
        boolean ok = u != null && u.passwordHash.contains("$")
                && hash(password, u.passwordHash.split("\\$", 2)[0])
                        .equals(u.passwordHash);
        if (!ok) return ResponseEntity.status(401).body(Map.of("error", "用户名或密码错误"));
        String token = Sessions.issue(redis, username);
        return ResponseEntity.ok(Map.of("token", token, "user", Sessions.publicUser(u)));
    }

    @PostMapping("/logout")
    public ResponseEntity<?> logout(@RequestHeader(value = "Authorization", required = false) String h) {
        if (h != null && h.startsWith("Bearer ")) Sessions.revoke(redis, h.substring(7));
        return ResponseEntity.ok(Map.of("ok", true));
    }

    /** 修改开发者/公司名称（登录态） */
    @PostMapping("/developer")
    public ResponseEntity<?> setDeveloper(@RequestBody Map<String, String> body,
                                          HttpServletRequest req) throws Sessions.AuthException {
        User u = Sessions.require(redis, users, req);
        String d = body.getOrDefault("developer", "").trim();
        if (d.isEmpty() || d.length() > 128) {
            return ResponseEntity.badRequest().body(Map.of("error", "开发者/公司名称 1-128 字"));
        }
        u.developer = d;
        users.save(u);
        return ResponseEntity.ok(Sessions.publicUser(u));
    }

    private String reqIp() {
        var s = org.springframework.web.context.request.RequestContextHolder.getRequestAttributes();
        if (s instanceof org.springframework.web.context.request.ServletRequestAttributes a) {
            String x = a.getRequest().getHeader("X-Real-IP");
            return x != null ? x : a.getRequest().getRemoteAddr();
        }
        return "unknown";
    }
}
