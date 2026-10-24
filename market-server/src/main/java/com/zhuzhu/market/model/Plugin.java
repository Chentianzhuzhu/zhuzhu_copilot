package com.zhuzhu.market.model;

import jakarta.persistence.*;
import java.time.Instant;

@Entity
@Table(name = "plugins")
public class Plugin {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;

    /** 插件唯一标识（plugin.json 的 name） */
    @Column(nullable = false, length = 64)
    public String slug;

    @Column(nullable = false, length = 128)
    public String name;

    @Column(length = 1024)
    public String description;

    /** 开发者/公司（上传时可指定；缺省 = 上传者用户名） */
    @Column(length = 128)
    public String developer;

    /** 上传者账号 */
    @Column(nullable = false, length = 64)
    public String uploader;

    @Column(nullable = false, length = 32)
    public String version;

    public long sizeBytes;

    public String sha256;

    /** 客户端本地 LLM 深度校验的结论（safe / dangerous + 理由），服务器留存 */
    @Column(length = 64)
    public String guardVerdict;

    @Column(length = 512)
    public String guardDetail;

    /** 服务器侧静态扫描结果（pass / blocked:<rule>） */
    @Column(length = 64)
    public String serverScan;

    public long downloads;

    public Instant createdAt = Instant.now();

    @Column(nullable = false)
    public String filePath;
}
