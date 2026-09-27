package com.zhuzhu.update.web;

import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.entity.DownloadLog;
import com.zhuzhu.update.repo.AppVersionRepo;
import com.zhuzhu.update.repo.DownloadLogRepo;
import com.zhuzhu.update.service.StatsService;
import com.zhuzhu.update.service.StorageService;
import com.zhuzhu.update.service.UpdateService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.view.RedirectView;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** 客户端更新检查与安装包下载 */
@RestController
@RequestMapping("/api/update")
public class UpdateController {

    private final UpdateService updateService;
    private final AppVersionRepo versionRepo;
    private final DownloadLogRepo logRepo;
    private final StatsService statsService;
    private final StorageService storage;

    public UpdateController(UpdateService updateService, AppVersionRepo versionRepo,
                            DownloadLogRepo logRepo, StatsService statsService,
                            StorageService storage) {
        this.updateService = updateService;
        this.versionRepo = versionRepo;
        this.logRepo = logRepo;
        this.statsService = statsService;
        this.storage = storage;
    }

    /** 客户端每 30s 轮询：version=客户端当前版本号，platform=windows/macos 等 */
    @GetMapping("/check")
    public Map<String, Object> check(@RequestParam String version,
                                     @RequestParam(defaultValue = "windows") String platform) {
        AppVersion latest = updateService.latest();
        Map<String, Object> out = new LinkedHashMap<>();
        if (latest == null) {
            out.put("hasUpdate", false);
            return out;
        }
        boolean hasUpdate = UpdateService.compare(latest.getVersion(), version) > 0;
        out.put("hasUpdate", hasUpdate);
        out.put("latest", latest.getVersion());
        out.put("notes", latest.getNotes());
        out.put("size", latest.getFileSize());
        out.put("md5", latest.getMd5());
        out.put("force", latest.isForceUpdate());
        out.put("url", "/api/update/download/" + latest.getId());
        return out;
    }

    /** 安装包下载：记录日志后 302 重定向到 nginx 静态文件路径，由 nginx 直接 serve 文件。
     *  nginx 原生支持 HTTP Range，完美支持断点续传与多线程下载。 */
    @GetMapping("/download/{id}")
    public RedirectView download(@PathVariable Long id,
                                  HttpServletRequest request) {
        AppVersion v = versionRepo.findById(id).orElse(null);
        if (v == null) {
            return null;
        }
        Path file = storage.resolve(v.getFilePath());
        if (!Files.isRegularFile(file)) {
            return null;
        }
        recordDownload(v, request);

        String filename = file.getFileName().toString();
        String scheme = request.getHeader("X-Forwarded-Proto");
        String host = request.getHeader("Host");
        String downloadUrl = (scheme != null ? scheme : "http") + "://"
                + (host != null ? host : request.getServerName())
                + "/downloads/" + filename;
        RedirectView rv = new RedirectView(downloadUrl);
        rv.setStatusCode(HttpStatus.FOUND);
        rv.setExposeModelAttributes(false);
        return rv;
    }

    private void recordDownload(AppVersion v, HttpServletRequest request) {
        try {
            DownloadLog log = new DownloadLog();
            log.setVersionId(v.getId());
            log.setIp(clientIp(request));
            String ua = request.getHeader("User-Agent");
            log.setUserAgent(ua != null && ua.length() > 255 ? ua.substring(0, 255) : ua);
            logRepo.save(log);
            statsService.invalidateTotal();
        } catch (Exception ignored) {
        }
    }

    private String clientIp(HttpServletRequest request) {
        String real = request.getHeader("X-Real-IP");
        return real != null && !real.isBlank() ? real : request.getRemoteAddr();
    }
}
