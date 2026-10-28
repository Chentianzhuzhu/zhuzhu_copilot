# -*- coding: utf-8 -*-
"""3D 桌宠（kitty_pet.fbx 烘焙资产 + QOpenGLWidget 直渲）回归。

守护契约：
  A. 烘焙资产完整、可加载（骨骼 22 根 / 部件绑定正确的骨头）；
  B. 蒙皮真的会算：内置动画 + 附加旋转都要能改变顶点（否则桌宠是块石头）；
  C. 「跟随鼠标」的几何语义：扭头只影响头，不改身体；
  D. 跟随逻辑是纯函数，可测：贴边收进屏幕内、待机回右下角、速度分档正确；
  E. 缺 Qt3D / 缺资产时必须能优雅回退（GIF 桌宠），不能把主程序拖死；
  F. 窗口尺寸与真透明：尺寸单一来源（可配置）、透明帧缓冲必须带 alpha；
  G. 启动预加载：导入 + 资产解析在后台线程完成，幂等且结果可复用。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("numpy")
pytest.importorskip("PyQt6.QtOpenGLWidgets")

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
def test_pet_defaults_to_fixed_bottom_right_without_following():
    """默认不吸附鼠标：启动即固定在右下角（与 GIF 桌宠同一落点，留白 16px）。"""
    from PyQt6.QtCore import QPoint, QRect, QSize

    assert pet_window.FOLLOW_BY_DEFAULT is False, "默认不得吸附/追踪鼠标"
    assert pet_window.PET_CORNER_MARGIN == 16, "落点留白必须与 GIF 桌宠一致（16px）"

    screen = QRect(0, 0, 1920, 1040)
    size = QSize(112, 126)
    pos = pet_window.corner_pos(size, screen)
    assert pos.x() == screen.right() - size.width() - 16
    assert pos.y() == screen.bottom() - size.height() - 16
    # 待机回角落用的也是同一锚点（不另写一份魔法数字）
    idle = pet_window.follow_target(QPoint(5, 5), size, screen, idle=True)
    assert idle == pos


def test_look_weight_only_follows_when_cursor_is_near():
    """只有光标靠近桌宠（窗口边缘 70px 内）才跟随；远处权重 0 → 回到正视前方。"""
    from PyQt6.QtCore import QPoint, QRect

    rect = QRect(1791, 889, 112, 126)          # 右下角实际位置
    assert pet_window.look_weight(rect, QPoint(1847, 950)) == 1.0, "光标在猫身上必须完全跟随"
    assert pet_window.look_weight(rect, QPoint(1791 - 40, 950)) == 1.0, "贴着左侧 40px 也算靠近"
    assert pet_window.look_weight(rect, QPoint(1791 - 2000, 100)) == 0.0, "隔着半个屏幕不该转头"

    mid = pet_window.look_weight(rect, QPoint(1791 - 160, 889 - 160))
    assert 0.0 < mid < 1.0, "中间距离应有平滑过渡，而不是硬切换"
    assert pet_window.LOOK_NEAR_PX < pet_window.LOOK_FAR_PX


def test_look_angles_point_toward_cursor_direction():
    """鼠标在头顶要抬头、左上/右上要抬头看向对应角落（符号不能反）。

    方向校准依据（骨骼实测）：Head 绕 x 轴 +20° 会让鼻子 z 下降 0.184（低头），
    所以「上方」必须输出负 pitch；+yaw 表示朝屏幕右侧看。
    """
    from PyQt6.QtCore import QPoint

    center = QPoint(1847, 950)          # 右下角的桌宠中心
    above = pet_window.look_angles(center, QPoint(1847, 300))
    assert above[1] < -15, f"头顶正上方应明显抬头（负 pitch），实际 {above}"
    assert abs(above[0]) < 1.0, "正上方不应左右偏"

    upper_left = pet_window.look_angles(center, QPoint(600, 260))
    assert upper_left[0] < -20 and upper_left[1] < -15, f"左上应抬头向左看：{upper_left}"

    upper_right = pet_window.look_angles(center, QPoint(1920, 260))
    assert upper_right[0] > 0 and upper_right[1] < -15, f"右上应抬头向右看：{upper_right}"

    below = pet_window.look_angles(center, QPoint(1847, 1400))
    assert below[1] > 15, "鼠标在下方应低头（正 pitch）"

    # 上限饱和：再远也不超过常量，避免头拧 180°
    far = pet_window.look_angles(center, QPoint(-5000, -5000))
    assert far[0] == -pet_window.LOOK_YAW_MAX_DEG
    assert far[1] == -pet_window.LOOK_PITCH_MAX_DEG


def test_blink_every_three_seconds(rig):
    """眨眼：每 3 秒一次，且眼睛/高光部件都参与（闭眼不留空洞）。"""
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui.pet3d.view import Pet3DView

    app = QApplication.instance() or QApplication([])   # noqa: F841
    view = Pet3DView(rig, size=QSize(112, 126))
    try:
        names = set(view._blink_parts)
        assert {"kitty_eye_l", "kitty_eye_r",
                "kitty_glint_l", "kitty_glint_r"} <= names, names

        samples = []
        t = 0.0
        for _ in range(300):                 # 10 秒 @30fps
            view.advance(1 / 30.0)
            t += 1 / 30.0
            samples.append((t, view._blink))

        peaks = [tt for i, (tt, v) in enumerate(samples)
                 if v > 0.8 and 0 < i < len(samples) - 1
                 and v >= samples[i - 1][1] and v >= samples[i + 1][1]]
        assert len(peaks) >= 3, f"10 秒内至少眨 3 次，实际 {peaks}"
        assert 2.7 <= peaks[0] <= 3.15, f"启动后约 3 秒才第一次眨眼，实际 {peaks[0]:.2f}s"
        gaps = [b - a for a, b in zip(peaks, peaks[1:])]
        assert all(abs(g - 3.0) < 0.2 for g in gaps), f"眨眼间隔应为 3 秒：{gaps}"
        assert view._blink < 0.02, "大部分时间眼睛必须是睁开的"
    finally:
        view.deleteLater()


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
    """新渲染层（QOpenGLWidget 直渲）默认真透明；设 ZHIZHU_PET3D_TRANSPARENT=0
    可强制回退到旧的「圆角卡片」观感（排障/特殊需求用）。"""
    monkeypatch.delenv("ZHIZHU_PET3D_TRANSPARENT", raising=False)
    assert pet_window._want_transparent() is True, "新渲染层默认透明（DWM 合成可靠）"
    monkeypatch.setenv("ZHIZHU_PET3D_TRANSPARENT", "0")
    assert pet_window._want_transparent() is False
    monkeypatch.setenv("ZHIZHU_PET3D_TRANSPARENT", "off")
    assert pet_window._want_transparent() is False


# ---------- F. 尺寸与透明 ----------
def test_default_width_is_configurable_and_small(monkeypatch):
    """桌宠宽度单一来源：默认 112（真透明贴图下尺寸即猫大小），环境变量可覆盖。"""
    from zhuzhu_Copilot.ui.pet3d import view as pet_view

    monkeypatch.delenv("ZHIZHU_PET3D_WIDTH", raising=False)
    assert pet_view._default_width() == 112
    monkeypatch.setenv("ZHIZHU_PET3D_WIDTH", "160")
    assert pet_view._default_width() == 160
    monkeypatch.setenv("ZHIZHU_PET3D_WIDTH", "abc")
    assert pet_view._default_width() == 112, "非法值必须回落到默认"
    monkeypatch.setenv("ZHIZHU_PET3D_WIDTH", "10")
    assert pet_view._default_width() == 72, "过小值必须被下限夹住"


def test_window_height_follows_view_aspect():
    """窗口高度按渲染取景比例 300:340 等比，宽高比不允许各写一份。"""
    assert pet_window.PET_WIDTH == pet_window.DEFAULT_WIDTH, "宽度必须取 view 的单一来源"
    assert pet_window.PET_HEIGHT == int(pet_window.PET_WIDTH * 340 / 300)


def test_pet3d_can_be_disabled_by_env(monkeypatch):
    """ZHIZHU_PET3D=0 必须能关掉 3D 桌宠（排障 / 省 GPU），回退 GIF。"""
    from zhuzhu_Copilot.ui import desktop_pet

    monkeypatch.delenv("ZHIZHU_PET3D", raising=False)
    assert desktop_pet._pet3d_disabled() is False
    for v in ("0", "off", "false", "no"):
        monkeypatch.setenv("ZHIZHU_PET3D", v)
        assert desktop_pet._pet3d_disabled() is True


# ---------- G. 启动预加载 ----------
def test_preload_pet3d_loads_rig_once():
    """导入 + 资产解析在后台线程完成：完成后 _PRELOAD 就绪，且重复调用不再起线程。"""
    from zhuzhu_Copilot.ui import desktop_pet

    desktop_pet._PRELOAD.update(thread=None, prefix=None, rig=None,
                               done=False, ok=False)
    desktop_pet.preload_pet3d()
    loader = desktop_pet._PRELOAD["thread"]
    assert loader is not None, "预加载线程必须启动"
    desktop_pet.preload_pet3d()                     # 幂等
    assert desktop_pet._PRELOAD["thread"] is loader
    assert loader.wait(60_000), "预加载线程未结束"
    assert desktop_pet._PRELOAD["done"] and desktop_pet._PRELOAD["ok"]
    assert desktop_pet._PRELOAD["rig"] is not None
    assert desktop_pet._PRELOAD["prefix"] is not None


# ---------- H. 头/躯干跟随鼠标 ----------
def test_head_and_torso_follow_cursor_smoothly(rig):
    """视线目标要经低通后才参与蒙皮：头转「丝滑」且躯干跟一小部分。

    回归（用户要求）：头部与躯干随鼠标移动平滑转向；旧实现把每帧的目标角直接
    塞进 Head 骨骼 —— 30fps 下光标一动就是硬切，观感生硬。
    """
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication
    from zhuzhu_Copilot.ui.pet3d.view import Pet3DView, _TORSO_YAW_RATIO

    app = QApplication.instance() or QApplication([])   # noqa: F841  QWidget 需要实例
    view = Pet3DView(rig, size=QSize(112, 126))
    try:
        view.set_motion(look_yaw=30.0, look_pitch=12.0, speed=0.5)
        view.advance(1 / 30.0)
        # 低通：单帧之后只应走完一小部分，绝不能一步到位（否则就是硬切）
        assert 0.0 < view._look_yaw_s < 30.0
        assert 0.0 < view._look_pitch_s < 12.0
        for _ in range(90):                     # 3s 后应基本追上目标
            view.advance(1 / 30.0)
        assert abs(view._look_yaw_s - 30.0) < 0.5
        assert abs(view._look_pitch_s - 12.0) < 0.5
        # 躯干只跟一部分（否则整只猫会拧成麻花）
        assert abs(view._torso_yaw_s - 30.0 * _TORSO_YAW_RATIO) < 0.5
        # 躯干旋转必须真的进了蒙皮（Spine 是身体/肚皮/项圈的父骨骼）
        extra = view._extra_rot()
        assert "Spine" in extra and "Head" in extra
        # Head 里已扣掉躯干那份，两层叠加后的总朝向才等于目标角
        import numpy as np
        expect_head = (_axis_angle("x", view._look_pitch_s - view._torso_pitch_s)
                       @ _axis_angle("y", view._look_yaw_s - view._torso_yaw_s))
        assert np.allclose(extra["Head"], expect_head)
    finally:
        view.deleteLater()
