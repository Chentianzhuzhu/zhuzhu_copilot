# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 确保 spec 顶部 collect_submodules('zhuzhu_Copilot.core') 能发现子模块：
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
# 延迟导入，未声明的子模块会被整包漏掉（曾导致某个 ui 子模块漏打包、
# 对应功能静默不生效）。
import glob as _glob
_ui_modules = [
    'zhuzhu_Copilot.ui.' + _os.path.splitext(_os.path.basename(_p))[0]
    for _p in sorted(_glob.glob(_os.path.join(_src, 'zhuzhu_Copilot', 'ui', '*.py')))
    if not _os.path.basename(_p).startswith('__')
]

# 沙盒运行时（Node/Python）打包进安装目录：由 scripts/prepare_runtime_bundle.py 预下载。
# 目录不存在时跳过（如源码调试直跑），避免构建硬失败。
# 注意：必须用 list 类型承载元组，避免星号展开/拼接时把裸字符串混入 datas。
_runtime_datas = [(_os.path.join(_root, 'build/runtime_bundle/runtime'), 'runtime')]
if not _os.path.isdir(_os.path.join(_root, 'build/runtime_bundle/runtime')):
    _runtime_datas = []
# d3dcompiler_47.dll：精简 Win10/11 常被裁剪，Qt D3D11 RHI 编译 shader 必需。
# 随应用目录分发（Windows DLL 搜索顺序：exe 目录优先于 System32），缺系统组件也能启动。
# 由 build_release.ps1 / build_setup.bat 从本机 System32 复制到 build/redist/。
_d3d_path = _os.path.join(_root, 'build/redist/d3dcompiler_47.dll')
_d3d_datas = [(_d3d_path, '.')] if _os.path.isfile(_d3d_path) else []
# 现成工作流种子：由 scripts/prepare_workflow_seed.py 在构建期快照用户目录全部
# 实例化工作流（含自定义）+ 团队配置。新机安装后 agent_workflow 首启自动补齐缺失
# 工作流到用户目录，做到「安装完成即 @ 调用」。目录不存在时跳过（源码直跑无种子）。
_seed_dir = _os.path.join(_root, 'build/workflows_seed')
_seed_datas = [(_seed_dir, 'workflows_seed')] if _os.path.isdir(_seed_dir) else []
# 现成技能 / Agent 插件种子：由 scripts/prepare_assets_seed.py 在构建期把「仓库内置 +
# 用户目录现有」合并快照到 build/assets_seed/，安装后运行时首启同步到用户目录，
# 做到「装完即拥有现有全部技能与插件」。种子已含仓库内置资源，存在时替代内置目录
# （避免同一份资源进包两次）；缺失时回退内置，未跑准备脚本也能构建。
_assets_seed = _os.path.join(_root, 'build/assets_seed')
_skills_datas = ([(_os.path.join(_assets_seed, 'skills'), 'skills_seed')]
                 if _os.path.isdir(_os.path.join(_assets_seed, 'skills'))
                 else [(_os.path.join(_root, 'src/zhuzhu_Copilot/skills'), 'skills')])
_plugins_datas = ([(_os.path.join(_assets_seed, 'plugins'), 'plugins_seed')]
                  if _os.path.isdir(_os.path.join(_assets_seed, 'plugins'))
                  else [(_os.path.join(_root, 'src/zhuzhu_Copilot/plugins'), 'plugins')])
# Copilot 插件**不打包**：它默认必须为空列表，由用户自行导入/生成，
# 避免把开发机上的插件带进安装包。

a = Analysis(
    [_os.path.join(_root, 'src/main.py')],
    pathex=[_src],
    binaries=[],
    datas=[(_os.path.join(_root, 'assets'), 'assets'),
           # 语言包（i18n）：不打包则英文界面在发布版里全部回退成中文。
           # core/i18n.py 的 _locales_dir() 会同时探测 <root>/locales 与
           # _MEIPASS/locales，故按 skills/plugins 同款平铺方式注入即可。
           (_os.path.join(_root, 'src/zhuzhu_Copilot/locales'), 'locales'),
           (_os.path.join(_root, 'src/zhuzhu_Copilot/core/workflow_templates'), 'workflow_templates'),
           # 安全引擎数据文件（config/security.json、security_rules.json、yara/ 规则库）：
           # 引擎在打包态按 _MEIPASS/config 解析，缺失则回退内置默认导致规则库/特征库不生效
           (_os.path.join(_root, 'config'), 'config')]
           + _skills_datas + _plugins_datas
           + _runtime_datas + _d3d_datas + _seed_datas,
    hiddenimports=[
        # agent_tools 通过 importlib 动态导入 core 子模块（agent_find/agent_tts/
        # agent_subagent/agent_deps 等），PyInstaller 静态分析发现不了，
        # 必须全量收集 core 包，否则打包后运行时报 ModuleNotFoundError（如 search_files
        # 报 No module named 'zhuzhu_Copilot.core.agent_find'）。
        *collect_submodules('zhuzhu_Copilot.core'),
        'zhuzhu_Copilot.core.app_scanner',
        'zhuzhu_Copilot.core.agent_workflow',
        'zhuzhu_Copilot.core.migration',
        'zhuzhu_Copilot.core.registry',
        'zhuzhu_Copilot.core.uwp',
        'zhuzhu_Copilot.core.orchestrator',
        'zhuzhu_Copilot.core.permissions',
        'zhuzhu_Copilot.core.data_dirs',
        'zhuzhu_Copilot.core.shortcut',
        'zhuzhu_Copilot.core.uninstaller',
        'zhuzhu_Copilot.core.memory_optimizer',
        'zhuzhu_Copilot.core.security',
        'zhuzhu_Copilot.core.network_defense',
        'zhuzhu_Copilot.core.execution_guard',
        'zhuzhu_Copilot.ui.main_window',
        'zhuzhu_Copilot.ui.styles',
        'zhuzhu_Copilot.ui.widgets',
        'zhuzhu_Copilot.ui.agent_panel',
        # ui 包子模块全量声明：Cython 加固后 ui/*.py 会编译成 .pyd，PyInstaller 无法分析
        # 二进制模块内部的（函数内）延迟导入 —— 未在此声明的 ui 子模块会被整包漏掉，
        # 表现为「功能静默失效」（仅打印一行异常）。这里按包内实际 *.py 文件自动枚举，
        # 新增 ui 子模块无需改 spec。
        *_ui_modules,
        'zhuzhu_Copilot.core.agent_llm',
        'zhuzhu_Copilot.core.agent_screen',
        'zhuzhu_Copilot.core.agent_sandbox',
        'zhuzhu_Copilot.core.agent_tools',
        # office 办公三件套高质量生成包（execute_tool 函数内延迟导入，显式声明防遗漏）
        'zhuzhu_Copilot.office',
        'zhuzhu_Copilot.office.theme',
        'zhuzhu_Copilot.office.utils',
        'zhuzhu_Copilot.office.docx_builder',
        'zhuzhu_Copilot.office.pptx_builder',
        'zhuzhu_Copilot.office.xlsx_builder',
        'zhuzhu_Copilot.office.beautify',
        'zhuzhu_Copilot.core.agent_browser',
        'zhuzhu_Copilot.core.agent_mcp',
        'zhuzhu_Copilot.core.agent_skills',
        'zhuzhu_Copilot.core.agent_engine',
        'zhuzhu_Copilot.core.agent_tts',
        'zhuzhu_Copilot.core.agent_plugins',
        # 设置页音乐播放（pygame.mixer 单例）：agent_panel 函数内动态导入，显式声明防打包遗漏
        'zhuzhu_Copilot.core.music_player',
        # 歌词引擎与桌面歌词（音乐页/主面板函数内动态导入，显式声明防打包遗漏）
        'zhuzhu_Copilot.core.lyrics_engine',
        'zhuzhu_Copilot.ui.lyrics_view',
        'zhuzhu_Copilot.ui.desktop_lyrics',
        'zhuzhu_Copilot.utils.helpers',
        # TTS 自动朗读播放器（函数内动态 import pygame，显式声明防打包遗漏）
        'pygame',
        # 语音输入（core/agent_asr.py 函数内动态 import websockets.sync.client）：
        # 账号系统的 WS 用的是异步 websockets，同步子模块不会被其牵连收集，
        # 漏声明会表现为「语音输入静默失效（ImportError 被兜住）」，显式声明防遗漏。
        'websockets.sync.client',
        'websockets.sync',
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
        # 顶栏网络状态用 QNetworkInformation（Windows 网络列表管理器）判断系统可达性：
        # 在 ui/agent_panel.py 函数内导入，且该模块会被 Cython 编译为 .pyd，
        # PyInstaller 无法分析其内部导入，必须显式声明，否则打包后静默退化为纯探测。
        'PyQt6.QtNetwork',
        # win32com 为动态包，需完整收集子模块（快捷方式 TargetPath 读取依赖）
        *collect_submodules('win32com'),
        # 三件套图片依赖：docx/pptx/openpyxl 的 add_picture/add_image 运行时 import PIL，显式声明确保随包
        'PIL',
        # 3D 桌宠（ui/pet3d）：QOpenGLWidget 直渲 + numpy。pet3d 是 PEP 562 懒加载包，
        # PyInstaller 静态分析看不到 `from . import window` 里的 QtOpenGL 导入，必须显式声明，
        # 否则打包版会静默回退成 GIF 桌宠（用户侧就是「怎么还是那只 gif」）。
        'PyQt6.QtOpenGLWidgets',
        'PyQt6.QtOpenGL',
        'numpy',
        # pet3d 的类是通过模块级 __getattr__ 暴露的（PEP 562 懒加载），
        # 静态分析拿不到 `from ... import Pet3DWindow` 的落点，四个模块显式声明。
        'zhuzhu_Copilot.ui.pet3d',
        'zhuzhu_Copilot.ui.pet3d.rig',
        'zhuzhu_Copilot.ui.pet3d.view',
        'zhuzhu_Copilot.ui.pet3d.window',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[_os.path.join(_root, 'scripts/rthook_qt_dll_path.py')],
    # 运行环境性能优化：排除确定未使用的重型 Qt 模块与内置库，减小体积、加快启动。
    # QtWebEngine 整体移除（约 180MB）：PPT 全屏放映、Office 保真预览、网页预览
    # 均已内置降级分支（QTextBrowser/静态渲染），剔除后包体显著缩小。
    excludes=[
        'tkinter', 'PyQt6.QtQuick', 'PyQt6.QtQml', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender',
        'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.QtPdf',
        'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtSerialPort',
        'PyQt6.QtWebSockets', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
        'PyQt6.QtSql', 'PyQt6.QtXml', 'PyQt6.QtTest', 'PyQt6.QtDBus',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineQuick',
        'PyQt6.QtWebChannel', 'PyQt6.QtPositioning',
        # numpy 不能进 excludes：3D 桌宠（ui/pet3d）依赖它，且在 hiddenimports 里显式声明；
        # 同时列入 excludes 会让 PyInstaller 把它剔除 → 打包版静默回退 GIF 桌宠。
        'pandas', 'scipy', 'matplotlib', 'sklearn', 'torch',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 性能优化：应用未使用 QTranslator，剔除全部 Qt 翻译文件(.qm)，
# 减小体积、加快安装解压与目录扫描
a.datas = [d for d in a.datas if not d[0].endswith('.qm')]

# QtWebEngine 二次剔除：PyQt6 的 hook 会整目录收集 Qt6/bin 下的 Qt DLL，
# excludes 只对 Python 模块生效，拦不住已收集的二进制（实测残留
# Qt6WebEngineCore.dll 等约 390MB）。这里按文件名统一过滤 binaries + datas，
# 同时清掉沙盒运行时（runtime_bundle）里可能混入的 WebEngine 副本。
import re as _re
_QTWEB = _re.compile(r'webengine', _re.IGNORECASE)
a.binaries = [b for b in a.binaries
              if not _QTWEB.search(_os.path.basename(b[0]))]
a.datas = [d for d in a.datas
           if not _QTWEB.search(d[0].replace('\\', '/'))]

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
