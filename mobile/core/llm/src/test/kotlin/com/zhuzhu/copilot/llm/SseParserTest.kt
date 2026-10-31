package com.zhuzhu.copilot.llm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SseParserTest {
    @Test
    fun `extracts data payload`() {
        assertEquals("""{"a":1}""", SseParser.payloadOf("""data: {"a":1}"""))
    }

    @Test
    fun `done marker yields null`() {
        assertNull(SseParser.payloadOf("data: [DONE]"))
    }

    @Test
    fun `ignores comment lines`() {
        assertNull(SseParser.payloadOf(": keep-alive"))
    }
}
