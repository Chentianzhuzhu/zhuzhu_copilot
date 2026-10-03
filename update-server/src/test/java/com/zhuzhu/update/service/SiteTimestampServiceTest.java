package com.zhuzhu.update.service;

import com.zhuzhu.update.entity.AppVersion;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 站点公开时间戳：三个口径的取值与「缺失即 null」的边界。
 *
 * <p>这些用例钉住的是对外契约 —— 页脚与 /api/site 都读同一份数据，
 * 一旦某个来源缺失就应保持 null（而不是编造一个看起来合理的默认时间）。
 */
class SiteTimestampServiceTest {

    private static AppVersion version(String tag, LocalDateTime createdAt) {
        AppVersion v = new AppVersion();
        v.setVersion(tag);
        v.setCreatedAt(createdAt);
        return v;
    }

    @Test
    @DisplayName("三个来源齐备时各字段正确落入快照")
    void ofCollectsAllSources() {
        LocalDateTime contentAt = LocalDateTime.of(2026, 10, 3, 12, 30);
        AppVersion latest = version("5.1.7", LocalDateTime.of(2026, 10, 2, 9, 0));
        long mediaMillis = LocalDateTime.of(2026, 10, 1, 18, 5)
                .atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli();

        SiteTimestampService.Timestamps ts = SiteTimestampService.of(contentAt, latest, mediaMillis);

        assertEquals(contentAt, ts.siteContentUpdatedAt());
        assertEquals(LocalDate.of(2026, 10, 2), ts.latestReleaseDate(),
                "版本发布日期只保留日期，用于对外展示");
        assertEquals(LocalDateTime.of(2026, 10, 1, 18, 5), ts.mediaLibraryUpdatedAt());
    }

    @Test
    @DisplayName("时间统一截断到秒，不对外暴露微秒噪声")
    void ofTruncatesToSeconds() {
        LocalDateTime contentAt = LocalDateTime.of(2026, 10, 2, 13, 31, 35, 183_211_000);
        long mediaMillis = LocalDateTime.of(2026, 10, 2, 13, 30, 42, 749_000_000)
                .atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli();

        SiteTimestampService.Timestamps ts = SiteTimestampService.of(contentAt, null, mediaMillis);

        assertEquals("2026-10-02T13:31:35", ts.contentUpdatedAtIso(), "内容更新时间应截断到秒");
        assertEquals("2026-10-02T13:30:42", ts.mediaUpdatedAtIso(), "媒体库更新时间应截断到秒");
    }

    @Test
    @DisplayName("来源缺失时保持 null，不编造默认值")
    void ofKeepsNullsWhenMissing() {
        SiteTimestampService.Timestamps ts = SiteTimestampService.of(null, null, 0L);
        assertNull(ts.siteContentUpdatedAt());
        assertNull(ts.latestReleaseDate());
        assertNull(ts.mediaLibraryUpdatedAt());
        assertNull(ts.contentUpdatedAtIso());
        assertNull(ts.latestReleaseDateIso());
        assertNull(ts.mediaUpdatedAtIso());
    }

    @Test
    @DisplayName("媒体库为空（时间戳 <= 0）视为没有媒体")
    void ofTreatsNonPositiveMediaAsMissing() {
        assertNull(SiteTimestampService.of(null, null, -1L).mediaLibraryUpdatedAt());
        assertNull(SiteTimestampService.of(null, null, 0L).mediaLibraryUpdatedAt());
        assertNotNull(SiteTimestampService.of(null, null, 1L).mediaLibraryUpdatedAt());
    }

    @Test
    @DisplayName("版本记录存在但没有创建时间时同样视为缺失")
    void ofHandlesVersionWithoutCreatedAt() {
        assertNull(SiteTimestampService.of(null, version("5.1.7", null), 0L).latestReleaseDate());
    }

    @Test
    @DisplayName("toMap 输出 ISO 字符串，缺失为 null")
    void toMapEmitsIsoAndNulls() {
        SiteTimestampService.Timestamps ts = SiteTimestampService.of(
                LocalDateTime.of(2026, 10, 3, 8, 15),
                version("5.1.7", LocalDateTime.of(2026, 10, 2, 9, 0)),
                0L);

        Map<String, Object> m = ts.toMap();

        assertEquals("2026-10-03T08:15", m.get("siteContentUpdatedAt"));
        assertEquals("2026-10-02", m.get("latestReleaseDate"));
        assertNull(m.get("mediaLibraryUpdatedAt"), "媒体库为空时应为 null 而非空串");
        assertEquals(3, m.size(), "对外字段数必须与文档一致");
    }

    @Test
    @DisplayName("dateText 统一为 yyyy-MM-dd，空值返回空串")
    void dateTextFormats() {
        assertEquals("2026-10-03", SiteTimestampService.dateText(LocalDate.of(2026, 10, 3)));
        assertEquals("2026-10-03", SiteTimestampService.dateText(LocalDateTime.of(2026, 10, 3, 23, 59)));
        assertEquals("", SiteTimestampService.dateText((LocalDate) null));
        assertEquals("", SiteTimestampService.dateText((LocalDateTime) null));
    }

    @Test
    @DisplayName("current 从三个服务各自取一次值，不重复调用")
    void currentReadsEachSourceOnce() {
        ContentService content = mock(ContentService.class);
        UpdateService update = mock(UpdateService.class);
        StorageService storage = mock(StorageService.class);
        LocalDateTime contentAt = LocalDateTime.of(2026, 10, 3, 12, 30);
        when(content.updatedAt()).thenReturn(contentAt);
        when(update.latest()).thenReturn(version("5.1.7", LocalDateTime.of(2026, 10, 2, 9, 0)));
        when(storage.latestMediaUpdate()).thenReturn(0L);

        SiteTimestampService.Timestamps ts =
                new SiteTimestampService(content, update, storage).current();

        assertEquals(contentAt, ts.siteContentUpdatedAt());
        assertEquals(LocalDate.of(2026, 10, 2), ts.latestReleaseDate());
        assertNull(ts.mediaLibraryUpdatedAt());
    }
}
