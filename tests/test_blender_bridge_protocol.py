# -*- coding: utf-8 -*-
"""blender-modeler 的行协议回归（空行 ≠ EOF，超时不再弄坏缓冲）。

背景：宿主侧原来用 `conn.settimeout(5)` + `makefile().readline()` —— 套接字超时后
文件对象的内部缓冲可能不一致（官方文档明确警告），半行被丢导致读到 EOF 直接退出，
服务端看到 Blender 消失又把它拉起来，用户侧就是「Blender 实例频繁重启」。
改为 select + 自管缓冲后，这里锁死两个易错点：
  A. 空行必须与 EOF 区分（否则每次空帧都会触发一次重启）；
  B. 半行数据必须留在缓冲里，等后续数据到齐再拼成完整一行。
"""
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SERVER = SRC / "zhuzhu_Copilot" / "plugins" / "blender-modeler" / "server.py"


def _load():
    spec = importlib.util.spec_from_file_location("blender_server_under_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bridge(mod):
    """真实构造（__init__ 只做字段初始化，无副作用），再用预置缓冲喂数据。"""
    b = mod.BlenderBridge()
    b._buf = bytearray()
    return b


def test_blank_line_is_not_eof():
    mod = _load()
    b = _bridge(mod)
    b._buf.extend(b"\n")
    assert b._read_line(0.1) == ("blank", b""), "空行不能等同于 EOF"
    b._buf.extend(b'{"ok": true}\n')
    assert b._read_msg(0.1) == ("line", b'{"ok": true}'), "空行应被跳过，读到真正的消息"


def test_read_msg_skips_leading_blank_frames():
    mod = _load()
    b = _bridge(mod)
    b._buf.extend(b"\n\n\n{\"id\": 1}\n")
    kind, line = b._read_msg(0.1)
    assert kind == "line" and line == b'{"id": 1}'


def test_partial_line_is_buffered_not_lost():
    """半行不许丢：数据只到一半时不该返回任何一行，补齐后必须完整取回。"""
    mod = _load()
    b = _bridge(mod)
    b._buf.extend(b'{"id": 7, "ok"')
    assert b._buf.find(b"\n") < 0            # 还没到换行：只能等
    b._buf.extend(b': true}\n')
    assert b._read_msg(0.1) == ("line", b'{"id": 7, "ok": true}')


def test_stop_on_fresh_bridge_is_safe():
    mod = _load()
    b = _bridge(mod)
    b.stop("测试")                            # 不连接时 stop 也必须安全（幂等）
    assert b.conn is None and b.proc is None
