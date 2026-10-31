package com.zhuzhu.copilot.agent

object Untrusted {
    fun wrap(source: String, content: String): String =
        "<untrusted source=\"$source\">\n${content.take(6000)}\n</untrusted>"
}
