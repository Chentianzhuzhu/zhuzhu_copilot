package com.zhuzhu.market.web;

import com.zhuzhu.market.model.Plugin;
import com.zhuzhu.market.model.User;
import com.zhuzhu.market.repo.PluginRepo;
import com.zhuzhu.market.repo.UserRepo;
import com.zhuzhu.market.util.Guard;
import org.springframework.data.domain.PageRequest;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.data.domain.Sort;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpHeaders;
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
    /** 版本号：只允许安全字符，顺带堵住 storageDir/../ 路径穿越。 */
    private static final Pattern VERSION = Pattern.compile("[0-9A-Za-z._-]{1,32}");
    /** 单条目送审上限（.py/.md）。 */
    private static final int MAX_TEXT_ENTRY = 512_000;
    /** 送审文本总预算，防 zip bomb 把堆打爆。 */
    private static final long MAX_TEXT_TOTAL = 32L * 1024 * 1024;
    /** 单次下载文件上限（上传上限 20MB，留余量防库表被污染后读超大文件）。 */
    private static final long MAX_DOWNLOAD_BYTES = 64L * 1024 * 1024;

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
        // 原实现扫「最新 50 行」再内存 filter：老插件（>50 条更新之后）会误报 404，且随行数线性变慢。
        return plugins.findFirstBySlugOrderByCreatedAtDesc(slug)
                .<ResponseEntity<?>>map(p -> ResponseEntity.ok(publicView(p)))
                .orElseGet(() -> ResponseEntity.notFound().build());
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
        if (!VERSION.matcher(version).matches()) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "version 只允许字母/数字/点/下划线/连字符（1-32 位）"));
        }
        if (name.length() > 128 || (description != null && description.length() > 1024)
                || (developer != null && developer.length() > 128)) {
            return ResponseEntity.badRequest().body(Map.of("error", "name/description/developer 超长"));
        }
        // 同一 slug+version 并发上传串行化（existsBy 检查与 save 之间存在竞态窗口）
        String lockKey = "pm:upllock:" + slug + ":" + version;
        if (!Boolean.TRUE.equals(redis.opsForValue()
                .setIfAbsent(lockKey, u.username, Duration.ofSeconds(60)))) {
            return ResponseEntity.status(429).body(Map.of("error", "该版本正在上传中，请稍后重试"));
        }
        try {
            return doUpload(file, data, slug, name, description, version, developer,
                    guardVerdict, guardDetail, u);
        } finally {
            redis.delete(lockKey);
        }
    }

    private ResponseEntity<?> doUpload(MultipartFile file, byte[] data, String slug, String name,
                                       String description, String version, String developer,
                                       String guardVerdict, String guardDetail, User u) throws Exception {
        // zip 结构检查：必须含 plugin.json；条目路径安全；提取 .py/.md 文本送服务器侧复核
        Map<String, String> texts = new LinkedHashMap<>();
        boolean hasMeta = false;
        long[] budget = { MAX_TEXT_TOTAL };
        try (ZipInputStream z = new ZipInputStream(new java.io.ByteArrayInputStream(data), StandardCharsets.UTF_8)) {
            ZipEntry e;
            int count = 0;
            while ((e = z.getNextEntry()) != null) {
                if (++count > 500) {
                    return ResponseEntity.badRequest().body(Map.of("error", "zip 条目超过 500 上限"));
                }
                if (e.isDirectory()) continue;
                String en = e.getName().replace("\\", "/");
                if (en.startsWith("/") || en.contains("../") || en.contains("..\\")) {
                    return ResponseEntity.badRequest().body(Map.of("error", "zip 内含越界路径：" + en));
                }
                if (en.endsWith("plugin.json") || en.equals("plugin.json")) hasMeta = true;
                if (en.endsWith(".py") || en.endsWith(".md")) {
                    // 注意：e.getSize() 对压缩流常为 -1（未知），原来的 size<512_000 判断形同虚设，
                    // readAllBytes 可被 zip bomb 撑爆堆。改为按实际字节有界读取。
                    byte[] text = readBounded(z, MAX_TEXT_ENTRY, budget);
                    if (text == null) {
                        return ResponseEntity.badRequest().body(Map.of(
                                "error", "送审文件过大（单文件 >500KB 或总量 >32MB）：" + en));
                    }
                    texts.put(en, new String(text, StandardCharsets.UTF_8));
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
        pl.guardVerdict = trunc(guardVerdict, 64);
        pl.guardDetail = trunc((guardDetail == null || guardDetail.isBlank())
                ? "本地深度校验通过" : guardDetail, 512);
        pl.serverScan = serverScan;
        pl.filePath = zipPath.toString();
        plugins.save(pl);
        return ResponseEntity.ok(Map.of("ok", true,
                "serverScan", serverScan, "slug", slug, "version", version,
                "warnings", List.of()));
    }

    /** 下载（计数）：流式返回，不把 zip 全量读进堆 */
    @GetMapping("/{slug}/download")
    public ResponseEntity<Resource> download(@PathVariable String slug,
                                             @RequestParam(defaultValue = "") String version) throws Exception {
        if (!version.isBlank() && !VERSION.matcher(version).matches()) {
            return ResponseEntity.badRequest().build();
        }
        // 原实现：无版本时扫最新 200 行、指定版本时 findAll() 全表 stream，数据量一大就是 O(N) 拖库。
        Optional<Plugin> pl = version.isBlank()
                ? plugins.findFirstBySlugOrderByCreatedAtDesc(slug)
                : plugins.findFirstBySlugAndVersionOrderByCreatedAtDesc(slug, version);
        if (pl.isEmpty()) return ResponseEntity.notFound().build();
        Plugin p = pl.get();
        // filePath 来自库表，仍按 storageDir 做归一化包含校验（防库/写路径被污染后越界取文件）
        Path root = Path.of(storageDir).toAbsolutePath().normalize();
        Path file = Path.of(p.filePath).toAbsolutePath().normalize();
        if (!file.startsWith(root)) return ResponseEntity.status(403).build();
        if (!Files.isReadable(file) || Files.isDirectory(file)) return ResponseEntity.notFound().build();
        long size = Files.size(file);
        if (size > MAX_DOWNLOAD_BYTES) return ResponseEntity.status(413).build();
        // 单条 UPDATE 自增，避免并发下载互相覆盖计数
        plugins.bumpDownloads(p.id);
        // filename 由 slug/version 拼接，二者均已被字符白名单校验，无 CRLF 注入面
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_TYPE, "application/zip")
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        "attachment; filename=\"" + p.slug + "-" + p.version + ".zip\"")
                .header(HttpHeaders.CACHE_CONTROL, "private, max-age=60")
                .contentLength(size)
                .body(new FileSystemResource(file));
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

    /** 超列宽即截断：数据库字段有长度上限，直接落库会抛 500（name/guardDetail 均由客户端可控）。 */
    private static String trunc(String s, int max) {
        if (s == null) return null;
        return s.length() <= max ? s : s.substring(0, max);
    }

    /**
     * 有界读取一个 zip 条目。
     * 超过单条上限或总预算时返回 null（调用方拒收），绝不无限读——防 zip bomb / OOM。
     */
    private static byte[] readBounded(ZipInputStream z, int perEntry, long[] budget)
            throws java.io.IOException {
        long cap = Math.min(perEntry, budget[0]);
        if (cap <= 0) return null;
        var out = new java.io.ByteArrayOutputStream((int) Math.min(cap, 16 * 1024));
        byte[] buf = new byte[8192];
        long total = 0;
        int n;
        while ((n = z.read(buf, 0, buf.length)) > 0) {
            total += n;
            if (total > cap) return null;
            out.write(buf, 0, n);
        }
        budget[0] -= total;
        return out.toByteArray();
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
