package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.RiskLevel

enum class ConfirmMode { EVERY_STEP, SENSITIVE_AND_ABOVE, AUTO }

class RiskPolicy(private val mode: ConfirmMode) {
    fun requiresConfirmation(risk: RiskLevel): Boolean = when (risk) {
        RiskLevel.DESTRUCTIVE -> true
        RiskLevel.SENSITIVE -> mode != ConfirmMode.AUTO
        RiskLevel.SAFE -> mode == ConfirmMode.EVERY_STEP
    }
}
