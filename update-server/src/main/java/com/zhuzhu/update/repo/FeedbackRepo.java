package com.zhuzhu.update.repo;

import com.zhuzhu.update.entity.Feedback;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface FeedbackRepo extends JpaRepository<Feedback, Long> {

    /** 管理端列表：按提交时间倒序 */
    List<Feedback> findAllByOrderByCreatedAtDesc();

    /**
     * 当日 IP 提交计数（限流用）。
     * 使用显式 @Query 避免派生查询方法名中 After 关键字与 createdAt 属性的解析歧义，
     * >= 确保包含当天 0 点整的记录。
     */
    @Query("SELECT COUNT(f) FROM Feedback f WHERE f.ip = :ip AND f.createdAt >= :start")
    long countTodayByIp(@Param("ip") String ip, @Param("start") LocalDateTime start);
}
