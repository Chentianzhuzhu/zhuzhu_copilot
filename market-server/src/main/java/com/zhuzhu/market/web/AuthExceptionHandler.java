package com.zhuzhu.market.web;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Map;

/** 登录态缺失统一回 401（避免未捕获异常被 Spring 兜成 500）。 */
@RestControllerAdvice
public class AuthExceptionHandler {

    @ExceptionHandler(Sessions.AuthException.class)
    public ResponseEntity<?> onAuth(Sessions.AuthException e) {
        return ResponseEntity.status(401).body(Map.of(
                "error", e.getMessage() == null ? "需要登录" : e.getMessage()));
    }
}
