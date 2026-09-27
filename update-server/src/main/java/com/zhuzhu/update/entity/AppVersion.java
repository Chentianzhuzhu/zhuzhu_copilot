package com.zhuzhu.update.entity;

import jakarta.persistence.*;

import java.time.LocalDateTime;

/** 已发布的更新版本（含安装包文件元数据） */
@Entity
@Table(name = "app_version")
public class AppVersion {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 语义化版本号，如 1.2.0 */
    @Column(nullable = false, unique = true, length = 32)
    private String version;

    /** 原始安装包文件名 */
    @Column(name = "file_name", nullable = false, length = 255)
    private String fileName;

    /** 相对上传根目录的存储路径，如 pkg/WinAppMigrator_1.2.0.exe */
    @Column(name = "file_path", nullable = false, length = 512)
    private String filePath;

    /** 文件字节数 */
    @Column(name = "file_size", nullable = false)
    private long fileSize;

    /** 文件 MD5（校验下载完整性） */
    @Column(length = 32)
    private String md5;

    /** 更新说明 */
    @Column(columnDefinition = "TEXT")
    private String notes;

    /** 强制更新：为 true 时客户端必须升级 */
    @Column(name = "force_update", nullable = false)
    private boolean forceUpdate;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt = LocalDateTime.now();

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getVersion() { return version; }
    public void setVersion(String version) { this.version = version; }
    public String getFileName() { return fileName; }
    public void setFileName(String fileName) { this.fileName = fileName; }
    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }
    public long getFileSize() { return fileSize; }
    public void setFileSize(long fileSize) { this.fileSize = fileSize; }
    public String getMd5() { return md5; }
    public void setMd5(String md5) { this.md5 = md5; }
    public String getNotes() { return notes; }
    public void setNotes(String notes) { this.notes = notes; }
    public boolean isForceUpdate() { return forceUpdate; }
    public void setForceUpdate(boolean forceUpdate) { this.forceUpdate = forceUpdate; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
