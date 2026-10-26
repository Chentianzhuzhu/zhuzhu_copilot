# -*- coding: utf-8 -*-
"""3D 桌宠（kitty_pet.fbx 烘焙资产 + Qt 3D）回归。

守护契约：
  A. 烘焙资产完整、可加载（骨骼 22 根 / 部件绑定正确的骨头）；
  B. 蒙皮真的会算：内置动画 + 附加旋转都要能改变顶点（否则桌宠是块石头）；
  C. 「跟随鼠标」的几何语义：扭头只影响头，不改身体；
  D. 跟随逻辑是纯函数，可测：贴边收进屏幕内、待机回右下角、速度分档正确；
  E. 缺 Qt3D / 缺资产时必须能优雅回退（GIF 桌宠），不能把主程序拖死。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("numpy")
pytest.importorskip("PyQt6.Qt3DCore")

from zhuzhu_Copilot.ui.pet3d import rig as rig_mod                     # noqa: E402
from zhuzhu_Copilot.ui.pet3d import window as pet_window               # noqa: E402
from zhuzhu_Copilot.ui.pet3d.rig import PetRig, _axis_angle            # noqa: E402


@pytest.fixture(scope="module")
def rig():
    prefix = pet_window.asset_prefix()
    if prefix is None:
        pytest.skip("未烘焙 3D 资产（scripts/bake_pet3d.py）")
    return PetRig(prefix)


# ---------- A. 资产 ----------
def test_asset_has_bones_parts_and_frames(rig):
    assert len(rig.bone_names) == 22, "骨骼数量与 FBX 不一致"
    assert len(rig.parts) >= 15
    assert rig.frame_count > 60 and rig.fps > 0
    names = {p.name for p in rig.parts}
    assert {"kitty_body", "kitty_head", "kitty_tail_1"} <= names


def test_every_part_is_bound_to_a_sane_bone(rig):
    """这份 FBX 没有蒙皮权重，靠 PART_BONE 语义表绑骨头；绑错就会头动身子不动。"""
    expect = {"kitty_head": "Head", "kitty_tail_1": "Tail_1", "kitty_ear_l": "L_Ear",
              "kitty_arm_l": "L_Foreleg", "kitty_foot_r": "R_Hindleg"}
    for part_name, bone in expect.items():
        part = next(p for p in rig.parts if p.name == part_name)
        assert part.bones == [bone], f"{part_name} 绑到了 {part.bones}，应为 {bone}"
        assert part.rigid, f"{part_name} 是刚性块，应该只受一根骨头影响"


# ---------- B. 蒙皮 ----------
def test_pose_changes_over_time(rig):
    a, b = rig.pose(0.0), rig.pose(1.5)
    assert a.shape == (len(rig.bone_names), 4, 4)
    assert abs(a - b).max() > 0, "内置动画必须让骨骼矩阵随时间变化"


def test_extra_rotation_moves_vertices(rig):
    part = next(p for p in rig.parts if p.name == "kitty_nose")
    p0, n0 = rig.skin(part, rig.pose(0.0))
    p1, n1 = rig.skin(part, rig.pose(0.0, {"Head": _axis_angle("y", 30)}))
    assert p0.shape == p1.shape and p0.dtype.name == "float32"
    assert abs(p0 - p1).max() > 0.1, "扭头 30° 必须显著移动鼻子（否则就是没蒙皮）"
    assert abs(n0).max() <= 1.001 and abs(n1).max() <= 1.001, "法线必须归一"


def test_skinning_is_vectorized_and_finite(rig):
    mats = rig.pose(0.7)
    for part in rig.parts:
        pos, nrm = rig.skin(part, mats)
        assert len(pos) == len(part.pos) and len(nrm) == len(part.nrm)
        assert pos.min() > -10 and pos.max() < 10, "坐标跑飞说明矩阵链算错"


def test_world_shift_moves_part_exactly(rig):
    """世界空间平移：这份 FBX 的部件都建在骨头原点上，旋转只是原地打转，
    四肢/尾巴的"摆动"必须靠平移（否则肉眼看不见）。"""
    tail = next(p for p in rig.parts if p.name == "kitty_tail_3")
    body = next(p for p in rig.parts if p.name == "kitty_body")
    base = rig.pose(0.0)
    moved = rig.pose(0.0, None, {"Tail_3": (0.08, 0.0, 0.0)})
    c0 = rig.skin(tail, base)[0].mean(axis=0)
    c1 = rig.skin(tail, moved)[0].mean(axis=0)
    assert abs((c1 - c0)[0] - 0.08) < 1e-4, "平移量必须精确等于传入值"
    assert abs(rig.skin(body, base)[0] - rig.skin(body, moved)[0]).max() < 1e-6, \
        "平移尾巴不该带动身体"


# ---------- C. 扭头只动头 ----------
def test_head_rotation_does_not_move_body(rig):
    body = next(p for p in rig.parts if p.name == "kitty_body")
    base = rig.pose(0.0)
    turned = rig.pose(0.0, {"Head": _axis_angle("y", 30)})
    b0, _ = rig.skin(body, base)
    b1, _ = rig.skin(body, turned)
    assert abs(b0 - b1).max() < 1e-6, "扭头不该带动身体"


# ---------- D. 跟随逻辑（纯函数） ----------
def test_follow_target_tracks_cursor_and_clamps():
    from PyQt6.QtCore import QPoint, QRect, QSize
    screen = QRect(0, 0, 1920, 1080)
    size = QSize(300, 340)

    center = pet_window.follow_target(QPoint(960, 500), size, screen, idle=False)
    assert 0 <= center.x() <= 1920 - 300 and 0 <= center.y() <= 1080 - 340
    assert center.x() < 960 and center.y() < 500, "桌宠应站在光标左上方，不盖住光标"

    corner = pet_window.follow_target(QPoint(5, 5), size, screen, idle=False)
    assert corner.x() >= 4 and corner.y() >= 4, "贴左上角时必须收进屏幕内"

    idle = pet_window.follow_target(QPoint(5, 5), size, screen, idle=True)
    assert idle.x() > 1500 and idle.y() > 700, "待机时回右下角"


def test_motion_states_scale_up_with_speed():
    idle = pet_window.motion_for(0.0)
    walk = pet_window.motion_for(pet_window.SPEED_IDLE + 50)
    run = pet_window.motion_for(pet_window.SPEED_RUN + 200)
    assert idle["speed"] < walk["speed"] < run["speed"]
    assert idle["tail"] < walk["tail"] < run["tail"]
    assert run["leg"] > 0 and idle["leg"] == 0
    assert pet_window.motion_for(0, dragging=True)["lean"] < 0, "被抓起时向后仰"
    assert pet_window.motion_for(0, happy=True)["tail"] > run["tail"], "被摸时尾巴甩最凶"


# ---------- E. 回退 ----------
def test_create_pet_falls_back_to_gif(monkeypatch):
    """3D 不可用时必须回退到 GIF 桌宠，不能抛异常打断主程序启动。"""
    from zhuzhu_Copilot.ui import desktop_pet

    class _FakePet:
        pass

    monkeypatch.setattr(desktop_pet, "_pet_gif_path", lambda: r"C:\fake\pet.gif")
    monkeypatch.setattr(desktop_pet, "DesktopPet",
                        lambda path, open_panel_cb=None: _FakePet())
    # 让 pet3d.available() 返回 False
    import zhuzhu_Copilot.ui.pet3d as pkg
    monkeypatch.setitem(sys.modules, "zhuzhu_Copilot.ui.pet3d", pkg)
    monkeypatch.setattr(pkg, "available", lambda: False, raising=False)

    main = type("M", (), {"_open_agent_panel": None})()
    pet = desktop_pet._create_pet(main)
    assert isinstance(pet, _FakePet)


def test_transparent_flag_is_opt_in(monkeypatch):
    monkeypatch.delenv("ZHIZHU_PET3D_TRANSPARENT", raising=False)
    assert pet_window._want_transparent() is False, "默认是圆角卡片（Windows 上 GL 窗口不吃透明）"
    monkeypatch.setenv("ZHIZHU_PET3D_TRANSPARENT", "1")
    assert pet_window._want_transparent() is True
