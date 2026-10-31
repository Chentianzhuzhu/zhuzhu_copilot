package com.zhuzhu.copilot.agent

object TokenBudget {
    fun estimate(text: String): Int {
        var cjk = 0
        var other = 0
        text.forEach { ch -> if (ch.code in 0x4E00..0x9FFF) cjk++ else other++ }
        return cjk + other / 4
    }

    fun usableInput(windowTokens: Int, maxOutputTokens: Int): Int =
        (windowTokens - maxOutputTokens).coerceAtLeast(1)
}

class ContextCompactor(
    private val windowTokens: Int = 262144,
    private val maxOutputTokens: Int = 8192,
) {
    private val usable = TokenBudget.usableInput(windowTokens, maxOutputTokens)

    fun shouldCompress(usedTokens: Int): Boolean = usedTokens > usable * 0.75

    fun compact(messages: MutableList<ChatTurn>): Boolean {
        val used = messages.sumOf { TokenBudget.estimate(it.content) }
        if (!shouldCompress(used) || messages.size <= 8) return false
        val keep = messages.takeLast(6)
        val dropped = messages.dropLast(6)
        val digest = dropped.joinToString(" / ") { it.content.take(60) }
        messages.clear()
        messages += ChatTurn(
            role = com.zhuzhu.copilot.model.Role.SYSTEM,
            content = "[已压缩历史] $digest",
        )
        messages += keep
        return true
    }

    fun ceilingReached(usedTokens: Int): Boolean = usedTokens > usable * 0.95
}
