# -*- coding: utf-8 -*-
"""随包插件同步（升级）回归。

守护契约：
  A. 随包 revision **更大**时才覆盖安装目录，相等时不动（避免每次启动都重写）；
  B. `config.json` 是用户数据，升级**不得覆盖**（否则用户填的路径/密钥被清空）；
  C. 升级会清掉 __pycache__（旧字节码继续被加载会让「改了代码却没生效」复发）。

背景：电脑操控插件在随包目录里早已修好「取窗口标题永久阻塞 → 所有工具超时」，
但本地副本 revision 没变、从未同步，用户侧一直跑着旧代码。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_plugins  # noqa: E402


def _mk(root: Path, name: str, revision: int, server: str, config: dict) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps(
        {"name": name, "revision": revision}, ensure_ascii=False), encoding="utf-8")
    (d / "server.py").write_text(server, encoding="utf-8")
    (d / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return d


def test_update_skips_when_revision_not_increased(tmp_path):
    src = _mk(tmp_path / "shipped", "demo", 3, "# new\n", {"k": "new"})
    dst = _mk(tmp_path / "installed", "demo", 3, "# old\n", {"k": "old"})
    assert agent_plugins._update_shipped_plugin(src, dst) is False
    assert (dst / "server.py").read_text(encoding="utf-8") == "# old\n"


def test_update_overwrites_code_but_keeps_user_config(tmp_path):
    src = _mk(tmp_path / "shipped", "demo", 4, "# new\n", {"k": "default"})
    dst = _mk(tmp_path / "installed", "demo", 3, "# old\n", {"k": "user-set"})
    cache = dst / "__pycache__"
    cache.mkdir()
    (cache / "server.cpython-313.pyc").write_bytes(b"stale")

    assert agent_plugins._update_shipped_plugin(src, dst) is True
    assert (dst / "server.py").read_text(encoding="utf-8") == "# new\n", "代码必须升级"
    assert json.loads((dst / "config.json").read_text(encoding="utf-8")) == {"k": "user-set"}, \
        "config.json 是用户数据，升级不得覆盖"
    assert not cache.is_dir(), "旧 __pycache__ 必须清掉，否则旧字节码继续被加载"


def test_shipped_plugin_never_ships_config_as_code(tmp_path):
    """随包目录里的 config.json 只用于首次安装的默认值。"""
    assert agent_plugins._USER_DATA_FILE == "config.json"
