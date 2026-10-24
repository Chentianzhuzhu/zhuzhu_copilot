package com.zhuzhu.market.repo;

import com.zhuzhu.market.model.Plugin;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PluginRepo extends JpaRepository<Plugin, Long> {
    Page<Plugin> findBySlugContainingOrNameContainingOrDescriptionContaining(
            String slug, String name, String desc, Pageable p);

    Page<Plugin> findByUploader(String uploader, Pageable p);

    boolean existsBySlugAndVersion(String slug, String version);
}
