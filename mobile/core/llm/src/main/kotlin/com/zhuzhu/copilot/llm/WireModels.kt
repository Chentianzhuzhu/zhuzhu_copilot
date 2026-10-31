package com.zhuzhu.copilot.llm

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

object WireModels {
    @Serializable
    data class ChatRequest(
        val model: String,
        val messages: List<WireMessage>,
        val tools: List<WireTool> = emptyList(),
        val stream: Boolean = true,
    )

    @Serializable
    data class WireMessage(
        val role: String,
        val content: String? = null,
        @SerialName("tool_calls") val toolCalls: List<ToolCallOut>? = null,
        @SerialName("tool_call_id") val toolCallId: String? = null,
    )

    @Serializable
    data class ToolCallOut(
        val id: String,
        val type: String = "function",
        val function: FunctionCall,
    )

    @Serializable
    data class FunctionCall(val name: String, val arguments: String)

    @Serializable
    data class WireTool(val type: String = "function", val function: WireFunction)

    @Serializable
    data class WireFunction(
        val name: String,
        val description: String,
        val parameters: JsonObject,
    )

    @Serializable
    data class StreamChunk(val choices: List<StreamChoice> = emptyList())

    @Serializable
    data class StreamChoice(
        val delta: StreamDelta = StreamDelta(),
        @SerialName("finish_reason") val finishReason: String? = null,
    )

    @Serializable
    data class StreamDelta(
        val content: String? = null,
        @SerialName("reasoning_content") val reasoning: String? = null,
        @SerialName("tool_calls") val toolCalls: List<StreamToolCall>? = null,
    )

    @Serializable
    data class StreamToolCall(
        val index: Int = 0,
        val id: String? = null,
        val function: StreamFunction? = null,
    )

    @Serializable
    data class StreamFunction(val name: String? = null, val arguments: String? = null)
}
