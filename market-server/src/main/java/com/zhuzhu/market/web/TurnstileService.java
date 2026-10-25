package com.zhuzhu.market.web;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * Cloudflare Turnstile 人机验证（服务端校验）。
 *
 * 站点密钥（site key）是公开信息，通过 /api/captcha/sitekey 下发给浏览器/客户端；
 * 密钥（secret key）只在服务端使用，取自配置（可用环境变量覆盖），代码里不写死。
 * 校验服务不可用、token 为空或已过期时一律判失败——验证不通过就不允许注册/登录。
 */
@Service
public class TurnstileService {
    private static final Logger log = LoggerFactory.getLogger(TurnstileService.class);
    private static final String VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
    private static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(5);
    private static final Duration VERIFY_TIMEOUT = Duration.ofSeconds(8);

    private final String siteKey;
    private final String secretKey;
    /** 是否启用：产品口径是「人机验证只用于应用账户登录，市场登录暂不启用」。 */
    private final boolean enabled;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(CONNECT_TIMEOUT).build();
    private final ObjectMapper json = new ObjectMapper();

    public TurnstileService(@Value("${market.turnstile.site-key:}") String siteKey,
                            @Value("${market.turnstile.secret-key:}") String secretKey,
                            @Value("${market.turnstile.enabled:false}") boolean enabled) {
        this.siteKey = siteKey == null ? "" : siteKey.trim();
        this.secretKey = secretKey == null ? "" : secretKey.trim();
        this.enabled = enabled;
    }

    /** 是否启用校验（关闭时 verify 直接放行）。 */
    public boolean enabled() {
        return enabled;
    }

    /** 公开站点密钥（前端渲染验证组件用）。 */
    public String siteKey() {
        return siteKey;
    }

    /**
     * 校验前端提交的 Turnstile token。
     *
     * @param token    组件回调里拿到的一次性 token
     * @param remoteIp 客户端 IP（可空；Cloudflare 用于风控参考）
     * @return 仅当 Cloudflare 明确返回 success=true 才通过
     */
    public boolean verify(String token, String remoteIp) {
        if (!enabled) return true;   // 未启用 = 不校验（市场登录当前走这条）
        if (secretKey.isEmpty() || token == null || token.isBlank()) return false;
        StringBuilder form = new StringBuilder("secret=").append(enc(secretKey))
                .append("&response=").append(enc(token));
        if (remoteIp != null && !remoteIp.isBlank()) {
            form.append("&remoteip=").append(enc(remoteIp));
        }
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(VERIFY_URL))
                    .timeout(VERIFY_TIMEOUT)
                    .header("Content-Type", "application/x-www-form-urlencoded")
                    .POST(HttpRequest.BodyPublishers.ofString(form.toString()))
                    .build();
            HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (res.statusCode() != 200) {
                log.warn("Turnstile 校验接口返回 HTTP {}", res.statusCode());
                return false;
            }
            JsonNode obj = json.readTree(res.body());
            boolean ok = obj.path("success").asBoolean(false);
            if (!ok) {
                // 只记录错误码，不记录 token 本身
                log.info("Turnstile 校验未通过：{}", obj.path("error-codes"));
            }
            return ok;
        } catch (Exception e) {
            // 网络/解析失败一律拒绝（宁可让用户重试，也不静默放行）
            log.warn("Turnstile 校验失败：{}", e.toString());
            return false;
        }
    }

    private static String enc(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }
}
