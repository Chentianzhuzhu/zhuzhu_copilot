"""文件隔离区测试：隔离 / 恢复 往返（临时隔离根）"""
import pytest

from zhuzhu_Copilot.core.security_engine.quarantine import Quarantine


@pytest.fixture()
def q(tmp_path, monkeypatch):
    inst = Quarantine()
    monkeypatch.setattr(inst, "root", tmp_path / "quarantine")
    inst.files_dir = inst.root / "files"
    inst.meta_dir = inst.root / "meta"
    return inst


def test_isolate_then_restore_roundtrip(q, tmp_path):
    src = tmp_path / "in" / "doc.exe"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"payload" * 10)
    assert q.isolate(str(src), "测试隔离")
    assert not src.exists()
    items = q.list_items()
    assert len(items) == 1 and items[0]["restorable"] is True
    assert q.restore(items[0]["id"])
    assert src.exists() and src.read_bytes() == b"payload" * 10
    assert q.list_items() == []


def test_isolate_missing_file(q, tmp_path):
    assert not q.isolate(str(tmp_path / "nope.exe"))


def test_restore_conflict_appends_suffix(q, tmp_path):
    src = tmp_path / "conflict.exe"
    src.write_bytes(b"aaa")
    assert q.isolate(str(src), "x")
    src.write_bytes(b"bbb")           # 原位置被新的同名文件占据
    items = q.list_items()
    assert q.restore(items[0]["id"])
    assert (tmp_path / "conflict.exe.restored").exists()


def test_singleton_uses_configured_root(monkeypatch):
    from zhuzhu_Copilot.core.security_engine.config import config
    monkeypatch.setitem(config.data.setdefault("quarantine", {}),
                        "root", "%TEMP%/winapp_q_singleton_test")
    inst = Quarantine()
    assert "winapp_q_singleton_test" in str(inst.root)