package com.zhuzhu.update.entity;

import jakarta.persistence.*;

import java.time.LocalDateTime;

/** 官网内容（单行存储整个内容 JSON，后台编辑、前台读取） */
@Entity
@Table(name = "site_content")
public class SiteContent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 内容键，固定为 "site" */
    @Column(nullable = false, unique = true, length = 32)
    private String ckey;

    /** 官网内容 JSON（title/slogan/description/heroImage/features/stats） */
    @Column(name = "content_json", columnDefinition = "TEXT", nullable = false)
    private String contentJson;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt = LocalDateTime.now();

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getCkey() { return ckey; }
    public void setCkey(String ckey) { this.ckey = ckey; }
    public String getContentJson() { return contentJson; }
    public void setContentJson(String contentJson) { this.contentJson = contentJson; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(LocalDateTime updatedAt) { this.updatedAt = updatedAt; }
}
