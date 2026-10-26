"""Qt 3D 桌宠（kitty_pet.fbx 烘焙资产 + 程序化交互动画）。

模块是懒加载的：``from zhuzhu_Copilot.ui.pet3d import Pet3DWindow`` 时才去 import
PyQt6.Qt3D —— 没装 Qt3D 的环境导入本包不会炸，调用方（desktop_pet）可以安全降级到 GIF 桌宠。
"""
from __future__ import annotations

__all__ = ["Pet3DWindow", "PetRig", "available", "asset_prefix"]


def __getattr__(name):  # PEP 562：按需导入，避免缺 Qt3D 时整包不可用
    if name in ("Pet3DWindow", "available", "asset_prefix"):
        from . import window
        return getattr(window, name)
    if name == "PetRig":
        from .rig import PetRig
        return PetRig
    raise AttributeError(name)
