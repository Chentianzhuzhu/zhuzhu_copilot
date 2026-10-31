package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.ConfirmationRequest

enum class ConfirmationDecision { ALLOW_ONCE, ALLOW_SESSION, DENY }

fun interface ConfirmationGateway {
    suspend fun confirm(request: ConfirmationRequest): ConfirmationDecision
}
