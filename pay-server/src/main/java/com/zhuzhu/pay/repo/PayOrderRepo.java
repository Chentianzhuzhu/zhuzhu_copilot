package com.zhuzhu.pay.repo;

import com.zhuzhu.pay.model.PayOrder;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface PayOrderRepo extends JpaRepository<PayOrder, String> {

    PayOrder findByStripeSessionId(String stripeSessionId);

    /**
     * 条件更新：仅当订单仍是 pending 时标记为 paid，返回受影响行数。
     * 并发 webhook 重放时只有一条请求能改成功，其余走幂等分支。
     */
    @Modifying
    @Query("UPDATE PayOrder o SET o.status = 'paid', o.paidAt = CURRENT_TIMESTAMP, " +
           "o.stripePaymentIntent = :pi WHERE o.orderId = :orderId AND o.status = 'pending'")
    int markPaidIfPending(@Param("orderId") String orderId, @Param("pi") String paymentIntent);

    /** 仅当 paid 且发放未成功时清空发放标记，供重试 */
    @Modifying
    @Query("UPDATE PayOrder o SET o.grantStatus = 'pending', o.grantError = NULL " +
           "WHERE o.orderId = :orderId AND o.status = 'paid' AND o.grantStatus <> 'granted'")
    int resetGrantIfRetryable(@Param("orderId") String orderId);
}
