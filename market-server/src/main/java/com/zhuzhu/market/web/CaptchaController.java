package com.zhuzhu.market.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 人机验证配置：只下发公开的站点密钥（secret 永不出服务端）。
 * 浏览器页面与客户端内置浏览器都从这里取 site key 渲染 Turnstile 组件。
 */
@RestController
@RequestMapping("/api/captcha")
public class CaptchaController {
    private final TurnstileService turnstile;

    public CaptchaController(TurnstileService turnstile) {
        this.turnstile = turnstile;
    }

    @GetMapping("/sitekey")
    public Map<String, String> sitekey() {
        return Map.of("sitekey", turnstile.siteKey());
    }
}
