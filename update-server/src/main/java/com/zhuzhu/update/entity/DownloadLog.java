package com.zhuzhu.update.entity;

import jakarta.persistence.*;

import java.time.LocalDate;

/** 单次安装包下载记录（用于总下载量与趋势统计） */
@Entity
@Table(name = "download_log", indexes = {
        @Index(name = "idx_log_version", columnList = "version_id"),
        @Index(name = "idx_log_date", columnList = "created_date")
})
public class DownloadLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "version_id", nullable = false)
    private Long versionId;

    /** 下载方 IP（nginx 透传 real_ip） */
    @Column(length = 64)
    private String ip;

    @Column(name = "user_agent", length = 255)
    private String userAgent;

    /** 按本地日期归档，趋势统计直接按此字段分组 */
    @Column(name = "created_date", nullable = false)
    private LocalDate createdDate = LocalDate.now();

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getVersionId() { return versionId; }
    public void setVersionId(Long versionId) { this.versionId = versionId; }
    public String getIp() { return ip; }
    public void setIp(String ip) { this.ip = ip; }
    public String getUserAgent() { return userAgent; }
    public void setUserAgent(String userAgent) { this.userAgent = userAgent; }
    public LocalDate getCreatedDate() { return createdDate; }
    public void setCreatedDate(LocalDate createdDate) { this.createdDate = createdDate; }
}
