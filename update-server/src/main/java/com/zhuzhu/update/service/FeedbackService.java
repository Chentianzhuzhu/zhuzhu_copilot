package com.zhuzhu.update.service;

import com.zhuzhu.update.entity.Feedback;
import com.zhuzhu.update.repo.FeedbackRepo;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/** 用户反馈：提交（含 IP 当日限流）、列表、回复与状态流转 */
@Service
public class FeedbackService {

    /** 每个 IP 每日反馈上限 */
    public static final int DAILY_LIMIT = 3;

    /** 反馈内容最大长度 */
    private static final int MAX_CONTENT = 2000;

    /** 查询凭证字节数（32 字节 → 64 位 hex 字符串） */
    private static final int TOKEN_BYTES = 32;

    private static final List<String> ALLOWED_STATUS = List.of("pending", "replied", "resolved");

    /** 加密安全随机数生成器，用于生成查询凭证 */
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private final FeedbackRepo repo;

    public FeedbackService(FeedbackRepo repo) {
        this.repo = repo;
    }

    /** 生成 64 位 hex 查询凭证（32 字节随机数，256 位熵，不可遍历） */
    private String generateToken() {
        byte[] bytes = new byte[TOKEN_BYTES];
        SECURE_RANDOM.nextBytes(bytes);
        StringBuilder sb = new StringBuilder(TOKEN_BYTES * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    /**
     * 提交反馈。
     *
     * <p>限流一律以服务器端 {@link LocalDateTime#now()} 为准（当天 0 点起计数），
     * 不信任客户端传来的任何时间；同一 IP 当日提交达到 {@link #DAILY_LIMIT} 次即拒绝。
     * 使用 @Transactional 保证计数与保存处于同一事务，避免并发下计数漂移。
     */
    @Transactional
    public Feedback submit(String ip, String content, String contact) {
        if (content == null || content.isBlank()) {
            throw new IllegalArgumentException("反馈内容不能为空");
        }
        if (content.length() > MAX_CONTENT) {
            throw new IllegalArgumentException("反馈内容不能超过 2000 字");
        }
        // 统一 trim IP，避免代理头空白导致同一用户被计为不同 IP
        String cleanIp = ip == null ? "" : ip.trim();
        LocalDateTime todayStart = LocalDateTime.now().toLocalDate().atStartOfDay();
        if (repo.countTodayByIp(cleanIp, todayStart) >= DAILY_LIMIT) {
            throw new RateLimitException("今日反馈次数已达上限（3次/天）");
        }
        Feedback f = new Feedback();
        f.setIp(cleanIp);
        f.setContent(content.trim());
        if (contact != null && !contact.isBlank()) {
            f.setContact(contact.trim());
        }
        f.setStatus("pending");
        f.setCreatedAt(LocalDateTime.now());
        // 生成查询凭证（64 位 hex，防止数字 ID 被遍历查看他人反馈）
        f.setQueryToken(generateToken());
        return repo.save(f);
    }

    /** 全部反馈按提交时间倒序 */
    public List<Feedback> list() {
        return repo.findAllByOrderByCreatedAtDesc();
    }

    /** 凭查询凭证查找反馈（公开查询用，不暴露 IP / 联系方式）。token 为空或不存在返回 empty。 */
    public Optional<Feedback> findByQueryToken(String token) {
        if (token == null || token.isBlank()) {
            return Optional.empty();
        }
        return repo.findByQueryToken(token.trim());
    }

    /** 管理员回复：写入回复内容并标记为 replied */
    public Feedback reply(Long id, String replyText) {
        Feedback f = repo.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("反馈不存在"));
        if (replyText == null || replyText.isBlank()) {
            throw new IllegalArgumentException("回复内容不能为空");
        }
        f.setReply(replyText.trim());
        f.setStatus("replied");
        f.setRepliedAt(LocalDateTime.now());
        return repo.save(f);
    }

    /** 标记状态（pending / replied / resolved） */
    public Feedback setStatus(Long id, String status) {
        Feedback f = repo.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("反馈不存在"));
        if (status == null || !ALLOWED_STATUS.contains(status)) {
            throw new IllegalArgumentException("非法状态：" + status);
        }
        f.setStatus(status);
        return repo.save(f);
    }

    /** 当日限流异常：Web 层据此返回 HTTP 429 */
    public static class RateLimitException extends RuntimeException {
        public RateLimitException(String message) {
            super(message);
        }
    }
}
