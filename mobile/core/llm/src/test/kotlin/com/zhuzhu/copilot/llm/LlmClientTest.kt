package com.zhuzhu.copilot.llm

import com.zhuzhu.copilot.llm.WireModels.ChatRequest
import com.zhuzhu.copilot.llm.WireModels.WireMessage
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Test

class LlmClientTest {
    @Test
    fun `streams text deltas and tool calls`() = runTest {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setHeader("Content-Type", "text/event-stream").setBody(
                """
                data: {"choices":[{"delta":{"content":"你好"}}]}
                data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"tap_node","arguments":"{\"idx\":1}"}}]}}]}
                data: [DONE]
                """.trimIndent()
            )
        )
        server.start()

        val client = LlmClient()
        val deltas = client.stream(
            baseUrl = server.url("/v1").toString().trimEnd('/'),
            apiKey = "test-key",
            request = ChatRequest(
                model = "test-model",
                messages = listOf(WireMessage(role = "user", content = "hi")),
            ),
        ).toList()

        assertEquals("你好", deltas.filterIsInstance<LlmDelta.Text>().joinToString("") { it.text })
        val tool = deltas.filterIsInstance<LlmDelta.ToolCallDelta>().first()
        assertEquals("tap_node", tool.name)
        assertEquals(0, tool.index)
        server.shutdown()
    }
}
