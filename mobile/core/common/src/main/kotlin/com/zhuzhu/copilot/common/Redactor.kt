package com.zhuzhu.copilot.common

object Redactor {
    private val phone = Regex("1[3-9]\\d{9}")
    private val code = Regex("\\b\\d{6}\\b")

    fun mask(text: String): String =
        code.replace(phone.replace(text) { m ->
            m.value.replaceRange(3, 7, "****")
        }, "***")
}
