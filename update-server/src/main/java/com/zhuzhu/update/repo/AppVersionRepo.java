package com.zhuzhu.update.repo;

import com.zhuzhu.update.entity.AppVersion;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface AppVersionRepo extends JpaRepository<AppVersion, Long> {

    Optional<AppVersion> findFirstByOrderByCreatedAtDesc();

    boolean existsByVersion(String version);
}
