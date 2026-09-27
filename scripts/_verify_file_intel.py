# scripts/_verify_file_intel.py
import os, sys, struct, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def make_test_pe(path):
    """手工构造最小 PE32：.text + .idata 两节，导入 KERNEL32.dll!VirtualAlloc（真实 PE 结构）"""
    dll, func = b"KERNEL32.dll", b"VirtualAlloc"
    raw = bytearray(0x600)
    raw[0:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", raw, 0x84, 0x14C, 2, 0, 0, 0, 0xE0, 0x0102)
    opt = 0x98
    struct.pack_into("<H", raw, opt, 0x10B)
    struct.pack_into("<I", raw, opt + 16, 0x1000)   # AddressOfEntryPoint
    struct.pack_into("<I", raw, opt + 20, 0x1000)   # BaseOfCode
    struct.pack_into("<I", raw, opt + 28, 0x400000)  # ImageBase
    struct.pack_into("<I", raw, opt + 32, 0x1000)   # SectionAlignment
    struct.pack_into("<I", raw, opt + 36, 0x200)    # FileAlignment
    struct.pack_into("<I", raw, opt + 56, 0x3000)   # SizeOfImage
    struct.pack_into("<I", raw, opt + 60, 0x200)    # SizeOfHeaders
    struct.pack_into("<H", raw, opt + 68, 3)        # Subsystem
    struct.pack_into("<I", raw, opt + 92, 16)       # NumberOfRvaAndSizes
    struct.pack_into("<II", raw, opt + 96 + 8, 0x2000, 40)  # Import 目录
    sec0, sec1 = 0x178, 0x178 + 40
    raw[sec0:sec0 + 8] = b".text\0\0\0"
    struct.pack_into("<IIIIIIHHI", raw, sec0 + 8, 0x1000, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    raw[sec1:sec1 + 8] = b".idata\0\0"
    struct.pack_into("<IIIIIIHHI", raw, sec1 + 8, 0x1000, 0x2000, 0x200, 0x400, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<IIIII", raw, 0x400, 0x2060, 0, 0, 0x2040, 0x2060)  # 导入描述符
    raw[0x440:0x440 + len(dll)] = dll                       # RVA 0x2040 dll 名
    struct.pack_into("<H", raw, 0x450, 0)                   # hint
    raw[0x452:0x452 + len(func)] = func                     # 函数名
    struct.pack_into("<II", raw, 0x460, 0x2050, 0)          # thunk 表 → 0x2050, 终止 0
    with open(path, "wb") as f:
        f.write(raw)


from winapp_migrator.core.security_engine import file_intel

tmp = tempfile.mkdtemp(prefix="fileintel_")
try:
    pe = os.path.join(tmp, "sample.exe")
    make_test_pe(pe)
    s = file_intel.analyze_file(pe)
    check("PE识别", s["pe"] is True)
    check("节区解析", [x["name"] for x in s["sections"]] == [".text", ".idata"], str(s["sections"]))
    check("导入表解析", "kernel32.dll!virtualalloc" in s["imports"], str(s["imports"][:5]))
    check("SHA256稳定且64位", len(s["sha256"]) == 64 and file_intel.sha256_of(pe) == s["sha256"])
    rnd = os.path.join(tmp, "rand.bin")
    with open(rnd, "wb") as f:
        f.write(os.urandom(64 * 1024))
    check("测试构造文件熵>7", file_intel.analyze_file(rnd)["entropy"] > 7.0)
    check("非PE文件识别", file_intel.analyze_file(rnd)["pe"] is False)
    check("纯零文件熵=0", file_intel.file_entropy(bytes(1024)) == 0.0)
    txt = os.path.join(tmp, "s.txt")
    with open(txt, "wb") as f:
        f.write(b"hello VirtualAlloc WScript.Shell mark")
    ss = file_intel.analyze_file(txt)
    check("可疑字符串抽取", "VirtualAlloc" in ss["strings"], ss["strings"][:60])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)