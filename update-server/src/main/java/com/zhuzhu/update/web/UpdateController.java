package com.zhuzhu.update.web;

import com.zhuzhu.update.entity.AppVersion;
import com.zhuzhu.update.entity.DownloadLog;
import com.zhuzhu.update.repo.AppVersionRepo;
import com.zhuzhu.update.repo.DownloadLogRepo;
import com.zhuzhu.update.service.DownloadTokenService;
import com.zhuzhu.update.service.StatsService;
import com.zhuzhu.update.service.StorageService;
import com.zhuzhu.update.service.UpdateService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.view.RedirectView;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** 客户端更新检查与安装包下载 */
@RestController
public class UpdateController {

    private static final Logger log = LoggerFactory.getLogger(UpdateController.class);

    private final UpdateService updateService;
    private final AppVersionRepo versionRepo;
    private final DownloadLogRepo logRepo;
    private final StatsService statsService;
    private final StorageService storage;
    private final DownloadTokenService downloadTokens;

    public UpdateController(UpdateService updateService, AppVersionRepo versionRepo,
                            DownloadLogRepo logRepo, StatsService statsService,
                            StorageService storage, DownloadTokenService downloadTokens) {
        this.updateService = updateService;
        this.versionRepo = versionRepo;
        this.logRepo = logRepo;
        this.statsService = statsService;
        this.storage = storage;
        this.downloadTokens = downloadTokens;
    }

    /** 客户端每 30s 轮询：version=客户端当前版本号，platform=windows/macos 等 */
    @GetMapping("/api/update/check")
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
        // 下载地址用 1 小时有效的签名令牌，不暴露明文版本 id
        out.put("url", "/api/download/" + downloadTokens.generateToken(latest.getId()));
        return out;
    }

    /** 旧版明文下载接口：兼容历史客户端，被使用时打警告日志（统计遍历风险） */
    @GetMapping("/api/update/download/{id}")
    public RedirectView download(@PathVariable Long id,
                                  HttpServletRequest request) {
        log.warn("旧版明文下载URL被使用: id={}, ip={}", id, clientIp(request));
        return doDownload(id, request);
    }

    /**
     * 新版签名下载接口：token 无效/过期一律 404。
     * 校验通过后的逻辑与旧版 download/{id} 完全一致。
     */
    @GetMapping("/api/download/{token}")
    public RedirectView tokenDownload(@PathVariable String token,
                                     HttpServletRequest request,
                                     HttpServletResponse response) {
        Long id = downloadTokens.validateToken(token);
        if (id == null) {
            response.setStatus(HttpServletResponse.SC_NOT_FOUND);
            return null;
        }
        RedirectView rv = doDownload(id, request);
        if (rv == null) {
            response.setStatus(HttpServletResponse.SC_NOT_FOUND);
            return null;
        }
        return rv;
    }

    /** 安装包下载：记录日志后 302 重定向到 nginx 静态文件路径，由 nginx 直接 serve 文件。
     *  nginx 原生支持 HTTP Range，完美支持断点续传与多线程下载。 */
    private RedirectView doDownload(Long id, HttpServletRequest request) {
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
