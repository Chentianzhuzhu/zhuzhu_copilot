package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.ToolSpec

data class ToolResult(
    val ok: Boolean,
    val summary: String,
    val data: String = "",
)

data class ToolContext(
    val taskId: String,
    val isCancelled: () -> Boolean = { false },
    val onProgress: (String) -> Unit = {},
)

interface AgentTool {
    val spec: ToolSpec
    val requiredPermission: String? get() = null
    suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult
}
