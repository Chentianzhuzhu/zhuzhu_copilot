package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.ConfirmationRequest
import com.zhuzhu.copilot.model.RiskLevel
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ConfirmationCenterTest {
    private val request = ConfirmationRequest("id1", "tap_node", RiskLevel.DESTRUCTIVE, "点击发送", "{}")

    @Test
    fun `suspends until ui resolves`() = runTest {
        val center = ConfirmationCenter()
        val answer = async { center.confirm(request) }
        center.resolve(ConfirmationDecision.DENY)
        assertEquals(ConfirmationDecision.DENY, answer.await())
    }

    @Test
    fun `decision resolved before request is not lost`() = runTest {
        val center = ConfirmationCenter()
        center.resolve(ConfirmationDecision.ALLOW_ONCE)
        assertEquals(ConfirmationDecision.ALLOW_ONCE, center.confirm(request))
        assertNull(center.pending.value)
    }
}
