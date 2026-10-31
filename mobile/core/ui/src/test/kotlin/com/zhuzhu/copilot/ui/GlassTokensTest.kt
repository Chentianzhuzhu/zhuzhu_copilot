package com.zhuzhu.copilot.ui

import androidx.compose.ui.graphics.toArgb
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class GlassTokensTest {
    @Test
    fun `standard blur and rim match spec`() {
        assertEquals(22, GlassTokens.StandardBlurDp)
        assertEquals(1.2f, GlassTokens.RimWidthDp, 0.01f)
    }

    @Test
    fun `colors match spec`() {
        assertEquals(0xFF27407A.toInt(), GlassTokens.BluePrimary.toArgb())
    }
}
