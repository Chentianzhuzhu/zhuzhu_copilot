"""PyInstaller runtime hook：把 PyQt6/Qt6/bin 加入 Windows DLL 搜索目录。

背景：无 _internal 布局下 PyInstaller 会把大部分 Qt6*.dll 复制到 exe 同层，
但部分 Qt DLL（如 Qt6WebEngineWidgets.dll）仍只在 PyQt6/Qt6/bin 下。
PyQt6 的 find_qt() 仅 add_dll_directory(exe 目录)，导致 QtWebEngineWidgets.pyd
加载时找不到 Qt6WebEngineWidgets.dll → Web 引擎不可用。此 hook 在 PyQt6
导入前把 Qt6/bin 显式加入 DLL 搜索目录，对所有 Qt 模块（含 QtMultimedia 等）
同样生效。在解释器启动（PYZ 加载后、用户代码前）执行。
"""


def _pyi_rthook():
    import os
    import sys
    if sys.platform == 'win32':
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(sys.executable)
        qt_bin = os.path.join(base, 'PyQt6', 'Qt6', 'bin')
        if os.path.isdir(qt_bin):
            try:
                os.add_dll_directory(qt_bin)
            except AttributeError:
                os.environ['PATH'] = qt_bin + os.pathsep + os.environ.get('PATH', '')


_pyi_rthook()
del _pyi_rthook
