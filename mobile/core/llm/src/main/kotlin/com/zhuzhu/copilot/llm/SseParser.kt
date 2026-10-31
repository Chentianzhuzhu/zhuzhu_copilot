package com.zhuzhu.copilot.llm

object SseParser {
    fun payloadOf(line: String): String? {
        val trimmed = line.trimEnd()
        if (trimmed.isEmpty() || trimmed.startsWith(":")) return null
        if (!trimmed.startsWith("data:")) return null
        val payload = trimmed.removePrefix("data:").trim()
        return if (payload == "[DONE]" || payload.isEmpty()) null else payload
    }
}
