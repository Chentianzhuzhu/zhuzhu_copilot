package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.model.ToolSpec
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolRegistryTest {
    private val tap = object : AgentTool {
        override val spec = ToolSpec("tap_node", "点击", """{"type":"object"}""", RiskLevel.SENSITIVE)
        override val requiredPermission: String? = "accessibility"
        override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult =
            ToolResult(ok = true, summary = "clicked")
    }

    @Test
    fun `filters tools by granted permissions`() = runTest {
        val registry = ToolRegistry(setOf(tap))
        val granted = setOf("accessibility")
        assertTrue(registry.specsFor(granted).any { it.name == "tap_node" })
        assertEquals(0, registry.toolNamesFor(emptySet()).size)
    }
}
