package com.zhuzhu.update.service;

import com.zhuzhu.update.repo.DownloadLogRepo;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 下载统计：总量走 Redis 缓存（60s 失效防抖），趋势按日期从 DB 聚合 */
@Service
public class StatsService {

    private static final String KEY_TOTAL = "stats:download_total";
    private static final Duration CACHE_TTL = Duration.ofSeconds(60);

    private final DownloadLogRepo logRepo;
    private final StringRedisTemplate redis;

    public StatsService(DownloadLogRepo logRepo, StringRedisTemplate redis) {
        this.logRepo = logRepo;
        this.redis = redis;
    }

    /** 总下载量（优先 Redis 缓存，miss 时查 DB 并回填） */
    public long totalDownloads() {
        String cached = redis.opsForValue().get(KEY_TOTAL);
        if (cached != null) {
            try {
                return Long.parseLong(cached);
            } catch (NumberFormatException ignored) {
                // 缓存损坏则回源
            }
        }
        long total = logRepo.count();
        try {
            redis.opsForValue().set(KEY_TOTAL, String.valueOf(total), CACHE_TTL);
        } catch (Exception ignored) {
            // Redis 不可用时降级为直查 DB
        }
        return total;
    }

    /** 最近 days 天按日期的下载趋势（无记录的日期补 0，升序） */
    public List<Map<String, Object>> trend(int days) {
        LocalDate from = LocalDate.now().minusDays(days - 1L);
        List<Object[]> rows = logRepo.countByDateFrom(from);
        Map<LocalDate, Long> map = new LinkedHashMap<>();
        for (Object[] r : rows) {
            map.put((LocalDate) r[0], (Long) r[1]);
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < days; i++) {
            LocalDate d = from.plusDays(i);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("date", d.toString());
            m.put("downloads", map.getOrDefault(d, 0L));
            out.add(m);
        }
        return out;
    }

    /** 每次下载后使总量缓存失效（下次查询回源刷新） */
    public void invalidateTotal() {
        try {
            redis.delete(KEY_TOTAL);
        } catch (Exception ignored) {
        }
    }
}
