"""微信 ClawBot 入站文件读取：下载→AES 解密→落盘→转交 agent 全链路。

覆盖（对齐官方 @weixin-claw/core 的 downloadMediaFromItem / parseAesKey）：
1. AES-128-ECB 加解密 roundtrip + PKCS7 去填充；
2. aes_key 双编码解析（base64(16字节) / base64(32字符hex)）；
3. 入站文件消息：解密落盘（wechat_inbox）→ 回调文本含本地路径 + read_docx 读取指引；
4. 入站图片：image_item.aeskey（hex）分支 + PNG magic bytes 扩展名 + view_image 指引；
5. 语音转文字优先（voice_item.text）；
6. 文本+文件混合消息合并为一条回调；
7. 下载/解密失败 → 回调包含失败提示（不静默丢失）；
8. 回归：读取类工具（read_docx 等）在 _CORE_TOOLS 常驻（任务裁剪不剔除）。
"""
import base64

import pytest

from zhuzhu_Copilot.core import agent_engine, wechat_bridge


@pytest.fixture(autouse=True)
def _isolated_cred(monkeypatch, tmp_path):
    """凭据路径重定向到临时目录，避免读取/写入真实用户凭据。"""
    monkeypatch.setattr(wechat_bridge, "_CRED_PATH", str(tmp_path / "wechat_credentials.json"))
    monkeypatch.setattr(wechat_bridge, "_inbox_dir", lambda: str(tmp_path))


def _make_bridge(monkeypatch, tmp_path):
    br = wechat_bridge.WeChatBridge()
    br._bot_token = "test-token"
    got = []
    br._on_message = lambda text, uid, ctx: got.append((text, uid, ctx))
    return br, got


def _cipher(buf: bytes, key_hex: str):
    """用 hex key 加密 buf；返回 (密文, base64(16字节) 编码, base64(hex字符串) 编码)。"""
    key = bytes.fromhex(key_hex)
    enc = wechat_bridge._aes_ecb_encrypt(buf, key)
    return enc, base64.b64encode(key).decode(), base64.b64encode(key_hex.encode()).decode()


def test_aes_ecb_roundtrip_pkcs7():
    """加密→解密 roundtrip；密文为 16 倍数；填充被正确去除。"""
    import os
    key = os.urandom(16)
    for data in (b"", b"hello", b"x" * 15, b"y" * 16, b"z" * 17, os.urandom(1000)):
        enc = wechat_bridge._aes_ecb_encrypt(data, key)
        assert len(enc) % 16 == 0 and len(enc) >= len(data)
        assert wechat_bridge._aes_ecb_decrypt(enc, key) == data


def test_parse_aes_key_two_encodings():
    """aes_key 双编码：base64(16字节) 与 base64(32字符hex) 都解析为同一密钥。"""
    key = bytes(range(16))
    hexs = key.hex()
    assert wechat_bridge._parse_aes_key_b64(base64.b64encode(key).decode()) == key
    assert wechat_bridge._parse_aes_key_b64(base64.b64encode(hexs.encode()).decode()) == key
    with pytest.raises(ValueError):
        wechat_bridge._parse_aes_key_b64(base64.b64encode(b"tooshort").decode())


def test_inbound_file_decrypted_saved_and_forwarded(tmp_path, monkeypatch):
    """入站文件：解密落盘 + 回调文本含本地路径与 read_docx 指引。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    payload = b"PK\x03\x04 fake docx content" * 20
    key_hex = "00112233445566778899aabbccddeeff"
    enc, _b64raw, b64_hex = _cipher(payload, key_hex)
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: enc)

    br._handle_incoming({
        "message_id": "m-file-1", "from_user_id": "u1", "context_token": "c1",
        "item_list": [{"type": 4, "file_item": {
            "file_name": "季度报告.docx",
            "media": {"encrypt_query_param": "PARAM", "aes_key": b64_hex},
        }}],
    })

    assert len(got) == 1, "应回调一条合并消息"
    text, uid, ctx = got[0]
    assert uid == "u1" and ctx == "c1"
    assert "季度报告.docx" in text and "read_docx" in text
    files = list(tmp_path.glob("*季度报告.docx"))
    assert len(files) == 1, "文件应落盘到 inbox 目录"
    assert files[0].read_bytes() == payload, "落盘内容必须等于解密后的原文"
    assert str(files[0]) in text, "回调文本应包含真实本地路径"


def test_inbound_file_base64_raw_key_encoding(tmp_path, monkeypatch):
    """文件媒体 aes_key 为 base64(16字节) 编码（另一种在野格式）也能解密。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    payload = b"content with 16-byte raw key" * 3
    key_hex = "ffeeddccbbaa99887766554433221100"
    enc, b64_raw, _b64_hex = _cipher(payload, key_hex)
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: enc)

    br._handle_incoming({
        "message_id": "m-file-2", "from_user_id": "u1", "context_token": "c1",
        "item_list": [{"type": 4, "file_item": {
            "file_name": "data.xlsx",
            "media": {"encrypt_query_param": "P", "aes_key": b64_raw},
        }}],
    })
    assert len(got) == 1 and "read_xlsx" in got[0][0]
    files = list(tmp_path.glob("*data.xlsx"))
    assert len(files) == 1 and files[0].read_bytes() == payload


def test_inbound_image_hex_aeskey_png_ext(tmp_path, monkeypatch):
    """入站图片：image_item.aeskey（hex）分支 + PNG magic bytes → .png + view_image 指引。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"image-bytes" * 10
    key_hex = "aabbccddeeff00112233445566778899"
    enc, _b64raw, _b64_hex = _cipher(png, key_hex)
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: enc)

    br._handle_incoming({
        "message_id": "m-img-1", "from_user_id": "u2", "context_token": "c2",
        "item_list": [{"type": 2, "image_item": {
            "aeskey": key_hex,          # hex 字符串（官方图片分支）
            "media": {"encrypt_query_param": "P"},
        }}],
    })
    assert len(got) == 1
    assert "view_image" in got[0][0] and "微信图片" in got[0][0]
    imgs = list(tmp_path.glob("*.png"))
    assert len(imgs) == 1 and imgs[0].read_bytes() == png


def test_inbound_image_plain_fallback(tmp_path, monkeypatch):
    """图片无 aes_key：按未加密下载（官方 plain 分支）直接落盘。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    jpg = b"\xff\xd8\xff\xe0" + b"jpeg-data" * 5
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: jpg)

    br._handle_incoming({
        "message_id": "m-img-2", "from_user_id": "u2", "context_token": "c2",
        "item_list": [{"type": 2, "image_item": {"media": {"encrypt_query_param": "P"}}}],
    })
    assert len(got) == 1 and "view_image" in got[0][0]
    imgs = list(tmp_path.glob("*.jpg"))
    assert len(imgs) == 1 and imgs[0].read_bytes() == jpg


def test_inbound_voice_text_preferred(tmp_path, monkeypatch):
    """语音：voice_item.text 存在时直接用文字（官方行为），不下载。"""
    br, got = _make_bridge(monkeypatch, tmp_path)

    def _no_download(param):
        raise AssertionError("有语音转文字时不应下载媒体")

    monkeypatch.setattr(br, "_download_cdn_bytes", _no_download)
    br._handle_incoming({
        "message_id": "m-v-1", "from_user_id": "u3", "context_token": "c3",
        "item_list": [{"type": 3, "voice_item": {"text": "帮我看看这份报告"}}],
    })
    assert len(got) == 1
    assert "语音转文字：帮我看看这份报告" in got[0][0]


def test_inbound_text_plus_file_merged(tmp_path, monkeypatch):
    """文本+文件混合：合并为一条回调（文本在前，文件说明在后）。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    payload = b"file with mixed text"
    key_hex = "0123456789abcdef0123456789abcdef"
    enc, _b64raw, b64_hex = _cipher(payload, key_hex)
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: enc)

    br._handle_incoming({
        "message_id": "m-mix", "from_user_id": "u4", "context_token": "c4",
        "item_list": [
            {"type": 1, "text_item": {"text": "请帮我分析这份数据"}},
            {"type": 4, "file_item": {
                "file_name": "sales.xlsx",
                "media": {"encrypt_query_param": "P", "aes_key": b64_hex},
            }},
        ],
    })
    assert len(got) == 1, "混合消息必须合并为一条（否则 agent 分两轮处理）"
    text = got[0][0]
    assert "请帮我分析这份数据" in text and "sales.xlsx" in text
    assert text.index("请帮我分析这份数据") < text.index("sales.xlsx")


def test_inbound_media_failure_reported(tmp_path, monkeypatch):
    """下载/解密失败：回调含失败提示（agent 能如实告知用户，不静默丢失）。"""
    br, got = _make_bridge(monkeypatch, tmp_path)

    def _boom(param):
        raise ConnectionError("CDN unreachable")

    monkeypatch.setattr(br, "_download_cdn_bytes", _boom)
    br._handle_incoming({
        "message_id": "m-bad", "from_user_id": "u5", "context_token": "c5",
        "item_list": [{"type": 4, "file_item": {
            "file_name": "x.pdf",
            "media": {"encrypt_query_param": "P", "aes_key": "bad"},
        }}],
    })
    assert len(got) == 1
    assert "下载/解密失败" in got[0][0]


def test_duplicate_media_message_ignored(tmp_path, monkeypatch):
    """同一 message_id 去重：媒体重复推送不会二次落盘/二次回调。"""
    br, got = _make_bridge(monkeypatch, tmp_path)
    payload = b"dup payload"
    key_hex = "11112222333344445555666677778888"
    enc, _b64raw, b64_hex = _cipher(payload, key_hex)
    monkeypatch.setattr(br, "_download_cdn_bytes", lambda param: enc)
    msg = {
        "message_id": "m-dup", "from_user_id": "u6", "context_token": "c6",
        "item_list": [{"type": 4, "file_item": {
            "file_name": "a.txt",
            "media": {"encrypt_query_param": "P", "aes_key": b64_hex},
        }}],
    }
    br._handle_incoming(msg)
    br._handle_incoming(msg)
    assert len(got) == 1
    assert len(list(tmp_path.glob("*a.txt"))) == 1


def test_core_tools_keep_file_reader_tools():
    """回归：用户上传文件的读取工具必须常驻 _CORE_TOOLS（任务裁剪不剔除）。"""
    assert {"read_docx", "read_pptx", "read_xlsx", "read_pdf",
            "extract_text", "view_image"} <= agent_engine._CORE_TOOLS


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
