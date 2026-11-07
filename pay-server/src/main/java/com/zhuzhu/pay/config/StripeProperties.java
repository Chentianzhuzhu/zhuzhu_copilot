package com.zhuzhu.pay.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Stripe 配置。密钥只从环境变量注入；税码/最低限额为账号级约束，集中在此便于调整。
 */
@ConfigurationProperties(prefix = "stripe")
public class StripeProperties {

    /** API 密钥（sk_ 或 rk_ 均可，rk_ 需具备 Checkout Sessions 写权限） */
    private String secretKey;

    /** Webhook 签名密钥（whsec_） */
    private String webhookSecret;

    /** 结算币种（小写 ISO 4217） */
    private String currency = "cny";

    /**
     * 默认商品税码。启用 Managed Payments 的账号强制要求 line_item 带 tax_code；
     * 默认「General - Electronically Supplied Services」，会员类商品可在
     * pay.products.*.tax-code 覆盖为 SaaS 类税码。
     */
    private String taxCode = "txcd_10000000";

    /**
     * 单笔最低金额（最小货币单位）。Stripe 按 USD 等值校验最低收款额，
     * 低于此值直接拒绝下单，避免打到 API 才报错。
     */
    private long minAmount = 400;

    public String getSecretKey() { return secretKey; }
    public void setSecretKey(String secretKey) { this.secretKey = secretKey; }
    public String getWebhookSecret() { return webhookSecret; }
    public void setWebhookSecret(String webhookSecret) { this.webhookSecret = webhookSecret; }
    public String getCurrency() { return currency; }
    public void setCurrency(String currency) { this.currency = currency; }
    public String getTaxCode() { return taxCode; }
    public void setTaxCode(String taxCode) { this.taxCode = taxCode; }
    public long getMinAmount() { return minAmount; }
    public void setMinAmount(long minAmount) { this.minAmount = minAmount; }
}
