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
        assertEquals(30, GlassTokens.StandardBlurDp)
        assertEquals(2.0f, GlassTokens.RimWidthDp, 0.01f)
        assertEquals(24, GlassTokens.ElevationDp)
    }

    @Test
    fun `colors match spec`() {
        assertEquals(0xFF27407A.toInt(), GlassTokens.BluePrimary.toArgb())
    }
}
