"""验证：QtWebEngineWidgets.pyd 在无 _internal 布局下因 Qt6WebEngineWidgets.dll
不在 exe 同层而加载失败；加入 PyQt6/Qt6/bin 到 DLL 搜索后可加载。"""
import os, sys, ctypes

root = os.path.abspath(r"dist\zhuzhu Copilot")
pyd = os.path.join(root, "PyQt6", "QtWebEngineWidgets.pyd")
qt_bin = os.path.join(root, "PyQt6", "Qt6", "bin")

# 复现 PyInstaller 现状：PyQt6.find_qt 只 add_dll_directory(exe 目录)
os.add_dll_directory(root)
# 兼容测试机 python DLL 搜索
os.add_dll_directory(os.path.dirname(sys.executable))

def try_load(tag):
    try:
        ctypes.WinDLL(pyd)
        print(f"{tag}: 加载成功")
        return True
    except OSError as e:
        print(f"{tag}: 加载失败 -> {e}")
        return False

ok_before = try_load("现状(exe同层)")
if os.path.isdir(qt_bin):
    os.add_dll_directory(qt_bin)
ok_after = try_load("修复后(+Qt6/bin)")

print("---")
print("Qt6WebEngineWidgets.dll 在 exe 同层:",
      os.path.exists(os.path.join(root, "Qt6WebEngineWidgets.dll")))
print("Qt6WebEngineWidgets.dll 在 Qt6/bin:",
      os.path.exists(os.path.join(qt_bin, "Qt6WebEngineWidgets.dll")))
if ok_before is False and ok_after is True:
    print("RESULT: 理论确认，runtime hook 可修复")
else:
    print("RESULT: 理论未复现，需进一步排查")
