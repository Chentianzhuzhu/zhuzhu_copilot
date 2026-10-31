package com.zhuzhu.copilot.model

enum class Role { SYSTEM, USER, ASSISTANT, TOOL }

data class ToolCall(
    val id: String,
    val name: String,
    val argumentsJson: String,
)

data class ChatMessage(
    val id: String,
    val role: Role,
    val content: String = "",
    val createdAt: Long = 0L,
    val toolCalls: List<ToolCall> = emptyList(),
    val toolCallId: String? = null,
    val compressed: Boolean = false,
)
