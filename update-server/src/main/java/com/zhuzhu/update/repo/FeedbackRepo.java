package com.zhuzhu.update.repo;

import com.zhuzhu.update.entity.Feedback;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface FeedbackRepo extends JpaRepository<Feedback, Long> {

    /** 管理端列表：按提交时间倒序 */
    List<Feedback> findAllByOrderByCreatedAtDesc();

    /** 当日 IP 提交计数（限流用，after 为当天 0 点，服务器端时间） */
    long countByIpAndCreatedAtAfter(String ip, LocalDateTime after);
}
