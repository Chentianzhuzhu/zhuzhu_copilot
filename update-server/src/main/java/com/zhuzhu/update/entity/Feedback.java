package com.zhuzhu.update.entity;

import jakarta.persistence.*;

import java.time.LocalDateTime;

/** 用户反馈 */
@Entity
@Table(name = "feedback")
public class Feedback {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 提交者 IP */
    @Column(length = 64)
    private String ip;

    /** 反馈内容（非空，最长 2000 字） */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    /** 联系方式（可选：邮箱/QQ/微信等） */
    @Column(length = 255)
    private String contact;

    /** 状态：pending / replied / resolved */
    @Column(length = 16, nullable = false)
    private String status = "pending";

    /** 管理员回复（可选） */
    @Column(columnDefinition = "TEXT")
    private String reply;

    /** 提交时间（服务器端时间） */
    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt = LocalDateTime.now();

    /** 回复时间（可选） */
    @Column(name = "replied_at")
    private LocalDateTime repliedAt;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getIp() { return ip; }
    public void setIp(String ip) { this.ip = ip; }
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    public String getContact() { return contact; }
    public void setContact(String contact) { this.contact = contact; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getReply() { return reply; }
    public void setReply(String reply) { this.reply = reply; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getRepliedAt() { return repliedAt; }
    public void setRepliedAt(LocalDateTime repliedAt) { this.repliedAt = repliedAt; }
}
