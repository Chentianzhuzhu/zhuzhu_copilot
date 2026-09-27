package com.zhuzhu.update.repo;

import com.zhuzhu.update.entity.SiteContent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface SiteContentRepo extends JpaRepository<SiteContent, Long> {

    Optional<SiteContent> findByCkey(String ckey);
}
