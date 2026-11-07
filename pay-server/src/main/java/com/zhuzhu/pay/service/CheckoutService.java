package com.zhuzhu.pay.service;

import com.stripe.Stripe;
import com.stripe.exception.StripeException;
import com.stripe.model.checkout.Session;
import com.stripe.param.checkout.SessionCreateParams;
import com.zhuzhu.pay.config.PayProperties;
import com.zhuzhu.pay.model.PayOrder;
import com.zhuzhu.pay.repo.PayOrderRepo;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/** Stripe Checkout 下单 */
@Service
public class CheckoutService {

    private static final Logger log = LoggerFactory.getLogger(CheckoutService.class);
    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");
    private static final SecureRandom RNG = new SecureRandom();

    private final PayProperties props;
    private final PayOrderRepo repo;
    private final AuthServerClient auth;

    @Value("${stripe.secret-key}")
    private String secretKey;

    @Value("${stripe.currency}")
    private String currency;

    public CheckoutService(PayProperties props, PayOrderRepo repo, AuthServerClient auth) {
        this.props = props;
        this.repo = repo;
        this.auth = auth;
    }

    /** 创建订单 + Stripe Checkout 会话，返回跳转 URL */
    @Transactional
    public MapResult createCheckout(String bearerToken, String productId) {
        PayProperties.ProductDef product = props.getProducts().get(productId);
        if (product == null) {
            throw new PayException(400, "商品不存在");
        }
        if (secretKey == null || secretKey.isBlank()) {
            throw new PayException(500, "服务端未配置 Stripe 密钥");
        }

        AuthServerClient.MeInfo me = auth.me(bearerToken);
        auth.assertPurchasable(bearerToken, productId);

        PayOrder order = new PayOrder();
        order.setOrderId(newOrderId());
        order.setUserId(me.id());
        order.setUsername(me.username());
        order.setProductId(productId);
        order.setCurrency(currency);
        order.setAmount(product.getAmount());
        order.setStatus("pending");
        repo.save(order);

        Stripe.apiKey = secretKey;
        long expireAt = Instant.now().plusSeconds(props.getSessionExpireMinutes() * 60L).getEpochSecond();
        try {
            Session session = Session.create(SessionCreateParams.builder()
                    .setMode(SessionCreateParams.Mode.PAYMENT)
                    .setClientReferenceId(order.getOrderId())
                    .setExpiresAt(expireAt)
                    .addLineItem(SessionCreateParams.LineItem.builder()
                            .setQuantity(1L)
                            .setPriceData(SessionCreateParams.LineItem.PriceData.builder()
                                    .setCurrency(currency)
                                    .setUnitAmount(product.getAmount())
                                    .setProductData(SessionCreateParams.LineItem.PriceData.ProductData.builder()
                                            .setName(product.getName())
                                            .build())
                                    .build())
                            .build())
                    .setSuccessUrl(props.getSuccessUrl().replace("{ORDER_ID}", order.getOrderId()))
                    .setCancelUrl(props.getCancelUrl().replace("{ORDER_ID}", order.getOrderId()))
                    .putMetadata("pay_order_id", order.getOrderId())
                    .putMetadata("user_id", String.valueOf(me.id()))
                    .putMetadata("product_id", productId)
                    .build());
            order.setStripeSessionId(session.getId());
            repo.save(order);
            log.info("创建 Checkout 会话 order={} user={} product={}", order.getOrderId(), me.id(), productId);
            return new MapResult(order.getOrderId(), session.getUrl());
        } catch (StripeException e) {
            log.error("Stripe 下单失败 order={}: {}", order.getOrderId(), e.getMessage());
            throw new PayException(502, "支付渠道下单失败，请稍后重试");
        }
    }

    public record MapResult(String orderId, String checkoutUrl) {}

    /** 订单号：PAY + 时间戳 + 4 位随机段 */
    private static String newOrderId() {
        return "PAY" + LocalDateTime.now().format(TS)
                + String.format("%04d", RNG.nextInt(10000));
    }
}
