package com.zhuzhu.copilot.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TokenBudgetTest {
    @Test
    fun `estimates chinese heavier than english`() {
        assertEquals(5, TokenBudget.estimate("中文字符十"))
        assertEquals(2, TokenBudget.estimate("abcdefgh"))
    }

    @Test
    fun `compresses above 75 percent of usable input`() {
        val compactor = ContextCompactor(windowTokens = 128, maxOutputTokens = 8)
        assertTrue(compactor.shouldCompress(usedTokens = 100))
        assertTrue(!compactor.shouldCompress(usedTokens = 80))
    }
}
