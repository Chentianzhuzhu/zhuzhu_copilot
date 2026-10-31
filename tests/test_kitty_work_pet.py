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
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

np = pytest.importorskip("numpy")

PLUGIN_DIR = Path(os.environ.get("KITTY_WORK_PLUGIN")
                  or Path.home() / ".zhuzhu_Copilot" / "copilot_plugins"
                  / "kitty-work-pet")
ASSET_PREFIX = PLUGIN_DIR / "assets" / "kitty_work"
RIG_FILE = PLUGIN_DIR / "src" / "rig.py"
VIEW_FILE = PLUGIN_DIR / "src" / "view.py"
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


def _load_pkg_module(pkg_name: str, base: Path, filename: str):
    """按插件包的相对导入规则加载（view.py 依赖 .rig 的相对导入）。"""
    import types
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(base)]
        sys.modules[pkg_name] = pkg
    name = f"{pkg_name}.{Path(filename).stem}"
    if name in sys.modules:
        return sys.modules[name]
    return _load_module(name, base / filename)


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


def test_animated_bounds_cover_raised_paws(rig):
    """相机取景必须按「全部动画帧的运动包围盒」，否则挥手/伸懒腰会切掉爪子。"""
    ab = rig.meta.get("anim_bounds")
    assert ab and ab.get("min") and ab.get("max"), "资产缺少 anim_bounds"
    static = rig.meta["bounds"]
    assert ab["max"][2] > static["max"][2] + 0.03, \
        f"运动包围盒没有比静态更高：{ab['max'][2]} vs {static['max'][2]}"
    assert ab["min"][2] <= static["min"][2] + 1e-4
    lo, hi = rig.anim_bounds_min, rig.anim_bounds_max
    assert hi[2] > lo[2] and hi[0] > lo[0]


def test_per_clip_bounds_come_from_animation_not_static(rig):
    """逐段包围盒要真实反映动作：挥手/伸懒腰比敲键盘宽一倍左右（相机据此拉远）。"""
    for c in rig.clips:
        assert c.name in rig.clip_bounds, f"{c.name} 缺少逐段包围盒"
    lo_t, hi_t = rig.bounds_for("typing")
    lo_w, hi_w = rig.bounds_for("wave")
    lo_s, hi_s = rig.bounds_for("stretch")
    assert (hi_w[0] - lo_w[0]) > (hi_t[0] - lo_t[0]) + 0.3, "挥手包围盒应明显更宽"
    assert (hi_s[0] - lo_s[0]) > (hi_t[0] - lo_t[0]) + 0.3, "伸懒腰包围盒应明显更宽"
    assert (hi_t[2] - lo_t[2]) > 1.2, "敲键盘时画面高度至少能装下整只猫"


def test_camera_fit_keeps_animated_model_inside_frustum():
    """相机取景数学：按运动包围盒 + 画面留边，上下左右都不能出画。"""
    if not VIEW_FILE.is_file():
        pytest.skip("未安装 kitty-work-pet 插件")
    pkg = "_kitty_work_view_test"
    _load_pkg_module(pkg, VIEW_FILE.parent, "rig.py")
    view_mod = _load_pkg_module(pkg, VIEW_FILE.parent, "view.py")

    lo, hi = [-0.46, -0.71, -0.01], [0.49, 0.55, 1.75]   # 代表性运动包围盒
    aspect = 320.0 / 380.0
    eye, target = view_mod.fit_camera(lo, hi, aspect)
    assert target[0] == pytest.approx((lo[0] + hi[0]) * 0.5)
    assert target[1] == pytest.approx((lo[2] + hi[2]) * 0.5)
    assert eye[2] > 0 and eye[1] > target[1], "相机应在模型前方且略高"

    fov = view_mod._FOV_DEG
    near = max(abs(lo[1]), abs(hi[1]))
    depth = float(eye[2] - near)
    assert depth > 0
    half_v = math.radians(fov * 0.5)
    assert math.atan((hi[2] - eye[1]) / depth) < half_v * 0.95
    assert math.atan((eye[1] - lo[2]) / depth) < half_v * 0.95
    half_h = math.atan(math.tan(half_v) * aspect)
    assert math.atan(max(abs(lo[0] - eye[0]), abs(hi[0] - eye[0])) / depth) \
        < half_h * 0.98


def test_every_clip_projects_fully_on_screen(rig):
    """把每段动作的运动包围盒 8 个角点真正投影一遍：上下左右都必须在画面内。

    这条比只看相机距离更硬：覆盖「包围盒 + 3/4 视角 + 宽高比」整条链路，
    任何一段动作切边都会在这里失败。
    """
    if not VIEW_FILE.is_file():
        pytest.skip("未安装 kitty-work-pet 插件")
    pkg = "_kitty_work_view_test"
    if f"{pkg}.view" not in sys.modules:
        _load_pkg_module(pkg, VIEW_FILE.parent, "rig.py")
        _load_pkg_module(pkg, VIEW_FILE.parent, "view.py")
    view_mod = sys.modules[f"{pkg}.view"]

    aspect = 320.0 / 380.0
    proj = view_mod._perspective(view_mod._FOV_DEG, aspect, 0.05, 100.0)
    up = np.array([0.0, 1.0, 0.0])
    for c in rig.clips:
        lo, hi = rig.bounds_for(c.name)
        eye, target = view_mod.fit_camera(lo, hi, aspect)
        view = view_mod._look_at(eye, target, up)
        corners = np.array(list(
            __import__("itertools").product(*zip(lo, hi))), dtype=np.float64)
        # Blender Z-up → GL Y-up：根变换绕 X -90°，（x,y,z）→（x, z, -y）
        gl = np.stack([corners[:, 0], corners[:, 2], -corners[:, 1],
                       np.ones(len(corners))], axis=1)
        # 渲染侧与 GLSL 同约定（矩阵左乘列向量）：proj @ view @ gl
        clip = (proj @ view @ gl.T).T
        ndc = clip[:, :2] / np.where(np.abs(clip[:, 3:4]) < 1e-9, 1e-9,
                                     clip[:, 3:4])
        assert np.abs(ndc).max() < 0.98, \
            f"{c.name} 有角点出画：{np.abs(ndc).max():.3f}"


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
