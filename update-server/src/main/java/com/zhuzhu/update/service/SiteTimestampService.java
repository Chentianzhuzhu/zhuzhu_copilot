package com.zhuzhu.update.service;

import com.zhuzhu.update.entity.AppVersion;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 站点公开时间戳：把官网上「内容 / 版本 / 媒体库」三处的时间收敛成一份可对外暴露的数据。
 *
 * <p>三个口径分别对应：
 * <ul>
 *   <li><b>站点内容更新时间</b> —— site_content 行的 updated_at，后台每保存一次内容就刷新；</li>
 *   <li><b>最新版本发布日期</b> —— 最新 AppVersion 的创建日期，等价于「最近一次发版」；</li>
 *   <li><b>媒体库更新时间</b> —— uploads/img 与 uploads/video 中最新一个文件的修改时间。</li>
 * </ul>
 *
 * <p>收敛到一处取值的意义：页脚展示、公开 API 与健康检查若各算各的，口径早晚漂移
 * （例如一处按日期、一处按时刻，或一处漏算视频）。任一来源缺失时对应字段为 {@code null}，
 * 由调用方决定是隐藏该条还是留空 —— 绝不编造一个看起来合理的默认时间。
 */
@Service
public class SiteTimestampService {

    private final ContentService contentService;
    private final UpdateService updateService;
    private final StorageService storageService;

    public SiteTimestampService(ContentService contentService, UpdateService updateService,
                                StorageService storageService) {
        this.contentService = contentService;
        this.updateService = updateService;
        this.storageService = storageService;
    }

    /** 站点公开时间戳快照；字段名与 {@link #toMap()} 的键保持一致，避免两处各起一名 */
    public record Timestamps(LocalDateTime siteContentUpdatedAt, LocalDate latestReleaseDate,
                             LocalDateTime mediaLibraryUpdatedAt) {

        /** 站点内容更新时间（ISO-8601），无内容记录时为 null */
        public String contentUpdatedAtIso() {
            return siteContentUpdatedAt == null ? null : siteContentUpdatedAt.toString();
        }

        /** 最新版本发布日期（yyyy-MM-dd），尚无发版记录时为 null */
        public String latestReleaseDateIso() {
            return latestReleaseDate == null ? null : latestReleaseDate.toString();
        }

        /** 媒体库更新时间（ISO-8601），媒体库为空时为 null */
        public String mediaUpdatedAtIso() {
            return mediaLibraryUpdatedAt == null ? null : mediaLibraryUpdatedAt.toString();
        }

        /** 供公开 API 直接序列化；缺失项为 null 而不是空串，便于调用方区分「没有」与「空值」 */
        public Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("siteContentUpdatedAt", contentUpdatedAtIso());
            m.put("latestReleaseDate", latestReleaseDateIso());
            m.put("mediaLibraryUpdatedAt", mediaUpdatedAtIso());
            return m;
        }
    }

    /** 当前时间戳快照（各来源独立取值，互不阻断） */
    public Timestamps current() {
        AppVersion latest = updateService.latest();
        return of(contentService.updatedAt(), latest, storageService.latestMediaUpdate());
    }

    /**
     * 纯函数装配：把三处原始值归一成快照。
     *
     * <p>单独抽出便于单元测试直接构造边界情形（全部缺失 / 只有版本 / 媒体时间戳为 0）。
     *
     * <p>时间统一截断到**秒**：库里的 {@code LocalDateTime} 带微秒（如
     * {@code 2026-10-02T13:31:35.183211}），直接对外暴露既噪声大、又暗示了我们并不
     * 保证的亚秒精度；截断后对外契约稳定为一个固定形状。
     *
     * @param mediaMillis 媒体库最新修改时间，{@code <= 0} 视为「没有媒体」
     */
    public static Timestamps of(LocalDateTime contentUpdatedAt, AppVersion latest, long mediaMillis) {
        LocalDate release = (latest == null || latest.getCreatedAt() == null)
                ? null : latest.getCreatedAt().toLocalDate();
        LocalDateTime media = mediaMillis <= 0L
                ? null : LocalDateTime.ofInstant(Instant.ofEpochMilli(mediaMillis), ZoneId.systemDefault());
        return new Timestamps(truncate(contentUpdatedAt), release, truncate(media));
    }

    /** 截断到秒；null 原样返回 */
    private static LocalDateTime truncate(LocalDateTime value) {
        return value == null ? null : value.truncatedTo(ChronoUnit.SECONDS);
    }

    /** 统一的页面展示口径：一律「yyyy-MM-dd」，日期或时刻都适用；为空时返回空串 */
    public static String dateText(LocalDate date) {
        return date == null ? "" : date.toString();
    }

    public static String dateText(LocalDateTime dateTime) {
        return dateTime == null ? "" : dateTime.toLocalDate().toString();
    }
}
