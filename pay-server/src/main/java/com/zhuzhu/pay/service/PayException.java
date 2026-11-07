package com.zhuzhu.pay.service;

/** 业务异常：携带建议的 HTTP 状态码 */
public class PayException extends RuntimeException {
    private final int status;

    public PayException(int status, String message) {
        super(message);
        this.status = status;
    }

    public int getStatus() { return status; }
}
