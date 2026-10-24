package com.zhuzhu.market.web;

import com.zhuzhu.market.model.Plugin;
import com.zhuzhu.market.model.User;
import com.zhuzhu.market.repo.PluginRepo;
import com.zhuzhu.market.repo.UserRepo;
import com.zhuzhu.market.util.Guard;
import org.springframework.data.domain.PageRequest;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.*;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

@RestController
@RequestMapping("/api/plugins")
public class PluginController {
    private final PluginRepo plugins;
    private final UserRepo users;
    private final StringRedisTemplate redis;
    private final String storageDir;

    public PluginController(PluginRepo plugins, UserRepo users,
                            StringRedisTemplate redis,
                            org.springframework.core.env.Environment env) {
        this.plugins = plugins;
        this.users = users;
        this.redis = redis;
        this.storageDir = env.getProperty("market.storage-dir", "/var/lib/plugin-market/files");
    }

    /** 列表 / 搜索：?q=&page=0&size=10 */
    @GetMapping
    public Map<String, Object> list(@RequestParam(defaultValue = "") String q,
                                    @RequestParam(defaultValue = "0") int page,
                                    @RequestParam(defaultValue = "10") int size) {
        size = Math.min(Math.max(size, 1), 50);
        var p = PageRequest.of(Math.max(page, 0), size,
                Sort.by(Sort.Direction.DESC, "createdAt"));
        var r = (q == null || q.isBlank())
                ? plugins.findAll(p)
                : plugins.findBySlugContainingOrNameContainingOrDescriptionContaining(
                        q.trim(), q.trim(), q.trim(), p);
        List<Map<String, Object>> items = new ArrayList<>();
        for (var pl : r.getContent()) items.add(publicView(pl));
        return Map.of("total", r.getTotalElements(), "page", r.getNumber(),
                "size", r.getSize(), "items", items);
    }

    @GetMapping("/{slug}")
    public ResponseEntity<?> detail(@PathVariable String slug) {
        var list = plugins.findAll(PageRequest.of(0, 50,
                        Sort.by(Sort.Direction.DESC, "createdAt"))).getContent().stream()
                .filter(x -> x.slug.equals(slug)).toList();
        if (list.isEmpty()) return ResponseEntity.notFound().build();
        return ResponseEntity.ok(publicView(list.get(0)));
    }

    /** 上传：需登录。file=zip，name/description/developer 可指定。 */
    @PostMapping
    public ResponseEntity<?> upload(@RequestParam("file") MultipartFile file,
                                    @RequestParam("name") String name,
                                    @RequestParam(value = "description", required = false) String description,
                                    @RequestParam(value = "version", defaultValue = "1.0.0") String version,
                                    @RequestParam(value = "developer", required = false) String developer,
                                    @RequestParam(value = "guardVerdict", defaultValue = "safe") String guardVerdict,
                                    @RequestParam(value = "guardDetail", required = false) String guardDetail,
                                    HttpServletRequest req) throws Exception {
        User u = Sessions.require(redis, users, req);
        if ("dangerous".equalsIgnoreCase(guardVerdict)) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "本地深度安全校验判定该插件危险，市场拒绝接收"));
        }
        if (file.isEmpty()) return ResponseEntity.badRequest().body(Map.of("error", "缺少 zip 文件"));
        byte[] data = file.getBytes();
        if (data.length > 20 * 1024 * 1024) {
            return ResponseEntity.badRequest().body(Map.of("error", "zip 超过 20MB 上限"));
        }
        // 每用户每日上传限额
        Long n = redis.opsForValue().increment("pm:upl:" + u.username);
        if (n != null && n == 1) redis.expire("pm:upl:" + u.username, Duration.ofHours(24));
        if (n != null && n > 30) return ResponseEntity.status(429).body(Map.of("error", "今日上传次数已达上限"));

        String slug = slugify(name);
        if (!slug.matches("[a-z0-9_-]{2,64}")) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "名称只允许小写字母/数字/下划线/连字符（2-64 位）"));
        }
        // zip 结构检查：必须含 plugin.json；条目路径安全；提取 .py/.md 文本送服务器侧复核
        Map<String, String> texts = new LinkedHashMap<>();
        boolean hasMeta = false;
        try (ZipInputStream z = new ZipInputStream(new java.io.ByteArrayInputStream(data), StandardCharsets.UTF_8)) {
            ZipEntry e;
            int count = 0;
            while ((e = z.getNextEntry()) != null && count++ < 500) {
                if (e.isDirectory()) continue;
                String en = e.getName().replace("\\", "/");
                if (en.startsWith("/") || en.contains("../") || en.contains("..\\")) {
                    return ResponseEntity.badRequest().body(Map.of("error", "zip 内含越界路径：" + en));
                }
                if (en.endsWith("plugin.json") || en.equals("plugin.json")) hasMeta = true;
                if ((en.endsWith(".py") || en.endsWith(".md")) && e.getSize() < 512_000) {
                    texts.put(en, new String(z.readAllBytes(), StandardCharsets.UTF_8));
                }
            }
        }
        if (!hasMeta) return ResponseEntity.badRequest().body(Map.of("error", "zip 缺少 plugin.json"));
        if (plugins.existsBySlugAndVersion(slug, version)) {
            return ResponseEntity.badRequest().body(Map.of("error", "该版本已存在（换一个 version 再传）"));
        }
        // 服务器侧静态复核（客户端上传前已跑过完整校验系统 + 本地 LLM 深度评审）
        var blocked = Guard.scan(texts);
        if (!blocked.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "服务器静态复核未通过，已拒收（未保存）",
                    "warnings", blocked));
        }
        String serverScan = "pass";

        // 落盘
        String sha = Sessions.sha256(data);
        Path dir = Path.of(storageDir, slug, version);
        Files.createDirectories(dir);
        Path zipPath = dir.resolve(slug + ".zip");
        Files.write(zipPath, data);

        Plugin pl = new Plugin();
        pl.slug = slug;
        pl.name = name;
        pl.description = description == null ? "" : description;
        pl.version = version;
        pl.developer = (developer == null || developer.isBlank()) ? u.developer : developer;
        pl.uploader = u.username;
        pl.sizeBytes = data.length;
        pl.sha256 = sha;
        pl.guardVerdict = guardVerdict;
        pl.guardDetail = (guardDetail == null || guardDetail.isBlank())
                ? "本地深度校验通过" : guardDetail;
        pl.serverScan = serverScan;
        pl.filePath = zipPath.toString();
        plugins.save(pl);
        return ResponseEntity.ok(Map.of("ok", true,
                "serverScan", serverScan, "slug", slug, "version", version,
                "warnings", List.of()));
    }

    /** 下载（计数） */
    @GetMapping("/{slug}/download")
    public ResponseEntity<byte[]> download(@PathVariable String slug,
                                           @RequestParam(defaultValue = "") String version) throws Exception {
        Optional<Plugin> pl;
        if (version.isBlank()) {
            pl = plugins.findAll(PageRequest.of(0, 200,
                            Sort.by(Sort.Direction.DESC, "createdAt"))).getContent().stream()
                    .filter(x -> x.slug.equals(slug)).findFirst();
        } else {
            pl = plugins.findAll().stream()
                    .filter(x -> x.slug.equals(slug) && x.version.equals(version)).findFirst();
        }
        if (pl.isEmpty()) return ResponseEntity.notFound().build();
        Plugin p = pl.get();
        byte[] data = Files.readAllBytes(Path.of(p.filePath));
        p.downloads++;
        plugins.save(p);
        return ResponseEntity.ok()
                .header("Content-Disposition",
                        "attachment; filename=\"" + p.slug + "-" + p.version + ".zip\"")
                .body(data);
    }

    /** 我上传的插件（登录态） */
    @GetMapping("/mine")
    public Map<String, Object> mine(HttpServletRequest req) throws Sessions.AuthException {
        User u = Sessions.require(redis, users, req);
        var list = plugins.findByUploader(u.username,
                        PageRequest.of(0, 100, Sort.by(Sort.Direction.DESC, "createdAt")))
                .getContent().stream().map(this::publicView).toList();
        return Map.of("items", list);
    }

    private Map<String, Object> publicView(Plugin p) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("slug", p.slug);
        m.put("name", p.name);
        m.put("description", p.description);
        m.put("developer", p.developer);
        m.put("version", p.version);
        m.put("size", p.sizeBytes);
        m.put("downloads", p.downloads);
        m.put("createdAt", p.createdAt.toString());
        m.put("guardVerdict", p.guardVerdict);
        m.put("serverScan", p.serverScan);
        m.put("downloadUrl", "/api/plugins/" + p.slug + "/download?version=" + p.version);
        return m;
    }

    private static String slugify(String s) {
        return Pattern.compile("[^a-z0-9_-]+")
                .matcher((s == null ? "" : s).toLowerCase().replace(' ', '-'))
                .replaceAll("").replaceAll("^-+|-+$", "");
    }
}
