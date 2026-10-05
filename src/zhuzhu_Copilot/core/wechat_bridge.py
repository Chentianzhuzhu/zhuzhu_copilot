"""微信 ClawBot 官方 iLink 协议接入。

官方文档：https://www.wechatbot.dev/en/protocol
Base URL: https://ilinkai.weixin.qq.com

三阶段：
  1. 扫码登录：get_bot_qrcode → get_qrcode_status 轮询 → confirmed 拿到 bot_token
  2. 消息收发：getupdates 长轮询(35s) 收消息 → sendmessage 发回复(必须回传 context_token)
  3. 媒体文件：AES-128-ECB 加密 → CDN 上传 → sendmessage 携带媒体 item

关键概念：context_token —— 每条入站消息携带，回复时必须原样回传，否则无法路由到正确会话。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import struct
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Optional
from urllib.parse import quote

ILINK_BASE = "https://ilinkai.weixin.qq.com"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
CHANNEL_VERSION = "2.0.0"

# 凭据持久化路径
_CRED_PATH = os.path.join(os.path.expanduser("~"), ".zhuzhu_copilot", "wechat_credentials.json")


def _uin_header() -> str:
    """生成 X-WECHAT-UIN：随机4字节 → uint32 → 十进制字符串 → base64。"""
    n = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(n).encode()).decode()


def _headers(bot_token: str) -> dict:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {bot_token}",
        "X-WECHAT-UIN": _uin_header(),
    }


def _post(url: str, body: dict, bot_token: str = "", timeout: int = 40) -> dict:
    """POST JSON，返回解析后的 dict。失败打印详细错误并返回 {}。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    if bot_token:
        for k, v in _headers(bot_token).items():
            req.add_header(k, v)
    else:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            print(f"[WeChatBridge] HTTP {resp.status} from {url.split('/')[-1]}")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"[WeChatBridge] HTTPError {e.code} {e.reason} body={err_body[:500]}")
        try:
            return json.loads(err_body)
        except Exception:
            return {}
    except Exception as e:
        print(f"[WeChatBridge] POST error {type(e).__name__}: {e}")
        return {}


def _get(url: str, timeout: int = 10) -> dict:
    """GET JSON，返回解析后的 dict。失败返回 {}。"""
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 加密（纯标准库实现，不依赖 cryptography）。"""
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_ECB)
        pad_len = 16 - (len(data) % 16)
        padded = data + bytes([pad_len] * pad_len)
        return cipher.encrypt(padded)
    except ImportError:
        pass
    # 回退：使用 ctypes 调用 Windows Crypto API（bcrypt）
    import ctypes
    from ctypes import wintypes
    bcrypt = ctypes.windll.bcrypt
    BCRYPT_AES_ALGORITHM = "AES".encode()
    BCRYPT_CHAIN_MODE_ECB = "ChainingModeECB".encode()
    KEY_OBJECT_LEN = 88
    h_alg = wintypes.BCRYPT_ALG_HANDLE()
    h_key = wintypes.BCRYPT_KEY_HANDLE()
    key_obj = (ctypes.c_ubyte * KEY_OBJECT_LEN)()
    cb_data = wintypes.ULONG()
    bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), BCRYPT_AES_ALGORITHM, None, 0)
    bcrypt.BCryptSetProperty(h_alg, BCRYPT_CHAIN_MODE_ECB, BCRYPT_CHAIN_MODE_ECB, len(BCRYPT_CHAIN_MODE_ECB), 0)
    bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), key_obj, KEY_OBJECT_LEN,
                                      (ctypes.c_ubyte * len(key))(*key), len(key), 0)
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)
    out = (ctypes.c_ubyte * len(padded))()
    bcrypt.BCryptEncrypt(h_key, (ctypes.c_ubyte * len(padded))(*padded), len(padded),
                         None, None, 0, out, len(padded), ctypes.byref(cb_data), 0)
    bcrypt.BCryptDestroyKey(h_key)
    bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)
    return bytes(out)


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 解密（纯标准库实现，不依赖 cryptography）。"""
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_ECB)
        dec = cipher.decrypt(data)
    except ImportError:
        import ctypes
        from ctypes import wintypes
        bcrypt = ctypes.windll.bcrypt
        BCRYPT_AES_ALGORITHM = "AES".encode()
        BCRYPT_CHAIN_MODE_ECB = "ChainingModeECB".encode()
        KEY_OBJECT_LEN = 88
        h_alg = wintypes.BCRYPT_ALG_HANDLE()
        h_key = wintypes.BCRYPT_KEY_HANDLE()
        key_obj = (ctypes.c_ubyte * KEY_OBJECT_LEN)()
        cb_data = wintypes.ULONG()
        bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), BCRYPT_AES_ALGORITHM, None, 0)
        bcrypt.BCryptSetProperty(h_alg, BCRYPT_CHAIN_MODE_ECB, BCRYPT_CHAIN_MODE_ECB,
                                 len(BCRYPT_CHAIN_MODE_ECB), 0)
        bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), key_obj, KEY_OBJECT_LEN,
                                          (ctypes.c_ubyte * len(key))(*key), len(key), 0)
        out = (ctypes.c_ubyte * len(data))()
        bcrypt.BCryptDecrypt(h_key, (ctypes.c_ubyte * len(data))(*data), len(data),
                             None, None, 0, out, len(data), ctypes.byref(cb_data), 0)
        bcrypt.BCryptDestroyKey(h_key)
        bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)
        dec = bytes(out[:cb_data.value])
    # PKCS7 去填充（末字节为填充长度 1..16）
    if dec:
        pad = dec[-1]
        if 1 <= pad <= 16 and len(dec) >= pad and dec[-pad:] == bytes([pad]) * pad:
            dec = dec[:-pad]
    return dec


def _parse_aes_key_b64(aes_key_b64: str) -> bytes:
    """解析 CDNMedia.aes_key（base64）为 16 字节 AES 密钥。

    网络上有两种编码（官方 parseAesKey 同款处理）：
      - base64(16 字节原始密钥)            → 图片（media.aes_key）
      - base64(32 字符 hex 字符串)         → 文件/语音/视频
    第二种 base64 解码后得到 32 个 ASCII hex 字符，需再按 hex 解析。
    """
    decoded = base64.b64decode((aes_key_b64 or "").strip())
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            txt = decoded.decode("ascii")
        except Exception:
            txt = ""
        if len(txt) == 32 and all(c in "0123456789abcdefABCDEF" for c in txt):
            return bytes.fromhex(txt)
    raise ValueError("aes_key 无法解析为 16 字节密钥（base64 解码后 %d 字节）" % len(decoded))


# 入站媒体（用户上传）落盘目录与上限
INBOUND_MAX_BYTES = 100 * 1024 * 1024   # 官方 WEIXIN_MEDIA_MAX_BYTES = 100MB


def _inbox_dir() -> str:
    """用户上传文件的落盘目录：工作目录/wechat_inbox（无工作目录则用户数据目录下）。"""
    try:
        from zhuzhu_Copilot.core import agent_tools
        wd = (agent_tools.get_workdir() or "").strip()
    except Exception:
        wd = ""
    if wd:
        base = os.path.join(wd, "wechat_inbox")
    else:
        base = os.path.join(os.path.expanduser("~"), ".zhuzhu_copilot", "wechat_inbox")
    os.makedirs(base, exist_ok=True)
    return base


def _safe_filename(name: str, fallback: str = "upload.bin") -> str:
    """文件名安全化：去目录成分与非法字符，防路径穿越。"""
    name = os.path.basename(str(name or "").replace("\\", "/").split("/")[-1]).strip()
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name or fallback


def _guess_image_ext(buf: bytes) -> str:
    """按 magic bytes 猜图片扩展名（官方同款：PNG/GIF/WebP，默认 JPG）。"""
    if len(buf) >= 4 and buf[:4] == b"\x89PNG":
        return ".png"
    if len(buf) >= 3 and buf[:3] == b"GIF":
        return ".gif"
    if len(buf) >= 12 and buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _fmt_size(sz: int) -> str:
    if sz >= 1024 * 1024:
        return "%.1f MB" % (sz / 1024 / 1024)
    if sz >= 1024:
        return "%.0f KB" % (sz / 1024)
    return "%d B" % sz


def _read_hint(filename: str) -> str:
    """按扩展名给出读取工具指引（agent 据此选择 read_docx/read_pptx/view_image 等）。"""
    ext = os.path.splitext(filename)[1].lower()
    tips = {
        ".docx": "（请用 read_docx 读取其内容）",
        ".doc": "（请用 extract_text 读取其内容）",
        ".pptx": "（请用 read_pptx 读取其内容）",
        ".ppt": "（请用 extract_text 读取其内容）",
        ".xlsx": "（请用 read_xlsx 读取其内容）",
        ".xls": "（请用 extract_text 读取其内容）",
        ".pdf": "（请用 read_pdf 读取其内容）",
    }
    if ext in tips:
        return tips[ext]
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff", ".tif"):
        return "（请用 view_image 查看图片内容）"
    return "（请用 read_file / extract_text 读取其内容）"


class WeChatBridge:
    """微信 ClawBot iLink 协议客户端。

    用法：
        bridge = WeChatBridge()
        qr = bridge.get_qrcode()          # 获取二维码（qrcode + qrcode_img_content）
        # 显示二维码给用户扫码
        creds = bridge.poll_qrcode(qr["qrcode"])  # 轮询直到 confirmed
        bridge.start(on_message=callback)  # 启动长轮询
        bridge.send_text("你好", context_token, user_id)  # 回复
        bridge.send_file("path/to/file", context_token, user_id)  # 发文件
    """

    def __init__(self):
        self._bot_token: str = ""
        self._ilink_bot_id: str = ""
        self._ilink_user_id: str = ""
        self._baseurl: str = ILINK_BASE
        self._get_updates_buf: str = ""
        self._context_tokens: dict[str, str] = {}  # user_id -> latest context_token
        self._client_ids: dict[str, str] = {}      # user_id -> latest client_id
        self._typing_tickets: dict[str, str] = {}   # user_id -> typing_ticket
        # 最近活跃会话（收到消息即更新）：agent 主动推送消息/文件时的默认目标
        self._last_user_id: str = ""
        self._last_context_token: str = ""
        self._on_message: Optional[Callable[[str, str, str], None]] = None
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_gen = 0                     # 轮询代际：stop()/start() 推进，使旧线程立即失效退出
        self._msg_lock = threading.Lock()      # 去重集合线程安全锁（防并发 in-then-add 竞态穿透）
        self._processed_msg_ids: set = set()  # 已处理消息 ID，防重复
        self._load_credentials()

    # ---------- 凭据持久化 ----------
    def _load_credentials(self):
        try:
            if os.path.isfile(_CRED_PATH):
                with open(_CRED_PATH, encoding="utf-8") as f:
                    d = json.load(f)
                self._bot_token = d.get("bot_token", "")
                self._ilink_bot_id = d.get("ilink_bot_id", "")
                self._ilink_user_id = d.get("ilink_user_id", "")
                self._baseurl = d.get("baseurl", ILINK_BASE)
                print("[WeChatBridge] credentials loaded, bound=", bool(self._bot_token))
        except Exception as e:
            print("[WeChatBridge] load credentials failed:", e)

    def _save_credentials(self):
        try:
            os.makedirs(os.path.dirname(_CRED_PATH), exist_ok=True)
            with open(_CRED_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "bot_token": self._bot_token,
                    "ilink_bot_id": self._ilink_bot_id,
                    "ilink_user_id": self._ilink_user_id,
                    "baseurl": self._baseurl,
                }, f, ensure_ascii=False, indent=2)
            print("[WeChatBridge] credentials saved to", _CRED_PATH)
        except Exception as e:
            print("[WeChatBridge] save credentials failed:", e)

    @property
    def bound(self) -> bool:
        return bool(self._bot_token)

    @property
    def polling(self) -> bool:
        return self._polling

    # ---------- 扫码登录 ----------
    def get_qrcode(self) -> dict:
        """获取登录二维码。返回 {qrcode, qrcode_img_content, raw}。"""
        url = f"{self._baseurl}/ilink/bot/get_bot_qrcode?bot_type=3"
        data = _get(url)
        print("[WeChatBridge] get_qrcode raw:", json.dumps(data, ensure_ascii=False)[:500])
        # 兼容不同返回格式：直接返回 / 嵌套 data 字段
        if "qrcode" in data:
            return data
        if "data" in data and isinstance(data["data"], dict):
            d = data["data"]
            if "qrcode" in d:
                return d
        # 兼容知识助理 API 格式：qrcode_url + qrcode
        if "qrcode_url" in data:
            return {
                "qrcode": data.get("qrcode", ""),
                "qrcode_img_content": data.get("qrcode_url", ""),
            }
        return {}

    def poll_qrcode_status(self, qrcode: str) -> Optional[dict]:
        """轮询扫码状态。返回 None=继续等待，dict=confirmed 凭据。"""
        url = f"{self._baseurl}/ilink/bot/get_qrcode_status?qrcode={qrcode}"
        data = _get(url, timeout=10)
        print("[WeChatBridge] poll_qrcode_status:", json.dumps(data, ensure_ascii=False)[:500])
        status = data.get("status", "")
        if status == "confirmed":
            # 兼容两种格式：嵌套 credentials / 直接返回
            creds = data.get("credentials", {})
            if not creds:
                creds = data  # 直接返回格式
            self._bot_token = creds.get("bot_token", "") or data.get("bot_token", "")
            self._ilink_bot_id = creds.get("ilink_bot_id", "") or data.get("ilink_bot_id", "")
            self._ilink_user_id = creds.get("ilink_user_id", "") or data.get("ilink_user_id", "")
            self._baseurl = data.get("baseurl", self._baseurl)
            print("[WeChatBridge] confirmed: token=", self._bot_token[:30] if self._bot_token else "EMPTY",
                  "bot_id=", self._ilink_bot_id, "user_id=", self._ilink_user_id)
            self._save_credentials()
            return {"status": "confirmed", "credentials": creds}
        if status in ("scaned", "wait"):
            return {"status": status}
        if status == "expired":
            return {"status": "expired"}
        return None

    def logout(self):
        """清除凭据，停止轮询。"""
        self._polling = False
        self._poll_gen += 1   # 使在飞轮询线程立即失效退出
        self._bot_token = ""
        self._ilink_bot_id = ""
        self._ilink_user_id = ""
        self._context_tokens.clear()
        self._typing_tickets.clear()
        self._last_user_id = ""
        self._last_context_token = ""
        try:
            if os.path.isfile(_CRED_PATH):
                os.remove(_CRED_PATH)
        except Exception:
            pass

    # ---------- 消息长轮询 ----------
    def start(self, on_message: Callable[[str, str, str], None]):
        """启动消息长轮询线程。on_message(text, from_user_id, context_token)。

        幂等：回调总是更新为最新调用方的；已有活跃轮询线程时不再重复起线程
        （多个线程共享 getupdates 游标会导致同一条消息被并发拉取、重复分发）。
        """
        if not self._bot_token:
            return
        self._on_message = on_message
        if self._polling and self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._polling = True
        self._poll_gen += 1
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(self._poll_gen,), daemon=True)
        self._poll_thread.start()

    def stop(self):
        """停止长轮询：置停止标记并推进代际。

        旧线程可能正阻塞在 35s 的长轮询请求里；推进代际后，它一返回就立即
        退出，不会因后续 start() 又把 _polling 置回 True 而"复活"并与新线程
        并发轮询（那正是同一条微信消息被分发两次、客户端出现两条的根因）。
        """
        self._polling = False
        self._poll_gen += 1

    def _poll_loop(self, gen: int):
        """长轮询循环：getupdates (35s hold)，收到消息后回调。

        gen：本线程的代际号。stop()/start() 推进 _poll_gen 后，旧代际线程即使
        从在飞的长轮询返回，也会在循环条件处立即退出——保证同一时刻只有一个
        轮询线程在跑、同一条消息只被拉取一次。
        """
        print("[WeChatBridge] poll loop started, gen=", gen,
              "token=", self._bot_token[:20] if self._bot_token else "EMPTY")
        while self._polling and self._bot_token and self._poll_gen == gen:
            body = {
                "base_info": {"channel_version": CHANNEL_VERSION},
                "get_updates_buf": self._get_updates_buf,
            }
            url = f"{self._baseurl}/ilink/bot/getupdates"
            data = _post(url, body, self._bot_token, timeout=40)
            if not data:
                time.sleep(1)
                continue
            print("[WeChatBridge] getupdates:", json.dumps(data, ensure_ascii=False)[:500])
            ret = data.get("ret", 0)
            if ret == -14:  # session expired
                print("[WeChatBridge] session expired (-14), stopping")
                self._bot_token = ""
                self._polling = False
                break
            # 更新游标
            new_buf = data.get("get_updates_buf", "")
            if new_buf:
                self._get_updates_buf = new_buf
            # 处理消息
            msgs = data.get("msgs", [])
            print(f"[WeChatBridge] got {len(msgs)} messages")
            for msg in msgs:
                self._handle_incoming(msg)
            time.sleep(0.1)

    # ---------- 入站媒体（用户上传的文件/图片等） ----------
    def _download_cdn_bytes(self, encrypt_query_param: str) -> bytes:
        """从 CDN 下载密文字节（URL 与官方 buildCdnDownloadUrl 一致）。"""
        url = f"{CDN_BASE}/download?encrypted_query_param={quote(str(encrypt_query_param), safe='')}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read(INBOUND_MAX_BYTES + 1)
        if len(raw) > INBOUND_MAX_BYTES:
            raise ValueError("文件超过 100MB 上限")
        return raw

    def _decrypt_media(self, media: dict, image_item: Optional[dict] = None) -> bytes:
        """下载并解密一个媒体文件。

        对齐官方 downloadMediaFromItem：
          - 图片 aes_key 优先取 image_item.aeskey（hex 字符串 → 转 base64 再解析）
          - 其余取 media.aes_key（base64）；无 aes_key 时按未加密下载（官方 plain 分支）
        """
        param = (media or {}).get("encrypt_query_param", "")
        if not param:
            raise ValueError("媒体缺少 encrypt_query_param")
        raw = self._download_cdn_bytes(param)
        aes_b64 = ""
        if image_item and image_item.get("aeskey"):
            try:
                aes_b64 = base64.b64encode(bytes.fromhex(str(image_item["aeskey"]))).decode()
            except Exception:
                aes_b64 = ""
        if not aes_b64:
            aes_b64 = (media or {}).get("aes_key", "") or ""
        if aes_b64:
            return _aes_ecb_decrypt(raw, _parse_aes_key_b64(aes_b64))
        return raw

    def _save_inbound(self, buf: bytes, filename: str) -> str:
        """入站媒体落盘（wechat_inbox/时间戳_原名），返回本地路径。"""
        d = _inbox_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(d, f"{ts}_{filename}")
        i = 1
        while os.path.exists(path):
            path = os.path.join(d, f"{ts}_{i}_{filename}")
            i += 1
        with open(path, "wb") as f:
            f.write(buf)
        return path

    def _handle_media_item(self, item: dict, item_type: int) -> str:
        """处理一个媒体项（下载→解密→落盘），返回给 agent 的说明文本。"""
        try:
            if item_type == 2:      # IMAGE
                img = item.get("image_item") or {}
                buf = self._decrypt_media(img.get("media") or {}, img)
                path = self._save_inbound(
                    buf, _safe_filename(f"wechat_image_{int(time.time())}{_guess_image_ext(buf)}"))
                print(f"[WeChatBridge] inbound image saved: {path}")
                return (f"[微信图片] 用户发来一张图片（{_fmt_size(len(buf))}），已保存到本地：\n"
                        f"{path}\n（请用 view_image 工具查看图片内容）")
            if item_type == 3:      # VOICE
                v = item.get("voice_item") or {}
                text = str(v.get("text") or "").strip()
                if text:            # 微信已转文字（官方优先用文字）
                    return f"[微信语音] 语音转文字：{text}"
                buf = self._decrypt_media(v.get("media") or {})
                path = self._save_inbound(
                    buf, _safe_filename(f"wechat_voice_{int(time.time())}.silk"))
                print(f"[WeChatBridge] inbound voice saved: {path}")
                return (f"[微信语音] 用户发来一条语音（{_fmt_size(len(buf))}），已保存到本地：\n{path}")
            if item_type == 4:      # FILE
                fi = item.get("file_item") or {}
                buf = self._decrypt_media(fi.get("media") or {})
                fname = _safe_filename(fi.get("file_name") or "wechat_file.bin")
                path = self._save_inbound(buf, fname)
                print(f"[WeChatBridge] inbound file saved: {path}")
                return (f"[微信文件] 用户上传了文件：{fname}（{_fmt_size(len(buf))}），已保存到本地：\n"
                        f"{path}\n{_read_hint(fname)}")
            if item_type == 5:      # VIDEO
                vv = item.get("video_item") or {}
                buf = self._decrypt_media(vv.get("media") or {})
                path = self._save_inbound(
                    buf, _safe_filename(f"wechat_video_{int(time.time())}.mp4"))
                print(f"[WeChatBridge] inbound video saved: {path}")
                return (f"[微信视频] 用户发来一段视频（{_fmt_size(len(buf))}），已保存到本地：\n{path}")
        except Exception as e:
            print(f"[WeChatBridge] inbound media error type={item_type}: {type(e).__name__}: {e}")
            return (f"[微信媒体] 收到媒体消息（type={item_type}），但下载/解密失败："
                    f"{type(e).__name__}: {e}")
        return ""

    def _handle_incoming(self, msg: dict):
        """处理一条入站消息：文本抽取 + 媒体下载解密落盘，合并回调（按 message_id 去重）。"""
        msg_id = str(msg.get("message_id", ""))
        with self._msg_lock:  # 去重判定+登记必须原子：并发轮询线程下防 in-then-add 竞态穿透
            if msg_id and msg_id in self._processed_msg_ids:
                print(f"[WeChatBridge] skip duplicate msg_id={msg_id}")
                return
            if msg_id:
                self._processed_msg_ids.add(msg_id)
                if len(self._processed_msg_ids) > 1000:
                    self._processed_msg_ids = set(list(self._processed_msg_ids)[-500:])
        from_user = msg.get("from_user_id", "")
        # context_token：优先 context_token 字段，其次 client_id
        context_token = msg.get("context_token") or msg.get("client_id") or ""
        client_id = msg.get("client_id", "")
        print(f"[WeChatBridge] from={from_user} ctx={context_token[:50]} client_id={client_id[:30]}")
        if from_user and context_token:
            self._context_tokens[from_user] = context_token
            # 记录最近活跃会话：agent 主动推送（文本/文件）时的默认目标
            self._last_user_id = from_user
            self._last_context_token = context_token
        if from_user and client_id:
            self._client_ids[from_user] = client_id
        item_list = msg.get("item_list", [])
        texts, media_notes = [], []
        for item in item_list:
            item_type = item.get("type", 0)
            if item_type == 1:  # 文本
                text_item = item.get("text_item", {})
                text = (text_item.get("text") if isinstance(text_item, dict) else "") or \
                       item.get("text", "") or item.get("content", "")
                if text:
                    texts.append(str(text))
                else:
                    print("[WeChatBridge] no text found, item keys=", list(item.keys()),
                          "text_item=", text_item)
            elif item_type in (2, 3, 4, 5):   # 图片/语音/文件/视频：下载→解密→落盘
                note = self._handle_media_item(item, item_type)
                if note:
                    media_notes.append(note)
        # 文本 + 媒体说明合并为一条消息交给 agent（文本+文件混合时一并处理）
        body = "\n".join(texts + media_notes).strip()
        if body and self._on_message:
            try:
                print("[WeChatBridge] dispatching:", body[:120].replace("\n", " | "))
                self._on_message(body, from_user, context_token)
            except Exception as e:
                print("[WeChatBridge] on_message callback error:", e)
        elif body:
            print("[WeChatBridge] _on_message is None!")

    # ---------- 发送消息 ----------
    def _raw_sendmessage(self, body: dict, label: str) -> dict:
        """直接调用 sendmessage 并打印结果。"""
        url = f"{self._baseurl}/ilink/bot/sendmessage"
        print(f"\n=== [{label}] ===")
        print(f"body: {json.dumps(body, ensure_ascii=False)[:400]}")
        data = _post(url, body, self._bot_token, timeout=15)
        print(f"resp: {json.dumps(data, ensure_ascii=False)}")
        return data

    def send_text_all_formats(self, text: str, context_token: str, to_user_id: str):
        """诊断：尝试所有可能的 sendmessage 格式组合，打印每种结果。"""
        bi = {"base_info": {"channel_version": CHANNEL_VERSION}}
        results = []

        # 格式1：msg 嵌套 + text_item.text（npm 官方）
        b1 = {**bi, "msg": {"to_user_id": to_user_id, "context_token": context_token,
                            "item_list": [{"type": 1, "text_item": {"text": text + " [1]"}}]}}
        results.append(("1 msg+text_item", self._raw_sendmessage(b1, "format1")))
        time.sleep(1)

        # 格式2：msg 嵌套 + text
        b2 = {**bi, "msg": {"to_user_id": to_user_id, "context_token": context_token,
                            "item_list": [{"type": 1, "text": text + " [2]"}]}}
        results.append(("2 msg+text", self._raw_sendmessage(b2, "format2")))
        time.sleep(1)

        # 格式3：msg + message_type:2 + message_state:2 + text_item
        b3 = {**bi, "msg": {"to_user_id": to_user_id, "context_token": context_token,
                            "message_type": 2, "message_state": 2,
                            "item_list": [{"type": 1, "text_item": {"text": text + " [3]"}}]}}
        results.append(("3 msg+type2+text_item", self._raw_sendmessage(b3, "format3")))
        time.sleep(1)

        # 格式4：msg + message_type:2 + message_state:2 + text
        b4 = {**bi, "msg": {"to_user_id": to_user_id, "context_token": context_token,
                            "message_type": 2, "message_state": 2,
                            "item_list": [{"type": 1, "text": text + " [4]"}]}}
        results.append(("4 msg+type2+text", self._raw_sendmessage(b4, "format4")))
        time.sleep(1)

        # 格式5：扁平 + text_item
        b5 = {**bi, "to_user_id": to_user_id, "context_token": context_token,
              "item_list": [{"type": 1, "text_item": {"text": text + " [5]"}}]}
        results.append(("5 flat+text_item", self._raw_sendmessage(b5, "format5")))
        time.sleep(1)

        # 格式6：扁平 + text
        b6 = {**bi, "to_user_id": to_user_id, "context_token": context_token,
              "item_list": [{"type": 1, "text": text + " [6]"}]}
        results.append(("6 flat+text", self._raw_sendmessage(b6, "format6")))

        print("\n=== SUMMARY ===")
        for label, resp in results:
            print(f"  {label}: {json.dumps(resp, ensure_ascii=False)[:100]}")

    def send_text(self, text: str, context_token: str = "", to_user_id: str = "", client_id: str = "") -> bool:
        """发送文本消息。严格按 @weixin-claw/core 源码 buildTextMessageReq 格式：

        msg: { from_user_id: "", to_user_id, client_id(客户端生成),
               message_type: 2(BOT), message_state: 2(FINISH),
               item_list: [{type:1, text_item:{text}}], context_token }
        """
        if not self._bot_token:
            print("[WeChatBridge] send_text: no bot_token")
            return False
        if not context_token and to_user_id:
            context_token = self._context_tokens.get(to_user_id, "")
        if not context_token:
            print("[WeChatBridge] send_text: no context_token")
            return False
        # 生成客户端 client_id：openclaw-weixin:{timestamp}-{8 hex}
        gen_client_id = f"openclaw-weixin:{int(time.time()*1000)}-{os.urandom(4).hex()}"
        body = {
            "base_info": {"channel_version": CHANNEL_VERSION},
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": gen_client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
        }
        print(f"[WeChatBridge] send_text body: {json.dumps(body, ensure_ascii=False)[:500]}")
        url = f"{self._baseurl}/ilink/bot/sendmessage"
        data = _post(url, body, self._bot_token, timeout=15)
        print(f"[WeChatBridge] send_text resp: {json.dumps(data, ensure_ascii=False)}")
        return data.get("ret", -1) == 0 or "message_id" in data

    def send_typing(self, status: int, to_user_id: str = "", context_token: str = "") -> bool:
        """显示/隐藏"对方正在输入中"。status: 1=开始, 2=停止。"""
        if not self._bot_token or not to_user_id:
            return False
        # 获取 typing_ticket（缓存24小时）
        ticket = self._typing_tickets.get(to_user_id, "")
        if not ticket:
            if not context_token and to_user_id:
                context_token = self._context_tokens.get(to_user_id, "")
            cfg_body = {
                "base_info": {"channel_version": CHANNEL_VERSION},
                "ilink_user_id": to_user_id,
                "context_token": context_token,
            }
            cfg = _post(f"{self._baseurl}/ilink/bot/getconfig", cfg_body, self._bot_token, timeout=10)
            ticket = cfg.get("typing_ticket", "")
            if ticket:
                self._typing_tickets[to_user_id] = ticket
        if not ticket:
            return False
        body = {
            "base_info": {"channel_version": CHANNEL_VERSION},
            "ilink_user_id": to_user_id,
            "typing_ticket": ticket,
            "status": status,
        }
        data = _post(f"{self._baseurl}/ilink/bot/sendtyping", body, self._bot_token, timeout=10)
        return data.get("ret", -1) == 0

    # ---------- 发送文件 ----------
    def latest_session(self) -> tuple:
        """最近一次收到过消息的会话 (to_user_id, context_token)。

        agent 主动推送文件/消息（send_file_to_wechat 等）时的默认目标：
        优先真正收到过消息的用户（_last_*，收消息时更新）；
        无记录时退回 context_tokens 里任一条目，最后退回绑定的 ilink_user_id。
        """
        if self._last_user_id and self._last_context_token:
            return self._last_user_id, self._last_context_token
        for uid, ctx in self._context_tokens.items():
            if ctx:
                return uid, ctx
        return self._ilink_user_id, ""

    def send_file(self, filepath: str, context_token: str = "", to_user_id: str = "") -> bool:
        """发送文件：AES-128-ECB 加密 → CDN 上传 → sendmessage 携带文件 item。

        协议严格对齐官方 @weixin-claw/core（2.0.60，sendFileMessageWeixin / uploadFileAttachmentToWeixin）：
          - getuploadurl: filekey/aeskey 为 hex 字符串；携带 to_user_id、rawsize、rawfilemd5、
            filesize（PKCS7 填充后大小）、no_need_thumb；media_type=3（FILE）
          - CDN 上传密文后，**下载参数取自上传响应头 x-encrypted-param**（不是上传时的 upload_param）
          - sendmessage file_item: type=4（FILE）；media.encrypt_query_param=下载参数、
            aes_key=base64(hex 字符串的 UTF-8 字节)、encrypt_type=1；len=明文大小（字符串）
        """
        ok, _err = self.send_file_detail(filepath, context_token, to_user_id)
        return ok

    def send_file_detail(self, filepath: str, context_token: str = "",
                         to_user_id: str = "") -> tuple:
        """send_file 的详细版：返回 (ok, 失败原因)；供工具层组装可读提示。"""
        if not self._bot_token:
            return False, "未绑定（缺少 bot_token）"
        if not os.path.isfile(filepath):
            return False, "文件不存在"
        # 会话参数缺省时自动解析（先按 to_user_id 查，再退回最近活跃会话）
        if not context_token and to_user_id:
            context_token = self._context_tokens.get(to_user_id, "")
        if not context_token or not to_user_id:
            uid, ctx = self.latest_session()
            context_token = context_token or ctx
            to_user_id = to_user_id or uid
        if not context_token:
            return False, "缺少 context_token（请先在微信里发一条消息）"
        if not to_user_id:
            return False, "缺少 to_user_id"
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
        except Exception as e:
            return False, "读取文件失败: %s" % e
        rawsize = len(raw)
        rawfilemd5 = hashlib.md5(raw).hexdigest()
        filesize = ((rawsize + 16) // 16) * 16   # PKCS7：ceil((n+1)/16)*16
        filekey = os.urandom(16).hex()           # 官方：randomBytes(16).toString("hex")
        aes_key = os.urandom(16)
        aeskey_hex = aes_key.hex()               # getuploadurl 参数用 hex 字符串
        # 获取上传 URL（官方 getUploadUrl 参数字段）
        up_body = {
            "base_info": {"channel_version": CHANNEL_VERSION},
            "filekey": filekey,
            "media_type": 3,        # UploadMediaType.FILE = 3
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        }
        up = _post(f"{self._baseurl}/ilink/bot/getuploadurl", up_body, self._bot_token, timeout=15)
        upload_param = (up or {}).get("upload_param", "")
        if not upload_param:
            return False, "getuploadurl 未返回 upload_param: %s" % json.dumps(up, ensure_ascii=False)[:200]
        # 上传到 CDN（密文）→ 下载参数取自响应头 x-encrypted-param
        encrypted = _aes_ecb_encrypt(raw, aes_key)
        cdn_url = (f"{CDN_BASE}/upload?encrypted_query_param={quote(str(upload_param), safe='')}"
                   f"&filekey={quote(filekey, safe='')}")
        download_param = ""
        try:
            req = urllib.request.Request(cdn_url, data=encrypted, method="POST")
            req.add_header("Content-Type", "application/octet-stream")
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    return False, "CDN 上传失败: HTTP %s" % status
                try:
                    download_param = (resp.headers.get("x-encrypted-param") or "").strip()
                except Exception:
                    download_param = ""
                resp.read()
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = (e.headers.get("x-error-message") if e.headers else "") or \
                    e.read().decode("utf-8", "replace")
            except Exception:
                pass
            return False, "CDN 上传失败: HTTP %s %s" % (e.code, str(msg)[:200])
        except Exception as e:
            return False, "CDN 上传失败: %s %s" % (type(e).__name__, e)
        if not download_param:
            return False, "CDN 上传失败：响应缺少 x-encrypted-param"
        # 发送消息携带文件 item（对齐官方 sendFileMessageWeixin 的 msg 结构）
        client_id = f"openclaw-weixin:{int(time.time()*1000)}-{os.urandom(4).hex()}"
        body = {
            "base_info": {"channel_version": CHANNEL_VERSION},
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,      # BOT
                "message_state": 2,     # FINISH
                "context_token": context_token,
                "item_list": [{
                    "type": 4,          # MessageItemType.FILE = 4
                    "file_item": {
                        "media": {
                            "encrypt_query_param": download_param,
                            # 官方：Buffer.from(aeskey_hex).toString("base64") —— hex 字符串的 UTF-8 字节
                            "aes_key": base64.b64encode(aeskey_hex.encode("utf-8")).decode(),
                            "encrypt_type": 1,
                        },
                        "file_name": os.path.basename(filepath),
                        "len": str(rawsize),
                    },
                }],
            },
        }
        data = _post(f"{self._baseurl}/ilink/bot/sendmessage", body, self._bot_token, timeout=15)
        if (data or {}).get("ret", -1) == 0 or "message_id" in (data or {}):
            return True, ""
        return False, "sendmessage 失败: %s" % json.dumps(data, ensure_ascii=False)[:200]

    def send_files(self, paths, context_token: str = "", to_user_id: str = "",
                   interval: float = 1.2) -> tuple:
        """批量发送文件（逐个推送到微信，间隔 interval 秒防限流）。

        返回 (sent, failed)：sent 为成功的路径列表；failed 为 [(路径, 失败原因), ...]。
        会话参数缺省时自动取最近活跃会话；单个文件的失败不影响其余文件继续发送。
        """
        sent, failed = [], []
        if not context_token or not to_user_id:
            uid, ctx = self.latest_session()
            context_token = context_token or ctx
            to_user_id = to_user_id or uid
        n = len(paths)
        for i, p in enumerate(paths):
            ok, err = self.send_file_detail(str(p), context_token, to_user_id)
            if ok:
                sent.append(str(p))
            else:
                failed.append((str(p), err))
            if i < n - 1:
                time.sleep(max(0.0, float(interval)))
        return sent, failed


# 全局单例
_instance: Optional[WeChatBridge] = None


def get_bridge() -> WeChatBridge:
    global _instance
    if _instance is None:
        _instance = WeChatBridge()
    return _instance
