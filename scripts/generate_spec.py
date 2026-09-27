"""构建入口：按当前仓库配置实时生成 build/WinAppMigrator.spec。

背景：编辑工具与外部进程（IDE/监视器）对该文件的并发写回会把 spec 覆盖成
中间状态（曾两次导致打包失败：_d3d_datas 未定义 / datas 元组被拆坏）。
为保证「写入的内容 = PyInstaller 读取的内容」，打包脚本在编译前调用本文件
重新生成 spec（单一事实来源，写入与构建之间无竞争窗口）。

用法：
    python scripts/generate_spec.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SPEC_TEXT = r"""# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 确保 spec 顶部 collect_submodules('winapp_migrator.core') 能发现子模块：
# PyInstaller 加载 spec 时 cwd 为项目根，sys.path 默认不含 ../src，
# 若不显式加入，collect_submodules 会因导入失败返回空列表，导致 agent_find 等
# 动态导入模块仍漏打包（运行时报 ModuleNotFoundError）。
import os as _os
import sys as _sys
# 注意：PyInstaller 执行 spec 时命名空间不提供 __file__，需用注入的 SPECPATH
# （spec 所在目录，即 build/）定位项目根。所有路径统一基于 SPECPATH 的绝对路径，
# 避免依赖执行时 cwd 造成的相对路径解析漂移（曾导致 runtime/d3d 等 datas 丢失）。
_spec_dir = _os.path.abspath(SPECPATH)
_root = _os.path.abspath(_os.path.join(_spec_dir, '..'))
_src = _os.path.join(_root, 'src')
if _src not in _sys.path:
    _sys.path.insert(0, _src)

# ui 包子模块清单：按包内实际 *.py 文件枚举（不依赖逐个手写，新增子模块自动纳入）。
# 原因见 hiddenimports：ui/*.py 若被 Cython 编译为 .pyd，PyInstaller 无法分析其内部
# 延迟导入，未声明的子模块会被整包漏掉（曾导致 ui.onboarding 漏打包：
# 首次安装打开程序新手指南静默不弹）。
import glob as _glob
_ui_modules = [
    'winapp_migrator.ui.' + _os.path.splitext(_os.path.basename(_p))[0]
    for _p in sorted(_glob.glob(_os.path.join(_src, 'winapp_migrator', 'ui', '*.py')))
    if not _os.path.basename(_p).startswith('__')
]

# 沙盒运行时（Node/Python）打包进安装目录：由 scripts/prepare_runtime_bundle.py 预下载。
# 目录不存在时跳过（如源码调试直跑），避免构建硬失败。
# 注意：必须用 list 类型承载元组，避免星号展开/拼接时把裸字符串混入 datas。
_runtime_datas = [(_os.path.join(_root, 'build/runtime_bundle/runtime'), 'runtime')]
if not _os.path.isdir(_os.path.join(_root, 'build/runtime_bundle/runtime')):
    _runtime_datas = []
# d3dcompiler_47.dll：精简 Win10/11 常被裁剪，Chromium/QtWebEngine 编译 shader 必需。
# 随应用目录分发（Windows DLL 搜索顺序：exe 目录优先于 System32），缺系统组件也能启动。
# 由 build_sign.ps1 / build_setup.bat 从本机 System32 复制到 build/redist/。
_d3d_path = _os.path.join(_root, 'build/redist/d3dcompiler_47.dll')
_d3d_datas = [(_d3d_path, '.')] if _os.path.isfile(_d3d_path) else []
# 现成工作流种子：由 scripts/prepare_workflow_seed.py 在构建期快照用户目录全部
# 实例化工作流（含自定义）+ 团队配置。新机安装后 agent_workflow 首启自动补齐缺失
# 工作流到用户目录，做到「安装完成即 @ 调用」。目录不存在时跳过（源码直跑无种子）。
_seed_dir = _os.path.join(_root, 'build/workflows_seed')
_seed_datas = [(_seed_dir, 'workflows_seed')] if _os.path.isdir(_seed_dir) else []

a = Analysis(
    [_os.path.join(_root, 'src/main.py')],
    pathex=[_src],
    binaries=[],
    datas=[(_os.path.join(_root, 'assets'), 'assets'),
           (_os.path.join(_root, 'src/winapp_migrator/skills'), 'skills'),
           (_os.path.join(_root, 'src/winapp_migrator/plugins'), 'plugins'),
           (_os.path.join(_root, 'src/winapp_migrator/core/workflow_templates'), 'workflow_templates'),
           # 安全引擎数据文件（config/security.json、security_rules.json、yara/ 规则库）：
           # 引擎在打包态按 _MEIPASS/config 解析，缺失则回退内置默认导致规则库/特征库不生效
           (_os.path.join(_root, 'config'), 'config')]
           + _runtime_datas + _d3d_datas + _seed_datas,
    hiddenimports=[
        # agent_tools 通过 importlib 动态导入 core 子模块（agent_find/agent_tts/
        # agent_subagent/agent_panels/agent_deps 等），PyInstaller 静态分析发现不了，
        # 必须全量收集 core 包，否则打包后运行时报 ModuleNotFoundError（如 search_files
        # 报 No module named 'winapp_migrator.core.agent_find'）。
        *collect_submodules('winapp_migrator.core'),
        'winapp_migrator.core.app_scanner',
        'winapp_migrator.core.agent_workflow',
        'winapp_migrator.core.migration',
        'winapp_migrator.core.registry',
        'winapp_migrator.core.uwp',
        'winapp_migrator.core.orchestrator',
        'winapp_migrator.core.permissions',
        'winapp_migrator.core.data_dirs',
        'winapp_migrator.core.shortcut',
        'winapp_migrator.core.uninstaller',
        'winapp_migrator.core.memory_optimizer',
        'winapp_migrator.core.security',
        'winapp_migrator.core.network_defense',
        'winapp_migrator.core.execution_guard',
        'winapp_migrator.ui.main_window',
        'winapp_migrator.ui.styles',
        'winapp_migrator.ui.widgets',
        'winapp_migrator.ui.agent_panel',
        # ui 包子模块全量声明：Cython 加固后 ui/*.py 会编译成 .pyd，PyInstaller 无法分析
        # 二进制模块内部的（函数内）延迟导入 —— 未在此声明的 ui 子模块会被整包漏掉，
        # 表现为「功能静默失效」（如漏掉 ui.onboarding → 首次安装打开程序新手指南不弹，
        # 仅打印一行异常）。这里按包内实际 *.py 文件自动枚举，新增 ui 子模块无需改 spec。
        *_ui_modules,
        'winapp_migrator.core.agent_llm',
        'winapp_migrator.core.agent_screen',
        'winapp_migrator.core.agent_sandbox',
        'winapp_migrator.core.agent_tools',
        # office 办公三件套高质量生成包（execute_tool 函数内延迟导入，显式声明防遗漏）
        'winapp_migrator.office',
        'winapp_migrator.office.theme',
        'winapp_migrator.office.utils',
        'winapp_migrator.office.docx_builder',
        'winapp_migrator.office.pptx_builder',
        'winapp_migrator.office.xlsx_builder',
        'winapp_migrator.office.beautify',
        'winapp_migrator.core.agent_browser',
        'winapp_migrator.core.agent_mcp',
        'winapp_migrator.core.agent_skills',
        'winapp_migrator.core.agent_engine',
        'winapp_migrator.core.agent_tts',
        'winapp_migrator.core.agent_plugins',
        # 设置页音乐播放（pygame.mixer 单例）：agent_panel 函数内动态导入，显式声明防打包遗漏
        'winapp_migrator.core.music_player',
        # 歌词引擎与桌面歌词（音乐页/主面板函数内动态导入，显式声明防打包遗漏）
        'winapp_migrator.core.lyrics_engine',
        'winapp_migrator.ui.lyrics_view',
        'winapp_migrator.ui.desktop_lyrics',
        'winapp_migrator.utils.helpers',
        # TTS 自动朗读播放器（函数内动态 import pygame，显式声明防打包遗漏）
        'pygame',
        # 内置浏览器（CodePreviewWindow/AI browser 工具）依赖 QtWebEngine：
        # 取消 excludes 排除后会经 PyInstaller 官方 hook 自动收集 QtWebEngineProcess、
        # 资源包(resources.pak/locales)与相关 Qt 模块，这里显式声明防遗漏。
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets',
        # 预览面板媒体播放（视频/音乐）：QMediaPlayer + QVideoWidget，
        # 需显式收集 QtMultimedia 模块及其 ffmpeg/windows 解码后端插件
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        'pywintypes',
        'win32api',
        'win32gui',
        'win32security',
        'win32con',
        # agent_panel 的 _svg_icon 渲染 Lucide SVG 矢量图标依赖 QtSvg
        'PyQt6.QtSvg',
        # win32com 为动态包，需完整收集子模块（快捷方式 TargetPath 读取依赖）
        *collect_submodules('win32com'),
        # 三件套图片依赖：docx/pptx/openpyxl 的 add_picture/add_image 运行时 import PIL，显式声明确保随包
        'PIL',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[_os.path.join(_root, 'scripts/rthook_qt_dll_path.py')],
    # 运行环境性能优化：排除确定未使用的重型 Qt 模块与内置库，减小体积、加快启动。
    # QtWebEngineCore/Widgets 不再排除——内置浏览器/CodePreviewWindow 依赖它（详见 hiddenimports）
    excludes=[
        'tkinter', 'PyQt6.QtQuick', 'PyQt6.QtQml', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender',
        'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.QtPdf',
        'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtSerialPort',
        'PyQt6.QtWebSockets', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
        'PyQt6.QtSql', 'PyQt6.QtXml', 'PyQt6.QtTest', 'PyQt6.QtDBus',
        'numpy', 'pandas', 'scipy', 'matplotlib', 'sklearn', 'torch',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 性能优化：应用未使用 QTranslator，剔除全部 Qt 翻译文件(.qm)，
# 减小体积、加快安装解压与目录扫描
a.datas = [d for d in a.datas if not d[0].endswith('.qm')]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='zhuzhu Copilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_os.path.join(_root, 'assets/icon.ico'),
    manifest=_os.path.join(_root, 'assets/admin.manifest'),
    version=_os.path.join(_spec_dir, 'version_info.txt'),
    uac_admin=True,
    # 去 _internal 本体层：所有运行文件与主程序同层（根目录即程序本体）。
    # 代码侧路径解析均兼容（_MEIPASS→exe同级 依次尝试），见 main.py / agent_skills 等
    contents_directory='.',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    contents_directory='.',
    name='zhuzhu Copilot',
)
"""


def main():
    dest = os.path.join(ROOT, "build", "WinAppMigrator.spec")
    import io
    import ast
    ast.parse(SPEC_TEXT)  # 生成前自检语法
    with io.open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(SPEC_TEXT)
    print(f"regenerated {dest}")


if __name__ == "__main__":
    main()