"""微信 ClawBot 文件发送：协议字段（对齐官方 @weixin-claw/core）+ 批量发送 + 工具层回归。

覆盖：
1. WeChatBridge.send_file 请求字段：
   - getuploadurl：media_type=3、hex filekey/aeskey、to_user_id、rawfilemd5、
     filesize（PKCS7 填充后大小）、no_need_thumb
   - CDN 上传：POST 密文；**下载参数取自响应头 x-encrypted-param**（不是 upload_param）
   - sendmessage file_item：type=4、file_name、len（明文字符串）、encrypt_type=1、
     aes_key=base64(hex 字符串)
2. send_files 批量：成功/失败清单、单个失败不中断其余文件
3. agent_tools._send_files_to_wechat 工具层：路径解析、汇总文案、未绑定/无会话提示
4. latest_session：最近活跃会话记录（收消息即更新）
5. 回归：_CORE_TOOLS 常驻两个微信工具（任务裁剪不剔除）
"""
import base64
import hashlib

import pytest

from zhuzhu_Copilot.core import agent_engine, agent_tools, wechat_bridge


@pytest.fixture(autouse=True)
def _isolated_cred(monkeypatch, tmp_path):
    """凭据路径重定向到临时目录，避免读取/写入真实用户凭据。"""
    monkeypatch.setattr(wechat_bridge, "_CRED_PATH", str(tmp_path / "wechat_credentials.json"))


class _FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_bridge(monkeypatch):
    """构造带测试凭据的 bridge，并替代 _post 为可记录的假实现。"""
    b = wechat_bridge.WeChatBridge()
    b._bot_token = "test-token"
    b._baseurl = "https://ilink.test"
    posts = []

    def fake_post(url, body, token="", timeout=40):
        posts.append((url, body, token))
        if url.endswith("getuploadurl"):
            return {"ret": 0, "upload_param": "UP-PARAM-123"}
        if url.endswith("sendmessage"):
            return {"ret": 0, "message_id": "m-1"}
        return {}

    monkeypatch.setattr(wechat_bridge, "_post", fake_post)
    return b, posts


def test_send_file_protocol_fields(tmp_path, monkeypatch):
    """单文件发送：三层请求字段全部对齐官方包。"""
    br, posts = _make_bridge(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _FakeResp(200, {"x-encrypted-param": "DL-PARAM-456"})

    monkeypatch.setattr(wechat_bridge.urllib.request, "urlopen", fake_urlopen)

    payload = b"hello wechat file" * 3          # 非 16 倍数 → 触发 PKCS7 填充
    f = tmp_path / "report v1.pdf"
    f.write_bytes(payload)

    assert br.send_file(str(f), "ctx1", "user1") is True

    # 1) getuploadurl 字段
    up_url, up_body, tok = posts[0]
    assert up_url.endswith("/ilink/bot/getuploadurl")
    assert tok == "test-token"
    assert up_body["media_type"] == 3
    assert up_body["to_user_id"] == "user1"
    assert up_body["rawsize"] == len(payload)
    assert up_body["rawfilemd5"] == hashlib.md5(payload).hexdigest()
    assert up_body["no_need_thumb"] is True
    hexdig = set("0123456789abcdef")
    assert len(up_body["filekey"]) == 32 and set(up_body["filekey"]) <= hexdig
    assert len(up_body["aeskey"]) == 32 and set(up_body["aeskey"]) <= hexdig
    assert up_body["filesize"] == ((len(payload) + 16) // 16) * 16

    # 2) CDN 上传：密文长度 = 填充后大小；URL 携带上传参数与 filekey
    assert "encrypted_query_param=UP-PARAM-123" in captured["url"]
    assert ("filekey=" + up_body["filekey"]) in captured["url"]
    assert isinstance(captured["data"], bytes)
    assert len(captured["data"]) == up_body["filesize"]

    # 3) sendmessage file_item
    sm_url, sm_body, _ = posts[1]
    assert sm_url.endswith("/ilink/bot/sendmessage")
    msg = sm_body["msg"]
    assert msg["from_user_id"] == ""
    assert msg["to_user_id"] == "user1"
    assert msg["context_token"] == "ctx1"
    assert msg["message_type"] == 2 and msg["message_state"] == 2
    assert msg.get("client_id"), "client_id 必须存在（官方 openclaw-weixin:{ts}-{hex}）"
    item = msg["item_list"][0]
    assert item["type"] == 4                    # MessageItemType.FILE
    fi = item["file_item"]
    assert fi["file_name"] == "report v1.pdf"
    assert fi["len"] == str(len(payload))
    assert fi["media"]["encrypt_query_param"] == "DL-PARAM-456"   # 来自响应头，非 upload_param
    assert fi["media"]["encrypt_type"] == 1
    assert fi["media"]["aes_key"] == base64.b64encode(up_body["aeskey"].encode()).decode()


def test_send_file_missing_download_header_fails(tmp_path, monkeypatch):
    """CDN 响应缺少 x-encrypted-param → 发送失败（不再误用上传参数硬发）。"""
    br, posts = _make_bridge(monkeypatch)

    def fake_urlopen(req, timeout=60):
        return _FakeResp(200, {})               # 无 x-encrypted-param

    monkeypatch.setattr(wechat_bridge.urllib.request, "urlopen", fake_urlopen)
    f = tmp_path / "x.txt"
    f.write_bytes(b"data")
    ok, err = br.send_file_detail(str(f), "ctx1", "user1")
    assert ok is False and "x-encrypted-param" in err
    assert len(posts) == 1                      # 未再发 sendmessage


def test_send_files_batch_mixed(tmp_path, monkeypatch):
    """批量：单个失败不中断，返回 (sent, failed) 清单。"""
    br, _ = _make_bridge(monkeypatch)
    calls = []

    def fake_detail(path, context_token="", to_user_id=""):
        calls.append((path, context_token, to_user_id))
        if path.endswith("bad.txt"):
            return False, "CDN 上传失败: HTTP 400"
        return True, ""

    monkeypatch.setattr(br, "send_file_detail", fake_detail)
    a, b, c = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "bad.txt"
    for p in (a, b, c):
        p.write_text("x")

    sent, failed = br.send_files([str(a), str(b), str(c)], "ctx9", "user9", interval=0)
    assert sent == [str(a), str(b)]
    assert failed == [(str(c), "CDN 上传失败: HTTP 400")]
    assert [p for p, _c, _u in calls] == [str(a), str(b), str(c)]
    assert all((c0, u0) == ("ctx9", "user9") for _p, c0, u0 in calls)


def test_latest_session_prefers_recent(tmp_path, monkeypatch):
    """latest_session：收到消息即更新；无记录时退回绑定账号。"""
    br = wechat_bridge.WeChatBridge()
    br._bot_token = "t"
    br._ilink_user_id = "bot-user"
    assert br.latest_session() == ("bot-user", "")
    br._handle_incoming({"message_id": "m1", "from_user_id": "u9",
                         "context_token": "c9", "item_list": []})
    assert br.latest_session() == ("u9", "c9")


class _FakeBridge:
    """工具层测试用假 bridge：记录调用并返回可预期结果。"""

    bound = True

    def __init__(self, uid="u1", ctx="c1", fail_third=False):
        self._uid, self._ctx = uid, ctx
        self.fail_third = fail_third
        self.texts = []
        self.batch = None

    def latest_session(self):
        return self._uid, self._ctx

    def send_text(self, text, context_token="", to_user_id=""):
        self.texts.append(text)
        return True

    def send_files(self, paths, context_token="", to_user_id="", interval=1.2):
        self.batch = (list(paths), context_token, to_user_id)
        if self.fail_third:
            return list(paths)[:2], [(list(paths)[2], "CDN 上传失败")]
        return list(paths), []


def test_send_files_tool_summary(tmp_path, monkeypatch):
    """工具层：批量汇总文案 + message 前置文本 + 会话参数传递。"""
    fb = _FakeBridge(fail_third=True)
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: fb)
    files = [tmp_path / f"f{i}.txt" for i in range(3)]
    for p in files:
        p.write_text("x")

    res = agent_tools._send_files_to_wechat(
        {"paths": [str(p) for p in files], "message": "文件打包好了"})
    assert "成功 2" in res["text"] and "失败 1" in res["text"]
    assert "❌ 失败" in res["text"]
    assert fb.texts == ["文件打包好了"]
    assert fb.batch[1] == "c1" and fb.batch[2] == "u1"


def test_send_files_tool_string_paths_and_missing(tmp_path, monkeypatch):
    """分号/换行分隔字符串兼容；不存在的路径列入跳过提示。"""
    fb = _FakeBridge()
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: fb)
    a = tmp_path / "a.txt"
    a.write_text("x")
    missing = tmp_path / "nope.txt"

    res = agent_tools._send_files_to_wechat({"paths": f"{a};\n{missing}"})
    assert "成功 1" in res["text"] and "失败 0" in res["text"]
    assert "路径不存在已跳过" in res["text"]
    assert fb.batch[0] == [str(a)]


def test_send_files_tool_unbound_and_no_session(tmp_path, monkeypatch):
    """未绑定 / 无活跃会话 → 返回可读提示，不抛异常。"""
    class _Unbound:
        bound = False
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: _Unbound())
    res = agent_tools._send_files_to_wechat({"paths": ["x.txt"]})
    assert "未绑定" in res["text"]

    class _NoSession:
        bound = True

        def latest_session(self):
            return "", ""
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: _NoSession())
    f = tmp_path / "a.txt"
    f.write_text("x")
    res2 = agent_tools._send_files_to_wechat({"paths": [str(f)]})
    assert "暂无活跃会话" in res2["text"]


def test_send_file_tool_single(tmp_path, monkeypatch):
    """单文件工具层：成功文案 + message 前置文本 + 失败原因透出。"""
    class _FB(_FakeBridge):
        def __init__(self, ok=True, err=""):
            super().__init__()
            self.ok, self.err = ok, err
            self.details = []

        def send_file_detail(self, path, context_token="", to_user_id=""):
            self.details.append((path, context_token, to_user_id))
            return self.ok, self.err

    fb = _FB()
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: fb)
    f = tmp_path / "out.docx"
    f.write_bytes(b"x" * 2048)

    res = agent_tools._send_file_to_wechat({"path": str(f), "message": "报告已生成"})
    assert "已发送到微信" in res["text"] and "out.docx" in res["text"]
    assert fb.texts == ["报告已生成"]
    assert fb.details == [(str(f), "c1", "u1")]

    fb2 = _FB(ok=False, err="CDN 上传失败: HTTP 400")
    monkeypatch.setattr(wechat_bridge, "get_bridge", lambda: fb2)
    res2 = agent_tools._send_file_to_wechat({"path": str(f)})
    assert "发送失败" in res2["text"] and "CDN 上传失败" in res2["text"]


def test_task_trim_keeps_wechat_tools(monkeypatch):
    """回归（核心缺陷）：任务命中类别（doc）触发工具裁剪后，微信工具仍须可用。

    此前 send_file_to_wechat 不在 _CORE_TOOLS，任何命中类别的任务（如「把报告发我
    微信」命中 doc）都会被裁掉工具，agent 根本调不到 —— 修复后必须常驻。
    """
    from zhuzhu_Copilot.core import agent_llm
    eng = agent_engine.AgentEngine(llm=agent_llm.LLMClient())
    eng._task_groups = ["doc"]              # 模拟「报告/文档」类任务触发的裁剪
    names = {t["function"]["name"] for t in eng._all_tools()}
    assert {"send_file_to_wechat", "send_files_to_wechat"} <= names
    # 未命中的其他类别工具仍被裁掉（确认裁剪逻辑本身在生效）
    assert "migrate_app" not in names


def test_core_tools_keep_wechat_tools():
    """回归：任务类别裁剪时微信发送工具必须常驻（_CORE_TOOLS）。"""
    assert {"send_file_to_wechat", "send_files_to_wechat"} <= agent_engine._CORE_TOOLS
    # 长网络任务不被 40s 等待上限放弃（否则「已停止等待」与真实发送结果错位）
    assert {"send_file_to_wechat", "send_files_to_wechat"} <= agent_engine._NO_WAIT_TIMEOUT_TOOLS
