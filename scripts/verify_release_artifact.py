# -*- coding: utf-8 -*-
r"""发布产物自检：确认打包出来的 exe 里「确实含有」关键模块。

背景（真实缺陷）：Cython/打包环节一旦漏掉某个 ui 子模块（如 zhuzhu_Copilot.ui.onboarding），
程序不会报错，只是对应功能静默失效 —— 曾导致「首次安装打开程序新手指南不弹」，
排查成本很高。此脚本在打包后立刻核对，把这类问题拦在发布之前。

做法：PyInstaller onedir 的 exe 内嵌 PYZ 归档，归档目录（TOC）里以明文保存模块全名，
因此直接在 exe 字节流中检索 `模块点分名` 即可判定该模块是否随包分发。

用法：
    python scripts/verify_release_artifact.py "dist\zhuzhu Copilot\zhuzhu Copilot.exe"
    python scripts/verify_release_artifact.py <exe> --require zhuzhu_Copilot.ui.onboarding
退出码：0 = 全部通过；1 = 有缺失（并打印缺失清单）。
"""
import argparse
import io
import os
import sys

# 默认必查模块：覆盖「漏打包即静默失效」的关键功能入口
DEFAULT_REQUIRED = [
    "zhuzhu_Copilot.ui.onboarding",       # 首次安装新手指南（本脚本的由来）
    "zhuzhu_Copilot.ui.agent_panel",      # 主面板
    "zhuzhu_Copilot.ui.main_window",      # Copilot 浮层宿主
    "zhuzhu_Copilot.core.agent_engine",   # Agent 引擎
    "zhuzhu_Copilot.core.agent_tools",    # 工具层
    "zhuzhu_Copilot.core.agent_skills",   # 技能
    "zhuzhu_Copilot.core.agent_llm",      # 模型接入
    "zhuzhu_Copilot.core.agent_ui_ux",    # UI/UX 包
    "zhuzhu_Copilot.core.agent_workflow", # 工作流
    "zhuzhu_Copilot.core.agent_team",     # 团队
    "zhuzhu_Copilot.core.security_engine.engine",   # 安全引擎
    "zhuzhu_Copilot.core.music_player",   # 音乐播放
    "zhuzhu_Copilot.core.lyrics_engine",  # 歌词引擎
    "zhuzhu_Copilot.office.docx_builder", # 办公三件套
    "zhuzhu_Copilot.office.pptx_builder",
    "zhuzhu_Copilot.office.xlsx_builder",
    "zhuzhu_Copilot.update_check",        # 自动更新（版本号所在模块）
]


def main() -> int:
    ap = argparse.ArgumentParser(description="发布产物关键模块自检")
    ap.add_argument("exe", help="待检查的 exe 路径")
    ap.add_argument("--require", default="", help="额外/替换的必查模块，逗号分隔")
    ap.add_argument("--quiet", action="store_true", help="仅输出结论")
    args = ap.parse_args()

    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        print("[FAIL] 找不到产物: %s" % exe)
        return 1

    with open(exe, "rb") as f:
        blob = f.read()
    size_mb = len(blob) / 1048576.0

    required = DEFAULT_REQUIRED
    if args.require.strip():
        required = [m.strip() for m in args.require.split(",") if m.strip()]

    if not args.quiet:
        print("产物: %s (%.1f MB)" % (exe, size_mb))

    missing = [m for m in required if m.encode("utf-8") not in blob]
    found = len(required) - len(missing)

    # PyInstaller 归档标志：确认这确实是打包产物而非裸 exe
    is_pyinstaller = b"MEI\x0c\x0b\x0a\x0b\x0e" in blob or b"PYZ\x00" in blob
    if not args.quiet:
        print("PyInstaller 归档标志: %s" % ("有" if is_pyinstaller else "无"))
        print("关键模块: %d/%d 命中" % (found, len(required)))
        if missing:
            for m in missing:
                print("   缺失 -> %s" % m)

    if missing:
        print("[FAIL] 产物缺少 %d 个关键模块：%s" % (len(missing), ", ".join(missing)))
        print("       请检查 build/zhuzhu_Copilot.spec 的 hiddenimports（"
              "ui 子模块由 *_ui_modules 自动枚举，见 scripts/generate_spec.py）。")
        return 1

    print("[OK] 产物自检通过：%d 个关键模块全部随包" % len(required))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
