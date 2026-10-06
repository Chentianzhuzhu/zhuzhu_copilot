package com.zhuzhu.update.web;

import com.zhuzhu.update.entity.Feedback;
import com.zhuzhu.update.service.FeedbackService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 用户反馈：公开提交接口 + 管理端查看/回复/状态（/admin/api/** 自动走 AdminAuthInterceptor） */
@RestController
public class FeedbackController {

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /**
     * 提交锁：必须在 Controller 层（事务之外）获取，包裹整个 submit 调用。
     * 原因：@Transactional 由 Spring 代理在方法外开启/提交，若只在 Service 方法上加 synchronized，
     * 两个线程可在「退出 synchronized 块」与「事务提交」之间交错，第二个事务仍读到旧 count → 超限。
     * Controller 层锁保证「事务开启 → count 查询 → insert → 事务提交」全程原子化。
     */
    private static final Object SUBMIT_LOCK = new Object();

    private final FeedbackService feedbackService;

    public FeedbackController(FeedbackService feedbackService) {
        this.feedbackService = feedbackService;
    }

    /** 公开提交反馈：body {"content": "...", "contact": "..."}。返回查询凭证 token（64位hex）。 */
    @PostMapping("/api/feedback")
    public ResponseEntity<Map<String, Object>> submit(@RequestBody Map<String, String> body,
                                                     HttpServletRequest request) {
        String ip = clientIp(request);
        try {
            Feedback f;
            synchronized (SUBMIT_LOCK) {
                f = feedbackService.submit(ip, body.get("content"), body.get("contact"));
            }
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ok", true);
            out.put("id", f.getId());
            out.put("token", f.getQueryToken());
            return ResponseEntity.ok(out);
        } catch (FeedbackService.RateLimitException e) {
            // 当日提交超限：429 Too Many Requests
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("error", e.getMessage());
            out.put("limit", FeedbackService.DAILY_LIMIT);
            return ResponseEntity.status(429).body(out);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }
    }

    /**
     * 公开查询反馈及回复：用户凭提交时获得的查询凭证（64位hex）查看处理进度与官方回复。
     * 使用随机长字符串凭证而非数字 ID，防止遍历查看他人反馈。
     * 仅返回内容/状态/回复/时间，不暴露 IP 与联系方式（隐私保护）。
     */
    @GetMapping("/api/feedback/{token}")
    public ResponseEntity<Map<String, Object>> get(@PathVariable String token) {
        return feedbackService.findByQueryToken(token).map(f -> {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ok", true);
            out.put("id", f.getId());
            out.put("content", f.getContent());
            out.put("status", f.getStatus());
            out.put("reply", f.getReply());
            out.put("createdAt", f.getCreatedAt() == null ? "" : f.getCreatedAt().format(FMT));
            out.put("repliedAt", f.getRepliedAt() == null ? "" : f.getRepliedAt().format(FMT));
            return ResponseEntity.ok(out);
        }).orElseGet(() -> ResponseEntity.status(404).body(Map.of("error", "查询凭证无效或反馈不存在，请检查凭证是否正确")));
    }

    /** 管理端反馈列表（按时间倒序），含查询凭证便于管理员告知用户 */
    @GetMapping("/admin/api/feedbacks")
    public Map<String, Object> list() {
        List<Map<String, Object>> items = new ArrayList<>();
        for (Feedback f : feedbackService.list()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", f.getId());
            m.put("ip", f.getIp());
            m.put("content", f.getContent());
            m.put("contact", f.getContact());
            m.put("status", f.getStatus());
            m.put("reply", f.getReply());
            m.put("queryToken", f.getQueryToken());
            m.put("createdAt", f.getCreatedAt() == null ? "" : f.getCreatedAt().format(FMT));
            m.put("repliedAt", f.getRepliedAt() == null ? "" : f.getRepliedAt().format(FMT));
            items.add(m);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("items", items);
        return out;
    }

    /** 管理端回复反馈：body {"reply": "..."} */
    @PostMapping("/admin/api/feedback/{id}/reply")
    public Map<String, Object> reply(@PathVariable Long id, @RequestBody Map<String, String> body) {
        try {
            feedbackService.reply(id, body.get("reply"));
        } catch (IllegalArgumentException e) {
            return Map.of("error", e.getMessage());
        }
        return Map.of("ok", true);
    }

    /** 管理端标记反馈状态：body {"status": "resolved"} */
    @PostMapping("/admin/api/feedback/{id}/status")
    public Map<String, Object> status(@PathVariable Long id, @RequestBody Map<String, String> body) {
        try {
            feedbackService.setStatus(id, body.get("status"));
        } catch (IllegalArgumentException e) {
            return Map.of("error", e.getMessage());
        }
        return Map.of("ok", true);
    }

    /** 管理端一键清空全部反馈（不可恢复） */
    @DeleteMapping("/admin/api/feedbacks")
    public Map<String, Object> clearAll() {
        int deleted = feedbackService.clearAll();
        return Map.of("ok", true, "deleted", deleted);
    }

    /**
     * 客户端真实 IP：依次尝试 X-Real-IP → X-Forwarded-For 首个 IP → remoteAddr。
     * 多级反代/CDN 下 X-Real-IP 可能缺失，X-Forwarded-For 为逗号分隔列表，取最左侧即原始客户端 IP。
     */
    private String clientIp(HttpServletRequest request) {
        String real = request.getHeader("X-Real-IP");
        if (real != null && !real.isBlank()) {
            return real.trim();
        }
        String xff = request.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            String first = xff.split(",")[0].trim();
            if (!first.isEmpty()) {
                return first;
            }
        }
        return request.getRemoteAddr();
    }
}
