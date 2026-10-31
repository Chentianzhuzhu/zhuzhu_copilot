# -*- coding: utf-8 -*-
"""打工猫 Copilot 插件（kitty-work-pet）资产 / 动作 / 服务入口回归。

守护契约：
  A. 6 段动作与 18 根骨骼齐全，双爪分别绑到 L_Paw / R_Paw，笔记本绑 Root；
  B. typing 动画里两只爪子都在键盘范围内交替起落（真的在敲键盘，不是摆拍）；
  C. 单次动作（wave / stretch）播到末尾停住，不会越过段尾回卷；
  D. copilot.json 的服务入口指向可导入的 src/pet.py，start / stop 可调用。

插件装在用户数据目录（~/.zhuzhu_Copilot/copilot_plugins/kitty-work-pet），
未安装时整组用例跳过——仓库测试不依赖某台机器的运行时插件。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("numpy")

PLUGIN_DIR = Path(os.environ.get("KITTY_WORK_PLUGIN")
                  or Path.home() / ".zhuzhu_Copilot" / "copilot_plugins"
                  / "kitty-work-pet")
ASSET_PREFIX = PLUGIN_DIR / "assets" / "kitty_work"
RIG_FILE = PLUGIN_DIR / "src" / "rig.py"
PET_FILE = PLUGIN_DIR / "src" / "pet.py"

CLIPS = {"typing", "idle", "wave", "excited", "sleep", "stretch"}
ONESHOT = {"wave", "stretch"}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rig_mod():
    if not RIG_FILE.is_file() or not ASSET_PREFIX.with_suffix(".npz").is_file():
        pytest.skip("未安装 kitty-work-pet 插件（或缺少烘焙资产）")
    return _load_module("_kitty_work_rig_test", RIG_FILE)


@pytest.fixture(scope="module")
def rig(rig_mod):
    return rig_mod.KittyWorkRig(ASSET_PREFIX)


# ---------- A. 资产 ----------
def test_asset_has_six_clips_and_expected_bones(rig):
    assert {c.name for c in rig.clips} == CLIPS
    assert len(rig.bone_names) == 18, rig.bone_names
    assert {"L_Paw", "R_Paw", "L_Forearm", "R_Forearm"} <= set(rig.bone_names)
    by_name = {c.name: c for c in rig.clips}
    assert by_name["typing"].count == 40 and by_name["typing"].loop
    assert by_name["stretch"].loop is False and by_name["wave"].loop is False
    assert rig.frame_count == sum(c.count for c in rig.clips)


def test_paws_and_laptop_are_bound_to_the_right_bones(rig):
    """双爪必须各绑各的爪子骨；笔记本绑 Root（不随身体晃）。"""
    expect = {"kitty_paw_l": "L_Paw", "kitty_paw_r": "R_Paw",
              "laptop_keys": "Root", "laptop_display": "Root",
              "kitty_head": "Head", "kitty_tail_3": "Tail_3"}
    parts = {p.name: p for p in rig.parts}
    for name, bone in expect.items():
        assert parts[name].bones == [bone], f"{name} 绑到了 {parts[name].bones}"
        assert parts[name].rigid, f"{name} 应为刚性块"
    assert sum(1 for p in rig.parts if "laptop" in p.name) >= 8


# ---------- B. 双爪敲键盘 ----------
def _paw_bounds(rig, side: str, t: float) -> tuple:
    part = next(p for p in rig.parts if p.name == f"kitty_paw_{side}")
    pos, _nrm = rig.skin(part, rig.pose("typing", t))
    return pos.min(axis=0), pos.max(axis=0)


def test_typing_both_paws_tap_the_keyboard_in_turns(rig):
    """逐帧取两爪世界包围盒，对照键帽顶面：
       · 爪子 x/y 必须落在键盘覆盖范围内（不是放在桌面上）；
       · 爪底到键帽顶的间隙保持在 ±2cm 内（既不悬空也不穿模）；
       · 两爪高低差会换向 → 真的是交替起落。
    """
    key_part = next(p for p in rig.parts if p.name == "laptop_keys")
    key_top = float(key_part.pos[:, 2].max())
    key_lo, key_hi = key_part.pos[:, :2].min(axis=0), key_part.pos[:, :2].max(axis=0)

    dur = rig.clip("typing").count / rig.fps
    samples = []
    for i in range(40):
        t = dur * i / 40.0
        rec = {}
        for side in ("l", "r"):
            lo, hi = _paw_bounds(rig, side, t)
            assert lo[0] <= key_hi[0] and hi[0] >= key_lo[0], f"{side} 爪不在键盘 x 范围"
            assert lo[1] <= key_hi[1] and hi[1] >= key_lo[1], f"{side} 爪不在键盘 y 范围"
            rec[side] = lo[2] - key_top
        samples.append(rec)

    # 交替敲击的语义：任意时刻至少有一只爪子贴/压在键帽上（±2cm 内），
    # 另一只抬起敲下（抬升不超过 5cm，太高就像在拍桌子）。
    gaps = [v for rec in samples for v in rec.values()]
    near = [min(rec["l"], rec["r"]) for rec in samples]
    assert all(-0.02 < v < 0.02 for v in near), \
        f"有帧两只爪子都离开键盘：{max(near):.3f}"
    assert min(gaps) > -0.02, f"爪子穿进键帽超过 2cm：{min(gaps):.3f}"
    assert max(gaps) < 0.05, f"抬爪过高（>5cm）：{max(gaps):.3f}"

    # 交替：两爪高低差要换向；单爪自身也要有可见的上下起伏
    signs = [rec["l"] - rec["r"] for rec in samples]
    assert max(abs(v) for v in signs) > 0.01, f"两爪没有交替起落：{max(map(abs, signs)):.4f}"
    # 两爪相位差 0.5：每个周期有两次「零差」瞬间（两爪同时贴键），换向要跨过零点判断
    flips, last = 0, 0.0
    for s in signs:
        if abs(s) < 1e-6:
            continue
        if last and s * last < 0:
            flips += 1
        last = s
    assert flips >= 1, "整个循环里两爪高低关系没有换向"

    for side in ("l", "r"):
        span = max(rec[side] for rec in samples) - min(rec[side] for rec in samples)
        assert span > 0.01, f"{side} 爪上下起伏只有 {span * 1000:.1f}mm"


# ---------- C. 动作与单次收尾 ----------
def test_clip_pose_differs_and_oneshot_stops_at_end(rig):
    assert not (rig.pose("typing", 0.0) == rig.pose("wave", 0.0)).all(), "不同动作姿态不能一样"
    for name in ONESHOT:
        dur = rig.clip(name).count / rig.fps
        end = rig.pose(name, dur - 1e-4)
        beyond = rig.pose(name, dur * 4)      # 超过时长也必须停在同一帧
        assert abs(end - beyond).max() < 1e-6, f"{name} 播完后仍在回卷"


def test_skinning_moves_vertices_and_stays_finite(rig):
    mats = rig.pose("typing", 0.3)
    for part in rig.parts:
        pos, nrm = rig.skin(part, mats)
        assert len(pos) == len(part.pos)
        assert pos.min() > -10 and pos.max() < 10
        assert abs(nrm).max() <= 1.001


# ---------- D. 插件服务入口 ----------
def test_service_manifest_points_to_importable_entry():
    if not PLUGIN_DIR.is_dir():
        pytest.skip("未安装 kitty-work-pet 插件")
    man = json.loads((PLUGIN_DIR / "copilot.json").read_text(encoding="utf-8-sig"))
    services = man.get("services") or []
    assert services, "copilot.json 必须声明 services，否则不会替换桌宠"
    svc = services[0]
    assert svc.get("module") == "src/pet.py"
    assert svc.get("entry") == "start" and svc.get("stop") == "stop"
    assert PET_FILE.is_file(), "服务入口文件不存在"

    pet_mod = _load_module("_kitty_work_pet_entry_test", PET_FILE)
    assert callable(getattr(pet_mod, "start", None))
    assert callable(getattr(pet_mod, "stop", None))
    assert callable(getattr(pet_mod, "replace_pet", None))
    assert callable(getattr(pet_mod, "restore_pet", None))
