package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.llm.LlmDelta
import com.zhuzhu.copilot.model.AgentEvent
import com.zhuzhu.copilot.model.ConfirmationRequest
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.model.Role
import com.zhuzhu.copilot.model.ToolCall
import com.zhuzhu.copilot.model.ToolSpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID

data class ChatTurn(
    val role: Role,
    val content: String,
    val toolCalls: List<ToolCall> = emptyList(),
    val toolCallId: String? = null,
)

interface LlmCaller {
    suspend fun stream(messages: List<ChatTurn>, tools: List<ToolSpec>): Flow<LlmDelta>
}

class AgentEngine(
    private val llm: LlmCaller,
    private val registry: ToolRegistry,
    private val riskPolicy: RiskPolicy,
    private val gateway: ConfirmationGateway,
    private val config: AgentConfig = AgentConfig(),
    private val grantedPermissions: () -> Set<String> = { setOf("accessibility") },
) {
    private var activeJob: Job? = null

    fun cancel() {
        activeJob?.cancel()
    }

    suspend fun run(
        prompt: String,
        context: ToolContext,
        onEvent: (AgentEvent) -> Unit,
    ): String = withContext(Dispatchers.Default) {
        activeJob = coroutineContext[Job]
        val messages = mutableListOf(ChatTurn(Role.USER, prompt))
        val sessionAllowed = mutableSetOf<String>()
        var steps = 0
        var lastText = ""

        withTimeoutOrNull(config.maxDurationMs) {
            while (steps < config.maxSteps) {
                if (context.isCancelled()) break
                steps++

                val tools = registry.specsFor(grantedPermissions())
                val deltas = llm.stream(messages, tools).toList()
                val text = deltas.filterIsInstance<LlmDelta.Text>().joinToString("") { it.text }
                deltas.filterIsInstance<LlmDelta.Reasoning>().forEach { onEvent(AgentEvent.Thinking(it.text)) }
                deltas.filterIsInstance<LlmDelta.Text>().forEach { onEvent(AgentEvent.TokenDelta(it.text)) }
                lastText = text

                val calls = assembleToolCalls(deltas)
                if (calls.isEmpty()) {
                    onEvent(AgentEvent.Done(text))
                    return@withTimeoutOrNull text
                }

                messages += ChatTurn(Role.ASSISTANT, text, calls)

                for (call in calls) {
                    if (context.isCancelled()) break
                    val tool = registry.find(call.name)
                    if (tool == null) {
                        messages += ChatTurn(Role.TOOL, "tool not found: ${call.name}", toolCallId = call.id)
                        onEvent(AgentEvent.ToolResult(call, false, "工具不存在"))
                        continue
                    }
                    val risk = tool.spec.risk
                    val needsConfirm = riskPolicy.requiresConfirmation(risk) &&
                        call.name !in sessionAllowed
                    if (needsConfirm) {
                        val request = ConfirmationRequest(
                            id = UUID.randomUUID().toString(),
                            toolName = call.name,
                            risk = risk,
                            summary = tool.spec.description,
                            argumentsJson = call.argumentsJson,
                        )
                        onEvent(AgentEvent.NeedConfirm(request))
                        when (gateway.confirm(request)) {
                            ConfirmationDecision.ALLOW_ONCE -> Unit
                            ConfirmationDecision.ALLOW_SESSION -> {
                                if (risk == RiskLevel.DESTRUCTIVE) {
                                    messages += ChatTurn(Role.TOOL, "destructive action requires explicit confirmation", toolCallId = call.id)
                                    onEvent(AgentEvent.ToolResult(call, false, "危险操作不允许会话常驻授权"))
                                    continue
                                }
                                sessionAllowed += call.name
                            }
                            ConfirmationDecision.DENY -> {
                                messages += ChatTurn(Role.TOOL, "user denied", toolCallId = call.id)
                                onEvent(AgentEvent.ToolResult(call, false, "用户拒绝"))
                                continue
                            }
                        }
                    }

                    onEvent(AgentEvent.ToolStarted(call))
                    val result = executeWithRetry(tool, call, context) { msg ->
                        onEvent(AgentEvent.ToolProgress(call.name, msg))
                    }
                    messages += ChatTurn(Role.TOOL, result.summary, toolCallId = call.id)
                    onEvent(AgentEvent.ToolResult(call, result.ok, result.summary))
                }
            }
            onEvent(AgentEvent.Error("达到步数或时长上限"))
            lastText.ifEmpty { "任务未完成：超过步数上限" }
        } ?: run {
            onEvent(AgentEvent.Error("任务超时"))
            "任务超时"
        }
    }

    private suspend fun executeWithRetry(
        tool: AgentTool,
        call: ToolCall,
        context: ToolContext,
        onProgress: (String) -> Unit,
    ): ToolResult {
        var last = ToolResult(false, "未执行")
        repeat(config.maxToolRetries + 1) { attempt ->
            if (context.isCancelled()) return ToolResult(false, "已取消")
            last = runCatching { tool.execute(call.argumentsJson, context.copy(onProgress = onProgress)) }
                .getOrElse { ToolResult(false, "执行异常：${it.message}") }
            if (last.ok) return last
            if (attempt < config.maxToolRetries) onProgress("重试 ${attempt + 1}")
        }
        return last
    }

    private fun assembleToolCalls(deltas: List<LlmDelta>): List<ToolCall> {
        val builder = linkedMapOf<Int, ToolCallAccumulator>()
        deltas.filterIsInstance<LlmDelta.ToolCallDelta>().forEach { d ->
            val acc = builder.getOrPut(d.index) { ToolCallAccumulator() }
            d.id?.let { acc.id = it }
            d.name?.let { acc.name = it }
            d.arguments?.let { acc.arguments += it }
        }
        return builder.values.mapNotNull { acc ->
            val name = acc.name ?: return@mapNotNull null
            ToolCall(
                id = acc.id.ifEmpty { UUID.randomUUID().toString() },
                name = name,
                argumentsJson = acc.arguments.ifEmpty { "{}" },
            )
        }
    }

    private class ToolCallAccumulator(
        var id: String = "",
        var name: String = "",
        var arguments: String = "",
    )
}
