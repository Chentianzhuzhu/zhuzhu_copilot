package com.zhuzhu.pay.service;

import com.stripe.model.Event;
import com.stripe.model.checkout.Session;
import com.stripe.net.Webhook;
import com.zhuzhu.pay.config.StripeProperties;
import com.zhuzhu.pay.model.PayOrder;
import com.zhuzhu.pay.repo.PayOrderRepo;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/** Stripe webhook 处理：验签 + 幂等确认 + 回调发货 */
@Service
public class WebhookService {

    private static final Logger log = LoggerFactory.getLogger(WebhookService.class);

    private final PayOrderRepo repo;
    private final AuthServerClient auth;
    private final StripeProperties stripe;

    public WebhookService(PayOrderRepo repo, AuthServerClient auth, StripeProperties stripe) {
        this.repo = repo;
        this.auth = auth;
        this.stripe = stripe;
    }

    /**
     * 处理 webhook 原始报文。返回 null 表示事件已忽略（无需重试），
     * 返回 pay_order_id 表示已确认支付。
     */
    public String handle(String payload, String sigHeader) {
        String webhookSecret = stripe.getWebhookSecret();
        if (webhookSecret == null || webhookSecret.isBlank()) {
            throw new PayException(500, "服务端未配置 Webhook 签名密钥");
        }
        Event event;
        try {
            event = Webhook.constructEvent(payload, sigHeader, webhookSecret);
        } catch (Exception e) {
            log.warn("Webhook 验签失败: {}", e.getMessage());
            throw new PayException(400, "签名校验失败");
        }

        if (!"checkout.session.completed".equals(event.getType())) {
            return null;
        }
        Session session = (Session) event.getDataObjectDeserializer().getObject().orElse(null);
        if (session == null) {
            return null;
        }
        return confirmPaid(session);
    }

    /** 幂等确认：pending→paid 只有第一条请求成功；已 paid 但发放未成功的重试发货 */
    private String confirmPaid(Session session) {
        String orderId = session.getMetadata() != null
                ? session.getMetadata().get("pay_order_id") : null;
        if (orderId == null) {
            orderId = session.getClientReferenceId();
        }
        if (orderId == null) {
            log.warn("checkout.session.completed 缺少订单标识 session={}", session.getId());
            return null;
        }
        // 不信任金额与商品来自 metadata 的金额字段，发货以本地订单为准
        PayOrder order = repo.findById(orderId).orElse(null);
        if (order == null) {
            log.warn("webhook 订单不存在 order={}", orderId);
            return null;
        }

        int updated = repo.markPaidIfPending(orderId, session.getPaymentIntent());
        if (updated == 1) {
            log.info("订单支付确认 order={} amount={} {}", orderId, order.getAmount(), order.getCurrency());
        }
        deliver(orderId);
        return orderId;
    }

    /** 发货（幂等）：granted 直接跳过；failed/pending 重试 */
    public void deliver(String orderId) {
        PayOrder order = repo.findById(orderId).orElse(null);
        if (order == null || !"paid".equals(order.getStatus())) {
            return;
        }
        if ("granted".equals(order.getGrantStatus())) {
            return;
        }
        try {
            auth.grant(order.getUserId(), order.getProductId(), orderId);
            order.setGrantStatus("granted");
            order.setGrantError(null);
            repo.save(order);
            log.info("发货成功 order={} user={} product={}", orderId, order.getUserId(), order.getProductId());
        } catch (Exception e) {
            order.setGrantStatus("failed");
            order.setGrantError(abbreviate(e.getMessage()));
            repo.save(order);
            // 抛出走 Stripe 自动重试（webhook 非法状态码会触发重发）
            throw new PayException(502, "发货失败，等待 webhook 重试");
        }
    }

    private static String abbreviate(String msg) {
        if (msg == null) return "unknown";
        return msg.length() > 500 ? msg.substring(0, 500) : msg;
    }
}
