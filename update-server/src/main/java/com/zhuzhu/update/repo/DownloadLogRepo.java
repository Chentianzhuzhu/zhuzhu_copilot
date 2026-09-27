package com.zhuzhu.update.repo;

import com.zhuzhu.update.entity.DownloadLog;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.List;

public interface DownloadLogRepo extends JpaRepository<DownloadLog, Long> {

    long countByVersionId(Long versionId);

    /** 按日期聚合下载数（趋势图数据，升序） */
    @Query("select d.createdDate, count(d) from DownloadLog d " +
            "where d.createdDate >= :from group by d.createdDate order by d.createdDate")
    List<Object[]> countByDateFrom(@Param("from") LocalDate from);
}
