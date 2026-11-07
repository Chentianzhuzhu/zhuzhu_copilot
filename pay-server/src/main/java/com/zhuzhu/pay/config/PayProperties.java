package com.zhuzhu.pay.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * pay-server 配置。所有敏感值（密钥/令牌）经环境变量注入，本类只承载结构。
 */
@ConfigurationProperties(prefix = "pay")
public class PayProperties {

    /** auth-server 基础地址（同机回环即可） */
    private String authBaseUrl;

    /** 内部接口令牌：与 auth-server INTERNAL_API_TOKEN 一致 */
    private String internalToken;

    /** 支付完成跳转页，{ORDER_ID} 占位符 */
    private String successUrl;

    /** 取消支付跳转页，{ORDER_ID} 占位符 */
    private String cancelUrl;

    /** Checkout 会话有效期（分钟） */
    private int sessionExpireMinutes = 30;

    /** 商品表：key = auth-server product_id */
    private Map<String, ProductDef> products = new LinkedHashMap<>();

    public static class ProductDef {
        private String name;
        /** 最小货币单位（分） */
        private long amount;
        /** 覆盖 stripe.tax-code 的商品税码（可为 null） */
        private String taxCode;

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }
        public long getAmount() { return amount; }
        public void setAmount(long amount) { this.amount = amount; }
        public String getTaxCode() { return taxCode; }
        public void setTaxCode(String taxCode) { this.taxCode = taxCode; }
    }

    public String getAuthBaseUrl() { return authBaseUrl; }
    public void setAuthBaseUrl(String authBaseUrl) { this.authBaseUrl = authBaseUrl; }
    public String getInternalToken() { return internalToken; }
    public void setInternalToken(String internalToken) { this.internalToken = internalToken; }
    public String getSuccessUrl() { return successUrl; }
    public void setSuccessUrl(String successUrl) { this.successUrl = successUrl; }
    public String getCancelUrl() { return cancelUrl; }
    public void setCancelUrl(String cancelUrl) { this.cancelUrl = cancelUrl; }
    public int getSessionExpireMinutes() { return sessionExpireMinutes; }
    public void setSessionExpireMinutes(int sessionExpireMinutes) { this.sessionExpireMinutes = sessionExpireMinutes; }
    public Map<String, ProductDef> getProducts() { return products; }
    public void setProducts(Map<String, ProductDef> products) { this.products = products; }
}
