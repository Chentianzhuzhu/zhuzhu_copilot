package com.zhuzhu.pay.service;

import com.zhuzhu.pay.config.PayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * auth-server 客户端：验用户身份、取商品可购性、回调内部发放接口。
 * 发货规则（等级只升不降/时长叠加/积分流水/WS 推送）全部收敛在 auth-server 一处。
 */
@Service
public class AuthServerClient {

    private static final Logger log = LoggerFactory.getLogger(AuthServerClient.class);

    private final RestClient http;
    private final PayProperties props;

    public AuthServerClient(PayProperties props) {
        this.props = props;
        this.http = RestClient.builder()
                .baseUrl(props.getAuthBaseUrl())
                .build();
    }

    /** 用户信息（/api/auth/me） */
    public record MeInfo(long id, String username) {}

    /** 校验用户 token，返回用户 id/username；无效则抛 401 */
    @SuppressWarnings("unchecked")
    public MeInfo me(String bearerToken) {
        Map<String, Object> resp;
        try {
            resp = http.get()
                    .uri("/api/auth/me")
                    .header(HttpHeaders.AUTHORIZATION, "Bearer " + bearerToken)
                    .retrieve()
                    .body(Map.class);
        } catch (Exception e) {
            log.warn("auth /me 请求失败: {}", e.getMessage());
            throw new PayException(401, "登录状态无效");
        }
        Map<String, Object> data = data(resp);
        if (data == null || data.get("id") == null) {
            throw new PayException(401, "登录状态无效");
        }
        return new MeInfo(((Number) data.get("id")).longValue(),
                String.valueOf(data.get("username")));
    }

    /** 校验商品在 auth-server 侧是否可购（会员类含等级裁决），不可购抛 400 */
    @SuppressWarnings("unchecked")
    public void assertPurchasable(String bearerToken, String productId) {
        Map<String, Object> resp;
        try {
            resp = http.get()
                    .uri("/api/shop/products")
                    .header(HttpHeaders.AUTHORIZATION, "Bearer " + bearerToken)
                    .retrieve()
                    .body(Map.class);
        } catch (Exception e) {
            throw new PayException(502, "商品服务暂不可用");
        }
        Map<String, Object> data = data(resp);
        if (data == null) {
            throw new PayException(502, "商品服务暂不可用");
        }
        for (String group : new String[]{"memberships", "point_packs"}) {
            Object list = data.get(group);
            if (!(list instanceof Iterable<?> items)) {
                continue;
            }
            for (Object o : items) {
                if (o instanceof Map<?, ?> item && productId.equals(item.get("id"))) {
                    if (Boolean.FALSE.equals(item.get("purchasable"))) {
                        String reason = String.valueOf(item.get("reason"));
                        throw new PayException(400, reason.isEmpty() ? "当前无法购买该商品" : reason);
                    }
                    return;
                }
            }
        }
        throw new PayException(400, "商品不存在");
    }

    /** 回调 auth-server 内部发放接口（会员/积分），失败抛 502（webhook 重放可重试） */
    public void grant(long userId, String productId, String orderId) {
        if (props.getInternalToken() == null || props.getInternalToken().isBlank()) {
            throw new PayException(500, "服务端未配置内部发放令牌");
        }
        try {
            Map<String, Object> resp = http.post()
                    .uri("/api/internal/grant")
                    .header("X-Internal-Token", props.getInternalToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of(
                            "user_id", userId,
                            "product_id", productId,
                            "order_id", orderId,
                            "source", "stripe"
                    ))
                    .retrieve()
                    .body(Map.class);
            if (resp == null || ((Number) resp.getOrDefault("code", -1)).intValue() != 0) {
                throw new PayException(502, "发货接口返回异常");
            }
        } catch (PayException e) {
            throw e;
        } catch (Exception e) {
            log.error("发货回调失败 order={} product={}: {}", orderId, productId, e.getMessage());
            throw new PayException(502, "发货回调失败，将在 webhook 重放时重试");
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> data(Map<String, Object> resp) {
        if (resp == null || ((Number) resp.getOrDefault("code", -1)).intValue() != 0) {
            return null;
        }
        return (Map<String, Object>) resp.get("data");
    }
}
