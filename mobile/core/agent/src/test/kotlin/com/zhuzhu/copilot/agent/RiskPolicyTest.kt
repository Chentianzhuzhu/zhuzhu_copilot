package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.RiskLevel
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RiskPolicyTest {
    @Test
    fun `safe never confirms`() {
        val policy = RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE)
        assertFalse(policy.requiresConfirmation(RiskLevel.SAFE))
    }

    @Test
    fun `sensitive confirms by default`() {
        val policy = RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE)
        assertTrue(policy.requiresConfirmation(RiskLevel.SENSITIVE))
    }

    @Test
    fun `destructive always confirms even in auto mode`() {
        assertTrue(RiskPolicy(ConfirmMode.AUTO).requiresConfirmation(RiskLevel.DESTRUCTIVE))
        assertTrue(RiskPolicy(ConfirmMode.EVERY_STEP).requiresConfirmation(RiskLevel.SAFE))
    }
}
