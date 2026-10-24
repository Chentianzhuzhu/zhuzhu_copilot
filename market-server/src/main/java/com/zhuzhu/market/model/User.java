package com.zhuzhu.market.model;

import jakarta.persistence.*;
import java.time.Instant;

@Entity
@Table(name = "users")
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    public Long id;

    @Column(unique = true, nullable = false, length = 64)
    public String username;

    @Column(nullable = false, length = 128)
    public String passwordHash;

    /** 开发者/公司名称：注册时默认取用户名，可随时改 */
    @Column(length = 128)
    public String developer;

    public Instant createdAt = Instant.now();
}
