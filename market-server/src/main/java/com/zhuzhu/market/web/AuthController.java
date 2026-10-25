package com.zhuzhu.market.web;

import com.zhuzhu.market.model.User;
import com.zhuzhu.market.repo.UserRepo;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.redis.core.StringRedisTemplate;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Duration;
import java.util.Base64;
import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {
    private final UserRepo users;
    private final StringRedisTemplate redis;
    private final TurnstileService turnstile;
    private static final SecureRandom RNG = new SecureRandom();
    private static final String RATE = "pm:rate:";
    private static final String CAPTCHA_ERR = "人机验证未通过，请点击验证组件后重试";

    /* 口令存储：pbkdf2$<迭代>$<saltB64>$<hashB64>
       旧格式（salt$sha256hex）仍可登录，成功登录后自动升级为重哈希。 */
    private static final String PREFIX = "pbkdf2$";
    private static final int ITERATIONS = 210_000;
    private static final int SALT_BYTES = 16;
    private static final int KEY_BITS = 256;
    private static final int PWD_MAX = 128;
    /** PBKDF2 单次 ~0.1s，注册限流必须比登录更紧，否则可被拿来当 CPU 放大器。 */
    private static final int REG_LIMIT_PER_10MIN = 5;

    public AuthController(UserRepo users, StringRedisTemplate redis,
                          TurnstileService turnstile) {
        this.users = users;
        this.redis = redis;
        this.turnstile = turnstile;
    }

    // ---------- 口令哈希 ----------

    private static String newHash(String password) {
        byte[] salt = new byte[SALT_BYTES];
        RNG.nextBytes(salt);
        return PREFIX + ITERATIONS + "$"
                + Base64.getEncoder().encodeToString(salt) + "$"
                + Base64.getEncoder().encodeToString(pbkdf2(password, salt, ITERATIONS));
    }

    private static boolean verify(String password, String stored) {
        if (stored == null || password == null) return false;
        if (stored.startsWith(PREFIX)) {
            String[] parts = stored.split("\\$");
            if (parts.length != 4) return false;
            try {
                int iter = Integer.parseInt(parts[1]);
                if (iter < 10_000 || iter > 2_000_000) return false; // 拒绝畸形迭代数（DoS）
                byte[] salt = Base64.getDecoder().decode(parts[2]);
                byte[] expect = Base64.getDecoder().decode(parts[3]);
                byte[] actual = pbkdf2(password, salt, iter);
                return MessageDigest.isEqual(expect, actual); // 定长比较，避免早退时序泄漏
            } catch (IllegalArgumentException e) {
                return false;
            }
        }
        // 旧格式：salt$sha256(salt + password)
        int i = stored.indexOf('$');
        if (i <= 0) return false;
        String salt = stored.substring(0, i);
        String expect = stored.substring(i + 1);
        try {
            var md = MessageDigest.getInstance("SHA-256");
            md.update(salt.getBytes(StandardCharsets.UTF_8));
            byte[] d = md.digest(password.getBytes(StandardCharsets.UTF_8));
            var sb = new StringBuilder(64);
            for (byte b : d) sb.append("%02x".formatted(b));
            return MessageDigest.isEqual(
                    sb.toString().getBytes(StandardCharsets.UTF_8),
                    expect.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            return false;
        }
    }

    private static byte[] pbkdf2(String password, byte[] salt, int iterations) {
        try {
            var spec = new PBEKeySpec(password.toCharArray(), salt, iterations, KEY_BITS);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                    .generateSecret(spec).getEncoded();
        } catch (Exception e) {
            throw new IllegalStateException("口令哈希失败", e);
        }
    }

    // ---------- 接口 ----------

    @PostMapping("/register")
    public ResponseEntity<?> register(@RequestBody Map<String, String> body) {
        String username = (body.getOrDefault("username", "")).trim().toLowerCase();
        String password = body.getOrDefault("password", "");
        String developer = body.getOrDefault("developer", "").trim();
        if (!username.matches("[a-z0-9_]{3,32}") || password.length() < 8 || password.length() > PWD_MAX) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "用户名 3-32 位（小写字母/数字/下划线），密码 8-128 位"));
        }
        // 限流在 Turnstile 之前：每次校验都要外呼 Cloudflare，若先校验再限流，
        // 攻击者可用无效 token 刷爆 siteverify（把本服务变成放大器）。
        // 注册限流：每 IP 10 分钟 5 次，压制批量注册 + PBKDF2 计算放大
        String ip = reqIp();
        Long n = redis.opsForValue().increment(RATE + "reg:" + ip);
        if (n != null && n == 1) redis.expire(RATE + "reg:" + ip, Duration.ofMinutes(10));
        if (n != null && n > REG_LIMIT_PER_10MIN) {
            return ResponseEntity.status(429).body(Map.of("error", "注册过于频繁，请稍后再试"));
        }
        // 人机验证（默认关闭，见 market.turnstile.enabled）：产品口径是只用于应用账户登录
        if (!turnstile.verify(body.getOrDefault("turnstileToken", ""), ip)) {
            return ResponseEntity.badRequest().body(Map.of("error", CAPTCHA_ERR));
        }
        if (users.findByUsername(username).isPresent()) {
            return ResponseEntity.badRequest().body(Map.of("error", "用户名已存在"));
        }
        User u = new User();
        u.username = username;
        u.developer = developer.isBlank() ? username : developer;
        u.passwordHash = newHash(password);
        try {
            users.save(u);
        } catch (DataIntegrityViolationException e) {
            // 唯一约束兜住并发同名注册；不返回 500
            return ResponseEntity.badRequest().body(Map.of("error", "用户名已存在"));
        }
        String token = Sessions.issue(redis, username);
        return ResponseEntity.ok(Map.of("token", token, "user", Sessions.publicUser(u)));
    }

    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody Map<String, String> body) {
        String username = (body.getOrDefault("username", "")).trim().toLowerCase();
        String password = body.getOrDefault("password", "");
        // 限流：每 IP 每分钟最多 10 次登录尝试（Redis INCR + EXPIRE）。
        // 放在人机验证之前，避免被无效 token 刷外呼（同 register）。
        String ip = reqIp();
        Long n = redis.opsForValue().increment(RATE + "login:" + ip);
        if (n != null && n == 1) redis.expire(RATE + "login:" + ip, Duration.ofMinutes(1));
        if (n != null && n > 10) {
            return ResponseEntity.status(429).body(Map.of("error", "尝试过于频繁，请稍后再试"));
        }
        // 人机验证（默认关闭，见 market.turnstile.enabled）
        if (!turnstile.verify(body.getOrDefault("turnstileToken", ""), ip)) {
            return ResponseEntity.badRequest().body(Map.of("error", CAPTCHA_ERR));
        }
        if (password.length() > PWD_MAX) {
            return ResponseEntity.status(401).body(Map.of("error", "用户名或密码错误"));
        }
        User u = users.findByUsername(username).orElse(null);
        if (u == null || !verify(password, u.passwordHash)) {
            return ResponseEntity.status(401).body(Map.of("error", "用户名或密码错误"));
        }
        if (!u.passwordHash.startsWith(PREFIX)) {
            // 存量旧哈希：登录成功即透明升级，不打断用户
            u.passwordHash = newHash(password);
            users.save(u);
        }
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

    /**
     * 取客户端 IP：X-Forwarded-For 首个跳 > X-Real-IP > remoteAddr。
     * 注意：XFF 可被客户端伪造，仅当本服务部署在可信反代之后（反代会覆写 XFF）才有意义，
     * 否则限流维度可被绕过；直连部署请只认 remoteAddr。
     */
    private String reqIp() {
        var s = org.springframework.web.context.request.RequestContextHolder.getRequestAttributes();
        if (s instanceof org.springframework.web.context.request.ServletRequestAttributes a) {
            String xff = a.getRequest().getHeader("X-Forwarded-For");
            if (xff != null && !xff.isBlank()) {
                int comma = xff.indexOf(',');
                String first = (comma > 0 ? xff.substring(0, comma) : xff).trim();
                if (!first.isEmpty()) return first;
            }
            String x = a.getRequest().getHeader("X-Real-IP");
            if (x != null && !x.isBlank()) return x.trim();
            return a.getRequest().getRemoteAddr();
        }
        return "unknown";
    }
}
