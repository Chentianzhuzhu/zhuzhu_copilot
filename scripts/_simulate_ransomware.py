"""模拟勒索软件加密（测试用，完全可还原）

用途：模拟勒索软件「批量加密文件 + 改扩展名」行为，用于验证蜜罐勒索防护
（ransomware_guard）能检测到：诱饵文件篡改 + 勒索加密后缀爆发。

安全说明：
- 仅加密 --dir 指定目录内的普通文件，默认在项目根创建 _ransomware_test/ 测试目录
- XOR 加密完全可逆，restore 命令按同一密钥还原全部文件
- 内置目录保护：拒绝加密系统盘根 / Windows / 用户主目录 / Program Files 等危险位置

用法：
  python scripts/_simulate_ransomware.py                 # 生成测试文件并加密
  python scripts/_simulate_ransomware.py restore         # 还原
  python scripts/_simulate_ransomware.py --dir DIR       # 加密指定目录
  python scripts/_simulate_ransomware.py --dir DIR restore
"""
import argparse
import sys
from pathlib import Path

_XOR_KEY = 0x5A                 # XOR 密钥（固定，保证可还原）
_RANSOM_EXT = ".locked"         # 模拟勒索扩展名（命中蜜罐勒索扩展名库）
_DEFAULT_DIR = Path(__file__).resolve().parents[1] / "_ransomware_test"

# 危险位置：拒绝加密，防止误伤真实数据
_FORBIDDEN_PARTS = {"windows", "system32", "syswow64", "program files", "program files (x86)"}
_FORBIDDEN_ROOTS = {"c:\\", "c:/", "d:\\", "d:/"}


def _is_forbidden(root: Path) -> bool:
    r = str(root.resolve()).lower()
    if r in _FORBIDDEN_ROOTS or r == str(Path.home()).lower():
        return True
    return bool({p.lower() for p in root.parts} & _FORBIDDEN_PARTS)


def _make_test_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    samples = {
        "report.docx": "季度经营报告内容……",
        "photo.jpg": "JPEG 二进制占位数据",
        "data.csv": "id,name\n1,alice\n2,bob\n3,carol",
        "notes.txt": "重要备忘笔记内容",
    }
    for name, content in samples.items():
        (root / name).write_text(content, encoding="utf-8")


def _xor_file(path: Path, key: int) -> None:
    path.write_bytes(bytes(b ^ key for b in path.read_bytes()))


def encrypt(root: Path) -> int:
    """加密目录内所有普通文件：XOR + 追加 .locked 后缀"""
    _make_test_files(root)
    count = 0
    for f in root.rglob("*"):
        if f.is_file() and f.suffix != _RANSOM_EXT:
            _xor_file(f, _XOR_KEY)
            f.rename(f.with_name(f.name + _RANSOM_EXT))
            count += 1
    return count


def restore(root: Path) -> int:
    """还原：对 .locked 文件 XOR 解密并去掉后缀"""
    count = 0
    for f in root.rglob("*"):
        if f.is_file() and f.suffix == _RANSOM_EXT:
            _xor_file(f, _XOR_KEY)
            f.rename(f.with_name(f.name[:-len(_RANSOM_EXT)]))
            count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="模拟勒索加密（可还原）")
    ap.add_argument("action", nargs="?", default="encrypt", choices=["encrypt", "restore"])
    ap.add_argument("--dir", default=str(_DEFAULT_DIR), help="目标目录")
    args = ap.parse_args()

    root = Path(args.dir)
    if _is_forbidden(root):
        print("已阻止：目标目录位于受保护位置，拒绝执行。请指定安全测试目录。")
        return 1

    if args.action == "encrypt":
        n = encrypt(root)
        print(f"已加密 {n} 个文件（XOR + {_RANSOM_EXT} 后缀）→ {root}")
        print("运行 restore 可完整还原。")
    else:
        n = restore(root)
        print(f"已还原 {n} 个文件 → {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
