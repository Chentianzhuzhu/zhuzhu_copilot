package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.ConfirmationRequest
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 真实确认网关：Agent 循环在此挂起，直到 UI 通过 [resolve] 给出决策。
 * UI 只观察 [pending]，任何放行都必须经过用户点击。
 */
class ConfirmationCenter : ConfirmationGateway {
    private val _pending = MutableStateFlow<ConfirmationRequest?>(null)
    val pending: StateFlow<ConfirmationRequest?> = _pending.asStateFlow()

    private val decisions = Channel<ConfirmationDecision>(Channel.BUFFERED)

    override suspend fun confirm(request: ConfirmationRequest): ConfirmationDecision {
        _pending.value = request
        return try {
            decisions.receive()
        } finally {
            _pending.value = null
        }
    }

    fun resolve(decision: ConfirmationDecision) {
        decisions.trySend(decision)
    }
}
