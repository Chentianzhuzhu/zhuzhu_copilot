package com.zhuzhu.pay.web;

import com.zhuzhu.pay.service.PayException;
import com.zhuzhu.pay.service.WebhookService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.util.Map;

/** Stripe webhook 入口：必须读原始报文验签，不能让框架先反序列化 */
@RestController
@RequestMapping("/api/stripe")
public class StripeWebhookController {

    private static final Logger log = LoggerFactory.getLogger(StripeWebhookController.class);

    private final WebhookService webhook;

    public StripeWebhookController(WebhookService webhook) {
        this.webhook = webhook;
    }

    @PostMapping("/webhook")
    public ResponseEntity<byte[]> webhook(
            @RequestHeader(value = "Stripe-Signature", required = false) String sigHeader,
            @RequestBody byte[] rawBody) {
        String payload = new String(rawBody, StandardCharsets.UTF_8);
        if (sigHeader == null || sigHeader.isBlank()) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(b("缺少签名头"));
        }
        String orderId = webhook.handle(payload, sigHeader);
        if (orderId != null) {
            log.info("webhook 处理完成 order={}", orderId);
        }
        return ResponseEntity.ok(b("ok"));
    }

    /** PayException 统一转换为对应状态码（Stripe 依据状态码决定是否重试） */
    @ExceptionHandler(PayException.class)
    public ResponseEntity<byte[]> onPayException(PayException e) {
        return ResponseEntity.status(e.getStatus()).body(b(e.getMessage()));
    }

    private static byte[] b(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }
}
