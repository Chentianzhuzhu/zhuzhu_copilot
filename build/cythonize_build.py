"""Cython 加固：将 winapp_migrator 包内所有模块就地编译为 .pyd（防反编译）。
就地编译后 PyInstaller 的 Analysis 会识别扩展模块并将其作为 binaries 收集，
对应源码不会进入 PYZ，运行时优先加载 .pyd。

注意：必须显式指定完整点分模块名（winapp_migrator.core.xxx），
否则 cythonize 会剥掉公共前缀 winapp_migrator/ 导致 .pyd 命名错误。
"""
import os
import sys
import glob
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # 禁止子进程写 __pycache__，避免沙箱拦截
sys.dont_write_bytecode = True
from setuptools import setup, Extension
from Cython.Build import cythonize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
os.chdir(SRC)

mods = [f.replace("\\", "/") for f in glob.glob("winapp_migrator/**/*.py", recursive=True)
        if not f.endswith("__init__.py")]  # 保留 __init__.py 支撑包结构
if not mods:
    raise SystemExit("未找到可编译模块")

# 完整点分模块名：winapp_migrator/core/agent_tts.py -> winapp_migrator.core.agent_tts
extensions = [
    Extension(name=f[:-3].replace("/", "."), sources=[f])
    for f in mods
]

print(f"[cythonize] 编译 {len(extensions)} 个模块 -> .pyd")
setup(
    name="zhuzhu_copilot_pyd",
    ext_modules=cythonize(
        extensions,
        compiler_directives={"language_level": 3},
        quiet=True,
    ),
    script_args=["build_ext", "--inplace"],
)
print("[cythonize] 编译完成")
