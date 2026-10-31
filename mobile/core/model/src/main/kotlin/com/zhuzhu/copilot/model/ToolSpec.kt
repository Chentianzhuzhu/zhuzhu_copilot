package com.zhuzhu.copilot.model

import kotlinx.serialization.Serializable

@Serializable
enum class RiskLevel { SAFE, SENSITIVE, DESTRUCTIVE }

@Serializable
data class ToolSpec(
    val name: String,
    val description: String,
    val parametersJson: String,
    val risk: RiskLevel = RiskLevel.SAFE,
    val readOnly: Boolean = false,
)
