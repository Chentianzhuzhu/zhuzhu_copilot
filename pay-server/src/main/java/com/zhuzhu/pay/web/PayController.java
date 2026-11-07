package com.zhuzhu.pay.web;

import com.zhuzhu.pay.model.PayOrder;
import com.zhuzhu.pay.repo.PayOrderRepo;
import com.zhuzhu.pay.service.AuthServerClient;
import com.zhuzhu.pay.service.CheckoutService;
import com.zhuzhu.pay.service.PayException;
import jakarta.validation.constraints.NotBlank;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/** 支付下单 / 订单查询 */
@RestController
@RequestMapping("/api/pay")
public class PayController {

    private final CheckoutService checkout;
    private final PayOrderRepo repo;
    private final AuthServerClient auth;

    public PayController(CheckoutService checkout, PayOrderRepo repo, AuthServerClient auth) {
        this.checkout = checkout;
        this.repo = repo;
        this.auth = auth;
    }

    public record CheckoutRequest(@NotBlank String product_id) {}

    /** 创建订单并返回 Stripe Checkout 跳转 URL（需用户 Bearer token） */
    @PostMapping("/checkout")
    public Map<String, Object> checkout(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestBody CheckoutRequest req) {
        String token = bearer(authorization);
        CheckoutService.MapResult r = checkout.createCheckout(token, req.product_id());
        return Map.of("code", 0, "data", Map.of(
                "order_id", r.orderId(),
                "checkout_url", r.checkoutUrl()
        ));
    }

    /** 查询订单状态（仅订单归属人可查） */
    @GetMapping("/order/{orderId}")
    public Map<String, Object> orderStatus(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @PathVariable String orderId) {
        String token = bearer(authorization);
        AuthServerClient.MeInfo me = auth.me(token);
        PayOrder order = repo.findById(orderId).orElseThrow(() -> new PayException(404, "订单不存在"));
        if (order.getUserId() != me.id()) {
            throw new PayException(404, "订单不存在");
        }
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("order_id", order.getOrderId());
        data.put("product_id", order.getProductId());
        data.put("amount", order.getAmount());
        data.put("currency", order.getCurrency());
        data.put("status", order.getStatus());
        data.put("grant_status", order.getGrantStatus());
        data.put("created_at", order.getCreatedAt() == null ? null : order.getCreatedAt().toString());
        data.put("paid_at", order.getPaidAt() == null ? null : order.getPaidAt().toString());
        return Map.of("code", 0, "data", data);
    }

    private static String bearer(String authorization) {
        if (authorization != null && authorization.startsWith("Bearer ")) {
            String t = authorization.substring(7).trim();
            if (!t.isEmpty()) {
                return t;
            }
        }
        throw new PayException(401, "缺少登录凭证");
    }
}
