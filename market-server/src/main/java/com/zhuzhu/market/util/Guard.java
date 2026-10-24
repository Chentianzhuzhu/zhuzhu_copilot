package com.zhuzhu.market.util;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 服务器侧快速静态扫描（第二道防线）。
 * 客户端在上传前已跑过完整插件校验系统（静态规则 + 本地 LLM 深度评审），
 * 这里再对 zip 里的 .py / .md 文本做一轮轻量规则复核并留存结论。
 */
public final class Guard {
    private Guard() {}

    private static final Pattern[] DANGER = {
            Pattern.compile("(?<![.\\w])(?:eval|exec)\\s*\\("),
            Pattern.compile("\\bos\\.system\\s*\\(|subprocess\\.(?:call|run|Popen)"),
            Pattern.compile("base64\\.b64decode|codecs\\.decode|marshal\\.loads"),
            Pattern.compile("(?i)Login Data|keylog|pynput"),
            Pattern.compile("schtasks|CurrentVersion\\\\Run|\\\\Startup\\\\"),
            Pattern.compile("(?i)Set-MpPreference|DisableAntiSpyware"),
            Pattern.compile("shutil\\.rmtree|format\\s+[A-Za-z]:"),
            Pattern.compile("plugin_guard|security_engine|_copilot_overlay_hook"),
    };

    /** 返回 {file: reason}；空 map = 通过。最多报 5 个文件。 */
    public static Map<String, String> scan(Map<String, String> texts) {
        Map<String, String> out = new LinkedHashMap<>();
        for (var e : texts.entrySet()) {
            for (Pattern p : DANGER) {
                if (p.matcher(e.getValue()).find()) {
                    out.put(e.getKey(), "静态规则命中：" + p.pattern());
                    break;
                }
            }
            if (out.size() >= 5) break;
        }
        return out;
    }
}
