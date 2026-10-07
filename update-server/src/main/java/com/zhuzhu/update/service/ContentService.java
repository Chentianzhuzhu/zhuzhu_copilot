package com.zhuzhu.update.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.zhuzhu.update.entity.SiteContent;
import com.zhuzhu.update.repo.SiteContentRepo;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 官网内容：单行 JSON 存储。
 *
 * <p>读取时把库中内容与默认结构做深合并，因此：
 * <ul>
 *   <li>后台新增字段（画廊 / 视频 / FAQ / SEO …）对历史数据自动补齐，不需要手工迁移；</li>
 *   <li>后台「清空某个列表」会被尊重，不会被默认值重新填回。</li>
 * </ul>
 */
@Service
public class ContentService {

    /** 内容键，固定为 site */
    private static final String KEY = "site";

    private final SiteContentRepo repo;
    private final ObjectMapper mapper;

    public ContentService(SiteContentRepo repo, ObjectMapper mapper) {
        this.repo = repo;
        this.mapper = mapper;
    }

    /** 官网内容（库中内容 + 默认结构深合并）；库中无记录时写入默认内容 */
    public Map<String, Object> get() {
        SiteContent row = repo.findByCkey(KEY).orElse(null);
        if (row == null) {
            Map<String, Object> initial = defaults();
            persist(initial);
            return initial;
        }
        return merge(defaults(), parse(row.getContentJson()));
    }

    /** 保存官网内容 */
    public void save(Map<String, Object> content) {
        persist(merge(defaults(), content));
    }

    /**
     * 官网内容最后更新时间。
     *
     * <p>取自单行内容的 {@code updated_at}（每次保存都会刷新），代表「站点内容多久前被改过」
     * 这一对外可见的事实；库中尚无记录时返回 {@code null}（调用方据此决定是否展示）。
     */
    public LocalDateTime updatedAt() {
        return repo.findByCkey(KEY).map(SiteContent::getUpdatedAt).orElse(null);
    }

    /** 默认内容：真实产品信息，仅作为结构骨架与首次初始化数据 */
    public static Map<String, Object> defaults() {
        Map<String, Object> c = new LinkedHashMap<>();
        c.put("title", "WinAppMigrator");
        c.put("slogan", "把电脑交给 AI，替你把活干完");
        c.put("description", "Windows 应用迁移与系统优化工具，内置 AI Copilot：观察屏幕、理解指令、自动完成电脑操作。");
        c.put("heroImage", "");

        c.put("sections", sections());
        c.put("features", features());
        c.put("gallery", new ArrayList<>());
        c.put("videos", new ArrayList<>());
        c.put("advantages", advantages());
        c.put("faq", faq());
        c.put("requirements", requirements());
        c.put("stats", stats());
        c.put("seo", seo());
        c.put("footer", footer());
        c.put("about", about());
        c.put("style", style());
        return c;
    }

    /* ---------------- 默认内容分块（保持可读性，逐块构建） ---------------- */

    private static Map<String, Object> sections() {
        Map<String, Object> s = new LinkedHashMap<>();
        s.put("features", section("// 01 — 功能特性", "为迁移与优化而生", "覆盖迁移、优化与 AI 自动化的完整能力矩阵。"));
        s.put("gallery", section("// 02 — 界面实拍", "看见真实的样子", "以下为产品实际界面截图，由后台维护。"));
        s.put("videos", section("// 03 — 视频演示", "三分钟看懂它", "演示视频支持本地上传或第三方平台嵌入。"));
        s.put("stats", section("// 04 — 数据一览", "真实数字不说空话", "下载量来自后端统计接口，随版本更新实时变化。"));
        s.put("advantages", section("// 05 — 产品优势", "为什么选择它", "深度系统能力与严谨的安全设计兼顾效率与可靠。"));
        s.put("faq", section("// 06 — 常见问题", "你可能想问", "关于安装、迁移与安全性的常见疑问。"));
        s.put("changelog", section("// 07 — 更新日志", "最新版本", "每次迭代的说明均来自版本服务接口。"));
        s.put("about", section("// 08 — 关于我们", "联系我们", "团队介绍与联系方式。"));
        return s;
    }

    private static Map<String, Object> section(String tag, String title, String sub) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("tag", tag);
        m.put("title", title);
        m.put("sub", sub);
        return m;
    }

    private static List<Object> features() {
        List<Object> list = new ArrayList<>();
        list.add(feature("migrate", "应用迁移", "一键迁移应用安装目录，保留设置与数据，杜绝 C 盘膨胀。"));
        list.add(feature("ai", "AI 助手", "直接输入任务，AI 自动截屏分析、点击输入，像真人一样操作电脑。"));
        list.add(feature("optimize", "系统优化", "内存优化、启动项治理、安全防护，让老电脑重新流畅。"));
        return list;
    }

    public static Map<String, Object> feature(String icon, String title, String desc) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("icon", icon);
        m.put("title", title);
        m.put("desc", desc);
        return m;
    }

    private static List<Object> advantages() {
        List<Object> list = new ArrayList<>();
        list.add(advantage("系统级深度操作", "直连 Windows 系统接口完成注册表读写、进程管理与快捷方式更新，迁移结果与手工操作一致。"));
        list.add(advantage("严谨的安全设计", "命令沙盒与路径白名单双重防护，禁止修改系统关键目录，迁移与卸载均需明确确认。"));
        list.add(advantage("AI 智能自动化", "截屏分析 → 调用工具 → 截图验证的闭环，支持多会话、上下文持久化与自动压缩。"));
        list.add(advantage("轻量开箱即用", "PyQt6 现代化界面，安装包一键安装，无需额外配置即可开始迁移。"));
        return list;
    }

    private static Map<String, Object> advantage(String title, String desc) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("title", title);
        m.put("desc", desc);
        return m;
    }

    private static List<Object> faq() {
        List<Object> list = new ArrayList<>();
        list.add(qa("迁移应用会丢失我的设置和数据吗？", "迁移会同步更新注册表、快捷方式与 AppData 数据目录，应用设置与数据保持不变；冲突目录会在迁移前提示并智能处理。"));
        list.add(qa("AI 助手需要联网吗？", "需要。AI 能力通过大模型服务完成推理，其余迁移、优化、清理等操作全部在本地完成，不上传任何本机文件。"));
        list.add(qa("会不会误删系统文件？", "不会。命令沙盒与路径白名单禁止触碰系统关键目录，所有破坏性操作都需二次确认，并提供操作记录。"));
        list.add(qa("支持哪些 Windows 版本？", "支持 Windows 10 与 Windows 11 的 64 位版本，需要管理员权限以完成系统级操作。"));
        return list;
    }

    private static Map<String, Object> qa(String q, String a) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("q", q);
        m.put("a", a);
        return m;
    }

    /** 下载页「系统要求」：同样是后台可编辑的数据，而非写死在模板里 */
    private static List<Object> requirements() {
        List<Object> list = new ArrayList<>();
        list.add(req("操作系统", "Windows 10 / 11（64 位）"));
        list.add(req("运行环境", "无需额外依赖，安装包自带运行时"));
        list.add(req("磁盘空间", "安装约 300 MB，迁移需要额外目标盘空间"));
        list.add(req("权限", "需要管理员权限以完成系统级操作"));
        return list;
    }

    private static Map<String, Object> req(String label, String value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("value", value);
        return m;
    }

    private static List<Object> stats() {
        List<Object> list = new ArrayList<>();
        list.add(stat("支持迁移应用", "50+"));
        list.add(stat("内置 AI 技能", "20+"));
        list.add(stat("混合定位层数", "3 层"));
        list.add(stat("本地化执行", "100%"));
        return list;
    }

    private static Map<String, Object> stat(String label, String value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("value", value);
        return m;
    }

    private static Map<String, Object> seo() {
        Map<String, Object> m = new LinkedHashMap<>();
        // 关键词与别名都覆盖「zhuzhu Copilot」与「WinAppMigrator」两个品牌，避免只被其中一个词根搜到
        m.put("keywords", "zhuzhu Copilot,WinAppMigrator,Windows应用迁移,C盘瘦身,系统优化,AI桌面助手,内存优化,软件卸载,网络防御,浏览器自动化,MCP客户端");
        m.put("alternateNames", "WinAppMigrator");
        m.put("ogImage", "");
        m.put("lang", "zh-CN");
        return m;
    }

    /**
     * 官网视觉自定义：主题色 / 背景 / Logo / 自定义 CSS 等，后台可逐项覆盖。
     *
     * <p>注意：配色实际以 {@code static/css/site.css} 的 :root 为准，这里只是后台表单的
     * 默认展示值与新增库记录的初始值。head.html 已不再内联注入配色变量 —— 两处并存时
     * 后加载的 :root 会静默覆盖前者，表现为「后台改了配色但线上无变化」。
     */
    private static Map<String, Object> style() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("themeColor", "#3D7DFF");
        m.put("themeColorDark", "#1E4FD0");
        m.put("accentColor", "#7DF9E4");
        m.put("bgColor", "#0A0C11");
        m.put("cardBgColor", "#11141C");
        m.put("textColor", "#F0F3F9");
        m.put("textMuted", "#A8B2C6");
        m.put("logoUrl", "");
        m.put("faviconUrl", "");
        m.put("heroBgImage", "");
        m.put("heroGradient", "linear-gradient(160deg, #0A0C11 0%, #111A2E 55%, #0A0C11 100%)");
        m.put("fontFamily", "");
        m.put("customCss", "");
        m.put("navStyle", "glass");
        m.put("cornerRadius", "14");
        m.put("socialLinks", new ArrayList<>());
        return m;
    }

    /**
     * 页脚内容：备案号 / 联系文字 / 导航链接，以及「底部角标」。
     *
     * <p>角标（badge）是页脚底部独立成行的一条署名，用于放置版权归属、开源许可、
     * 赞助方等对外声明，可在后台逐字自定义，并可选配跳转地址与状态灯。
     * 默认留空 —— 页脚已有品牌名与自动生成的版权行，角标再重复一遍站名与
     * 「开源免费」属于赘述，仅在确有必要时由后台填写。
     */
    private static Map<String, Object> footer() {
        Map<String, Object> m = new LinkedHashMap<>();
        // icp：备案号。留空即不渲染。注意不要拿它放版权声明 ——
        // 页脚已有一行自动生成的「© 年份 站名」，重复声明版权只会造成信息冗余。
        m.put("icp", "");
        m.put("contactLabel", "联系我们");
        List<Object> links = new ArrayList<>();
        links.add(link("功能特性", "/#features"));
        links.add(link("界面实拍", "/gallery"));
        links.add(link("下载安装", "/download"));
        links.add(link("常见问题", "/faq"));
        links.add(link("关于我们", "/about"));
        links.add(link("用户反馈", "/feedback"));
        m.put("links", links);

        // ---- 底部角标（后台可自定义） ----
        // badge：角标正文，留空即整块不渲染（默认留空，避免与版权行重复）
        m.put("badge", "");
        // badgeHref：角标点击跳转地址（可留空，此时角标为纯文本不可点击）
        m.put("badgeHref", "");
        // badgeVisible：角标总开关，关闭时即使有文案也不显示
        m.put("badgeVisible", true);
        // 角标左侧的状态灯样式：pulse=呼吸 / solid=常亮 / none=不显示
        m.put("badgeDot", "pulse");
        // 版权年份起始值，留空只用当前年份
        m.put("copyrightSince", "");
        return m;
    }

    private static Map<String, Object> about() {
        Map<String, Object> m = new LinkedHashMap<>();
        // 页面标题（pageCrumb/pageSection/pageTag）
        m.put("sectionTag", "// 关于我们");
        m.put("sectionTitle", "联系我们");
        m.put("sectionSub", "团队介绍与联系方式");
        // 联系方式列表
        List<Object> contacts = new ArrayList<>();
        contacts.add(contactItem("邮箱", "mailto:admin@winappmigrator.com"));
        contacts.add(contactItem("GitHub", "https://github.com/zhuzhu"));
        m.put("contacts", contacts);
        // 团队成员列表
        List<Object> members = new ArrayList<>();
        members.add(member("项目负责人", "zhuzhu", ""));
        m.put("members", members);
        return m;
    }

    private static Map<String, Object> contactItem(String label, String href) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("href", href);
        return m;
    }

    private static Map<String, Object> member(String role, String name, String avatar) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("role", role);
        m.put("name", name);
        m.put("avatar", avatar);
        return m;
    }

    private static Map<String, Object> link(String label, String href) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("href", href);
        return m;
    }

    /* ---------------- 合并与序列化 ---------------- */

    /**
     * 深合并：以 defaults 为骨架，用 stored 中的真实值覆盖。
     *
     * <p>列表一旦在 stored 中出现就整体采用（含空列表），保证后台删空后不会被默认值复填；
     * stored 中多出的键同样保留，便于前后端字段演进。
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> merge(Map<String, Object> defaults, Map<String, Object> stored) {
        Map<String, Object> out = new LinkedHashMap<>(defaults);
        if (stored == null || stored.isEmpty()) {
            return out;
        }
        for (Map.Entry<String, Object> e : stored.entrySet()) {
            String key = e.getKey();
            Object value = e.getValue();
            Object base = out.get(key);
            if (value instanceof Map<?, ?> vm && base instanceof Map<?, ?> bm) {
                out.put(key, merge((Map<String, Object>) bm, (Map<String, Object>) vm));
            } else if (value != null) {
                out.put(key, value);
            }
        }
        return out;
    }

    /** 宽松解析：内容损坏时返回空 Map，避免整站 500 */
    private Map<String, Object> parse(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return mapper.readValue(json, new TypeReference<Map<String, Object>>() {
            });
        } catch (Exception e) {
            return Map.of();
        }
    }

    private void persist(Map<String, Object> content) {
        try {
            SiteContent row = repo.findByCkey(KEY).orElseGet(SiteContent::new);
            row.setCkey(KEY);
            row.setContentJson(mapper.writeValueAsString(content));
            row.setUpdatedAt(LocalDateTime.now());
            repo.save(row);
        } catch (Exception e) {
            throw new IllegalStateException("官网内容保存失败", e);
        }
    }
}
