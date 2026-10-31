package com.zhuzhu.copilot.model

enum class TaskStatus { RUNNING, PAUSED, DONE, FAILED, ABORTED }

enum class StepStatus { PENDING, RUNNING, DONE, FAILED, SKIPPED }

data class AgentStep(
    val id: String,
    val order: Int,
    val title: String,
    val detail: String = "",
    val status: StepStatus = StepStatus.PENDING,
)

data class AgentTask(
    val id: String,
    val title: String,
    val status: TaskStatus = TaskStatus.RUNNING,
    val steps: List<AgentStep> = emptyList(),
    val failureReason: String = "",
)

data class InstalledApp(val label: String, val packageName: String)

data class ProviderConfig(
    val id: String,
    val name: String,
    val baseUrl: String,
    val model: String,
    val isBuiltin: Boolean = false,
)
