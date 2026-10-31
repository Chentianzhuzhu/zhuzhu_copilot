package com.zhuzhu.copilot.common

import org.junit.Assert.assertEquals
import org.junit.Test

class RedactorTest {
    @Test
    fun `mask phone number`() {
        assertEquals("手机号 138****5678", Redactor.mask("手机号 13812345678"))
    }

    @Test
    fun `mask six digit code`() {
        assertEquals("验证码 ***", Redactor.mask("验证码 123456"))
    }
}
