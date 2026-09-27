package com.zhuzhu.update.web;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 图标注册表：淡灰线性矢量图标（stroke 由 CSS 控制，模板统一 24x24 viewBox）。
 *
 * <p>后台只能从白名单里选图标名，模板渲染时也只取注册表内的路径，
 * 因此图标字段不存在注入任意 SVG 的可能。
 */
public final class Icons {

    /** 默认图标（未知或未填写时使用） */
    public static final String DEFAULT = "square";

    private static final Map<String, String> PATHS = new LinkedHashMap<>();

    static {
        PATHS.put("migrate", "<path d=\"M4 7h9l2 3h5v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Z\"/><path d=\"M8 15h8m0 0-2.5-2.5M16 15l-2.5 2.5\"/>");
        PATHS.put("ai", "<rect x=\"3\" y=\"4\" width=\"18\" height=\"12\" rx=\"2\"/><path d=\"M8 20h8M12 16v4\"/><circle cx=\"12\" cy=\"10\" r=\"2.2\"/><path d=\"M12 5.4V7m0 6v1.6M6.8 10H8m8 0h1.2\"/>");
        PATHS.put("optimize", "<path d=\"M13 2 4 14h6l-1 8 9-12h-6l1-8Z\"/>");
        PATHS.put("shield", "<path d=\"M12 3l7 4v5c0 4.6-3 7.6-7 9-4-1.4-7-4.4-7-9V7l7-4Z\"/><path d=\"M9 12l2.2 2.2L15.5 10\"/>");
        PATHS.put("network", "<circle cx=\"12\" cy=\"5\" r=\"2.4\"/><circle cx=\"5\" cy=\"19\" r=\"2.4\"/><circle cx=\"19\" cy=\"19\" r=\"2.4\"/><path d=\"M12 7.4v4.2m0 0-5.6 5.5m5.6-5.5 5.6 5.5\"/>");
        PATHS.put("uninstall", "<path d=\"M4 7h16M9.5 7V4.5h5V7\"/><path d=\"M6 7l1 13h10l1-13\"/><path d=\"M10 11v6m4-6v6\"/>");
        PATHS.put("memory", "<rect x=\"6\" y=\"6\" width=\"12\" height=\"12\" rx=\"2\"/><path d=\"M10 3v3m4-3v3M10 18v3m4-3v3M3 10h3m-3 4h3m15-4h-3m3 4h-3\"/>");
        PATHS.put("clean", "<path d=\"M4 20h16M6 20l1.6-9h8.8L18 20\"/><path d=\"M9.5 11V6.5a2.5 2.5 0 0 1 5 0V11\"/><path d=\"M12 3v1.5\"/>");
        PATHS.put("speed", "<path d=\"M4 18a8 8 0 1 1 16 0\"/><path d=\"M12 18l4.2-6\"/><circle cx=\"12\" cy=\"18\" r=\"1.6\"/>");
        PATHS.put("browser", "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M3 9h18\"/><circle cx=\"6.4\" cy=\"6.5\" r=\".9\"/><circle cx=\"9.2\" cy=\"6.5\" r=\".9\"/>");
        PATHS.put("file", "<path d=\"M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7l-4-4Z\"/><path d=\"M14 3v4h4\"/><path d=\"M9 13h6M9 17h4\"/>");
        PATHS.put("settings", "<circle cx=\"12\" cy=\"12\" r=\"3\"/><path d=\"M12 3v2.2m0 13.6V21M4.2 7.5l1.9 1.1m11.8 6.8 1.9 1.1M4.2 16.5l1.9-1.1m11.8-6.8 1.9-1.1\"/>");
        PATHS.put("spark", "<path d=\"M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z\"/><path d=\"M18.5 16.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z\"/>");
        PATHS.put("square", "<rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"3\"/><path d=\"M8 12h8\"/>");
    }

    private Icons() {
    }

    /** 图标名是否受支持 */
    public static boolean supports(String name) {
        return name != null && PATHS.containsKey(name);
    }

    /** 返回图标内部路径标记；未知名称回落到默认图标 */
    public static String of(String name) {
        return PATHS.getOrDefault(supports(name) ? name : DEFAULT, PATHS.get(DEFAULT));
    }

    /** 供后台下拉框使用的图标名列表 */
    public static java.util.Set<String> names() {
        return java.util.Collections.unmodifiableSet(PATHS.keySet());
    }
}
