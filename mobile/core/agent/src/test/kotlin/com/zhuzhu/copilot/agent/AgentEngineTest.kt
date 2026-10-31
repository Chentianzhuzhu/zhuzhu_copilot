package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.llm.LlmDelta
import com.zhuzhu.copilot.model.AgentEvent
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.model.ToolSpec
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.asFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class AgentEngineTest {
    private class FakeLlm(private val rounds: List<List<LlmDelta>>) : LlmCaller {
        var calls = 0
        override suspend fun stream(messages: List<ChatTurn>, tools: List<ToolSpec>): Flow<LlmDelta> =
            rounds[calls++].asFlow()
    }

    private class FakeTool : AgentTool {
        var executed = 0
        override val spec = ToolSpec("tap_node", "点击", """{"type":"object"}""", RiskLevel.SENSITIVE)
        override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
            executed++
            return ToolResult(true, "clicked")
        }
    }

    @Test
    fun `plain answer ends loop`() = runTest {
        val llm = FakeLlm(listOf(listOf(LlmDelta.Text("你好"), LlmDelta.Finished)))
        val engine = AgentEngine(llm, ToolRegistry(emptySet()), RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE), { ConfirmationDecision.DENY }, AgentConfig())
        val events = mutableListOf<AgentEvent>()
        val answer = engine.run("hi", ToolContext("t1")) { events += it }
        assertEquals("你好", answer)
    }

    @Test
    fun `tool call executes then answers`() = runTest {
        val tool = FakeTool()
        val llm = FakeLlm(
            listOf(
                listOf(
                    LlmDelta.ToolCallDelta(0, "call_1", "tap_node", """{"idx":1}"""),
                    LlmDelta.Finished,
                ),
                listOf(LlmDelta.Text("完成"), LlmDelta.Finished),
            )
        )
        val engine = AgentEngine(
            llm, ToolRegistry(setOf(tool)),
            RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE),
            { ConfirmationDecision.ALLOW_ONCE },
            AgentConfig(),
        )
        val answer = engine.run("点击", ToolContext("t1")) {}
        assertEquals(1, tool.executed)
        assertEquals("完成", answer)
    }

    @Test
    fun `denied confirmation skips tool`() = runTest {
        val tool = FakeTool()
        val llm = FakeLlm(
            listOf(
                listOf(LlmDelta.ToolCallDelta(0, "call_1", "tap_node", """{"idx":1}"""), LlmDelta.Finished),
                listOf(LlmDelta.Text("已取消"), LlmDelta.Finished),
            )
        )
        val engine = AgentEngine(
            llm, ToolRegistry(setOf(tool)),
            RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE),
            { ConfirmationDecision.DENY },
            AgentConfig(),
        )
        engine.run("点击", ToolContext("t1")) {}
        assertEquals(0, tool.executed)
    }
}
