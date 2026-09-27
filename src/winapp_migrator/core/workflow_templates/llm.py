"""llm.py — 自定义 LLM 客户端模板（Cordis 工作流核心文件）

用途：整体替换内置 LLM 客户端。请实现与内置相同的最小接口：
    __init__(base_url, api_key, model, timeout=60.0, protocol="chat")
    chat_stream(messages, tools=None, tool_choice="auto",
                on_delta=None, on_reasoning=None, stop=None) -> dict
    chat(messages, max_tokens=1024, timeout=60.0, stop=None) -> dict

chat_stream 返回 {"text", "tool_calls", "usage"}；chat 返回 {"text", "usage"}。
支持自定义厂商/协议/加密等。下方为可直接运行的 OpenAI 兼容 /v1/chat/completions 实现。
"""

import json
import urllib.request
import urllib.error
from typing import Callable, Optional


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端（真实 HTTP 调用，非 mock）"""

    def __init__(self, base_url: str = "https://api.agnes-ai.cn/v1",
                 api_key: str = "", model: str = "agnes-2.5-flash",
                 timeout: float = 60.0, protocol: str = "chat"):
        self.base_url = (base_url or "https://api.agnes-ai.cn/v1").rstrip("/")
        self.api_key = api_key
        self.model = model or "agnes-2.5-flash"
        self.timeout = timeout
        self.protocol = (protocol or "chat").lower()
        # 上层工作力度参数（如需支持请在此处理），None 表示不发送
        self.reasoning_effort = None
        self.effort_params = {}

    # ---- 私有工具：构造 OpenAI 兼容请求体 ----
    def _sanitize(self, messages: list) -> list:
        return [m for m in messages if isinstance(m, dict) and m.get("content")]

    def _request(self, payload: dict):
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "WinAppMigrator/CordisWorkflow",
            },
            method="POST")
        return urllib.request.urlopen(req, timeout=self.timeout)

    # ---- 流式对话（SSE） ----
    def chat_stream(self, messages: list, tools: Optional[list] = None,
                    tool_choice="auto",
                    on_delta: Optional[Callable[[str], None]] = None,
                    on_reasoning: Optional[Callable[[str], None]] = None,
                    stop: Optional[Callable[[], bool]] = None) -> dict:
        payload = {"model": self.model, "messages": self._sanitize(messages),
                   "stream": True, "stream_options": {"include_usage": True}}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.effort_params:
            payload.update(self.effort_params)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        resp = self._request(payload)
        text_parts, tool_calls, usage = [], {}, None
        try:
            while True:
                if stop and stop():
                    resp.close()
                    break
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    rc = delta.get("reasoning_content") or delta.get("thinking")
                    if rc and on_reasoning:
                        on_reasoning(rc)
                    content = delta.get("content")
                    if content:
                        text_parts.append(content)
                        if on_delta:
                            on_delta(content)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        cur = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            cur["name"] += fn["name"]
                        if fn.get("arguments"):
                            cur["args"] += fn["arguments"]
        finally:
            try:
                resp.close()
            except Exception:
                pass
        calls = [{"id": tool_calls[i]["id"], "type": "function",
                  "function": {"name": tool_calls[i]["name"],
                               "arguments": tool_calls[i]["args"] or "{}"}}
                 for i in sorted(tool_calls)]
        return {"text": "".join(text_parts), "tool_calls": calls,
                "usage": usage,
                "cache": {"hit": 0, "miss": int((usage or {}).get("prompt_tokens") or 0)}}

    # ---- 非流式单次对话 ----
    def chat(self, messages: list, max_tokens: int = 1024,
             timeout: float = 60.0, stop: Optional[Callable[[], bool]] = None) -> dict:
        payload = {"model": self.model, "messages": self._sanitize(messages),
                   "stream": False, "max_tokens": max_tokens}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "WinAppMigrator/CordisWorkflow",
            },
            method="POST")
        resp = urllib.request.urlopen(req, timeout=timeout or self.timeout)
        try:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        finally:
            resp.close()
        text = "".join(ch.get("text", "") for ch in obj.get("choices") or [])
        return {"text": text, "usage": obj.get("usage")}


# 可选：make_client(config) 工厂，返回自定义 client；存在时优先于 LLMClient 类
# def make_client(config: dict):
#     return LLMClient(config.get("base_url"), config.get("api_key"),
#                      config.get("model"), protocol=config.get("protocol", "chat"))
