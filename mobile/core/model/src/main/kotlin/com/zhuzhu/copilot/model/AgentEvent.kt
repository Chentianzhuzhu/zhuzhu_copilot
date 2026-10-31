package com.zhuzhu.copilot.model

sealed interface AgentEvent {
    data class Thinking(val text: String) : AgentEvent
    data class TokenDelta(val text: String) : AgentEvent
    data class ToolStarted(val call: ToolCall) : AgentEvent
    data class ToolProgress(val toolName: String, val message: String) : AgentEvent
    data class NeedConfirm(val request: ConfirmationRequest) : AgentEvent
    data class ToolResult(val call: ToolCall, val ok: Boolean, val summary: String) : AgentEvent
    data class TaskUpdated(val task: AgentTask) : AgentEvent
    data class Done(val text: String) : AgentEvent
    data class Error(val message: String) : AgentEvent
}

data class ConfirmationRequest(
    val id: String,
    val toolName: String,
    val risk: RiskLevel,
    val summary: String,
    val argumentsJson: String,
)
