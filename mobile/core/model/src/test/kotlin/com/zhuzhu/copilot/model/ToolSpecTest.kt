package com.zhuzhu.copilot.model

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolSpecTest {
    @Test
    fun `parameters json round trips`() {
        val spec = ToolSpec(
            name = "tap_node",
            description = "点击节点",
            parametersJson = """{"type":"object","properties":{"idx":{"type":"integer"}}}""",
            risk = RiskLevel.SENSITIVE,
        )
        val decoded = Json.decodeFromString<ToolSpec>(Json.encodeToString(spec))
        assertEquals("tap_node", decoded.name)
        assertEquals(RiskLevel.SENSITIVE, decoded.risk)
        assertTrue(decoded.parametersJson.contains("idx"))
    }
}
