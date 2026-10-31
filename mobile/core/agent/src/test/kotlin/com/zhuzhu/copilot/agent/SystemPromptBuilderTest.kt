package com.zhuzhu.copilot.agent

import org.junit.Assert.assertTrue
import org.junit.Test

class SystemPromptBuilderTest {
    @Test
    fun `prompt declares untrusted data rule`() {
        val prompt = SystemPromptBuilder.build(deviceInfo = "Android 16", permissions = setOf("accessibility"))
        assertTrue(prompt.contains("不可信数据"))
        assertTrue(prompt.contains("严禁执行"))
    }
}
