package com.zhuzhu.pay.web;

import com.zhuzhu.pay.service.PayException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Map;

/** 全局异常：业务异常转状态码，未预期异常兜底 500 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(PayException.class)
    public ResponseEntity<Map<String, Object>> onPay(PayException e) {
        return ResponseEntity.status(e.getStatus())
                .body(Map.of("code", e.getStatus(), "detail", e.getMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> onOther(Exception e) {
        log.error("未预期异常", e);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("code", 500, "detail", "服务内部错误"));
    }
}
