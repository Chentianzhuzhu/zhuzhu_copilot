package com.zhuzhu.market.repo;

import com.zhuzhu.market.model.Plugin;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

public interface PluginRepo extends JpaRepository<Plugin, Long> {
    Page<Plugin> findBySlugContainingOrNameContainingOrDescriptionContaining(
            String slug, String name, String desc, Pageable p);

    Page<Plugin> findByUploader(String uploader, Pageable p);

    boolean existsBySlugAndVersion(String slug, String version);

    /** 详情 / 最新版下载：单行走索引，替代原来的「全表分页 + 内存 filter」。 */
    Optional<Plugin> findFirstBySlugOrderByCreatedAtDesc(String slug);

    /** 指定版本下载。 */
    Optional<Plugin> findFirstBySlugAndVersionOrderByCreatedAtDesc(String slug, String version);

    /** 下载量自增：单条 UPDATE，避免「读-改-写」在并发下丢计数。 */
    @Modifying(flushAutomatically = true, clearAutomatically = false)
    @Transactional
    @Query("update Plugin p set p.downloads = p.downloads + 1 where p.id = :id")
    int bumpDownloads(@Param("id") Long id);
}
