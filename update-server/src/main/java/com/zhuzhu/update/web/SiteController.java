package com.zhuzhu.update.web;

import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.service.ContentService;
import com.zhuzhu.update.service.DownloadTokenService;
import com.zhuzhu.update.service.SiteTimestampService;
import com.zhuzhu.update.service.StatsService;
import com.zhuzhu.update.service.UpdateService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/** 官网公开数据接口 */
@RestController
@RequestMapping("/api")
public class SiteController {

    private final ContentService contentService;
    private final StatsService statsService;
    private final UpdateService updateService;
    private final SiteTimestampService timestamps;
    private final DownloadTokenService downloadTokens;

    public SiteController(ContentService contentService, StatsService statsService,
                          UpdateService updateService, SiteTimestampService timestamps,
                          DownloadTokenService downloadTokens) {
        this.contentService = contentService;
        this.statsService = statsService;
        this.updateService = updateService;
        this.timestamps = timestamps;
        this.downloadTokens = downloadTokens;
    }

    /** 官网首页数据：内容 + 最新版本 + 总下载量 + 站点时间戳 */
    @GetMapping("/site")
    public Map<String, Object> site() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("content", contentService.get());
        out.put("totalDownloads", statsService.totalDownloads());
        AppVersion latest = updateService.latest();
        out.put("latest", latest == null ? null : versionView(latest, downloadTokens));
        // 站点内容更新时间 / 最新版本发布日期 / 媒体库更新时间，统一由时间戳服务给出
        out.put("timestamps", timestamps.current().toMap());
        return out;
    }

    /** 最新版本信息 */
    @GetMapping("/version/latest")
    public Map<String, Object> latest() {
        AppVersion v = updateService.latest();
        return v == null ? Map.of() : versionView(v, downloadTokens);
    }

    static Map<String, Object> versionView(AppVersion v, DownloadTokenService tokens) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", v.getId());
        m.put("version", v.getVersion());
        m.put("notes", v.getNotes());
        m.put("notesLines", SitePageController.noteLines(v.getNotes()));
        m.put("size", v.getFileSize());
        m.put("sizeText", SitePageController.sizeText(v.getFileSize()));
        m.put("force", v.isForceUpdate());
        // 下载地址用 1 小时有效的签名令牌，不暴露明文版本 id
        m.put("url", "/api/download/" + tokens.generateToken(v.getId()));
        return m;
    }
}
