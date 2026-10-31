package com.zhuzhu.copilot.agent

data class AgentConfig(
    val maxSteps: Int = 30,
    val maxDurationMs: Long = 10 * 60 * 1000,
    val maxToolRetries: Int = 2,
)
