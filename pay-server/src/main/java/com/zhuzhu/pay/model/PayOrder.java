package com.zhuzhu.pay.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.LocalDateTime;

/** 支付订单（pay-server 自有订单体系，不与 auth-server orders 表耦合） */
@Entity
@Table(name = "pay_orders", indexes = {
        @Index(name = "idx_session", columnList = "stripeSessionId"),
        @Index(name = "idx_user", columnList = "userId")
})
public class PayOrder {

    /** 订单号：PAY + 日期 + 随机段 */
    @Id
    @Column(name = "order_id", length = 32, nullable = false)
    private String orderId;

    /** auth-server 用户 id */
    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(name = "username", length = 64)
    private String username;

    /** auth-server PRODUCTS 的 product_id（发货单一事实来源在 auth-server） */
    @Column(name = "product_id", length = 64, nullable = false)
    private String productId;

    @Column(name = "currency", length = 8, nullable = false)
    private String currency;

    /** 最小货币单位（分） */
    @Column(name = "amount", nullable = false)
    private long amount;

    /** pending / paid / canceled */
    @Column(name = "status", length = 16, nullable = false)
    private String status = "pending";

    /** 发放状态：pending / granted / failed（webhook 重放时据此幂等重试） */
    @Column(name = "grant_status", length = 16, nullable = false)
    private String grantStatus = "pending";

    @Column(name = "grant_error", length = 512)
    private String grantError;

    @Column(name = "stripe_session_id", length = 255)
    private String stripeSessionId;

    @Column(name = "stripe_payment_intent", length = 255)
    private String stripePaymentIntent;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt = LocalDateTime.now();

    @Column(name = "paid_at")
    private LocalDateTime paidAt;

    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
    public Long getUserId() { return userId; }
    public void setUserId(Long userId) { this.userId = userId; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getProductId() { return productId; }
    public void setProductId(String productId) { this.productId = productId; }
    public String getCurrency() { return currency; }
    public void setCurrency(String currency) { this.currency = currency; }
    public long getAmount() { return amount; }
    public void setAmount(long amount) { this.amount = amount; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getGrantStatus() { return grantStatus; }
    public void setGrantStatus(String grantStatus) { this.grantStatus = grantStatus; }
    public String getGrantError() { return grantError; }
    public void setGrantError(String grantError) { this.grantError = grantError; }
    public String getStripeSessionId() { return stripeSessionId; }
    public void setStripeSessionId(String stripeSessionId) { this.stripeSessionId = stripeSessionId; }
    public String getStripePaymentIntent() { return stripePaymentIntent; }
    public void setStripePaymentIntent(String stripePaymentIntent) { this.stripePaymentIntent = stripePaymentIntent; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getPaidAt() { return paidAt; }
    public void setPaidAt(LocalDateTime paidAt) { this.paidAt = paidAt; }
}
