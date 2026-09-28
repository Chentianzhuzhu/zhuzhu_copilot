"""文件静态分析测试：SHA256 / 信息熵 / 可打印字符串 / PE 节区与导入表解析"""
import struct

from zhuzhu_Copilot.core.security_engine import file_intel


def _fake_pe(tmp_path) -> str:
    """构造最小但结构正确的 PE32 文件：MZ + PE 头 + 1 节 + 导入表(kernel32!CreateRemoteThread)。

    布局（对照 file_intel._parse_pe 的偏移约定）：
    - e_lfanew=0x80；COFF 头 SizeOfOptionalHeader=224
    - 节区位于 opt+224：.text, VirtualSize/VA/SizeOfRaw/RawOff(4,0x1000,0x3000,0x400)
    - 数据目录[1]（导入）= (0x2000, 1)；导入数组→DLL 名(0x2100)→序号块(0x2200)
    全部偏移在 .text 节区内（[0x1000, 0x4000) → 文件 [0x400, 0x3400)）。
    """
    p = tmp_path / "sample.exe"
    data = bytearray()
    data += b"MZ" + b"\x00" * 0x3A
    data += struct.pack("<I", 0x80)
    data += b"\x00" * (0x80 - len(data))                     # 对齐到 e_lfanew
    data += b"PE\0\0"
    data += struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x0102)  # 节数=1, SizeOfOpt=224
    # 可选头（PE32 共 224 字节）：Magic(2)+链接器版本(2)+标准字段(88)+NumberOfRvaAndSizes(4)+数据目录(128)
    data += struct.pack("<H", 0x10B)                          # Magic
    data += struct.pack("<H", 0)                              # 链接器版本
    data += b"\x00" * 88                                      # opt+4..opt+92
    data += struct.pack("<I", 2)                              # opt+92 NumberOfRvaAndSizes
    data += struct.pack("<II", 0, 0)                          # 数据目录[0] 导出
    data += struct.pack("<II", 0x2000, 1)                     # 数据目录[1] 导入表
    data += b"\x00" * (224 - 112)                             # 其余目录 → 到 opt+224
    # 节区头部 .text（共 40 字节）
    data += b".text" + b"\x00" * 3
    data += struct.pack("<IIIIIIHHI", 0x3000, 0x1000, 0x3000, 0x400,
                        0, 0, 0, 0, 0x60000020)
    assert len(data) <= 0x400
    data += b"\x00" * (0x400 - len(data))                     # 对齐到 .text 文件起点
    # 导入数组：OriginalFirstThunk=0x2200，Name=DLL名RVA 0x2100
    data += b"\x00" * (0x1400 - len(data))                    # 对齐到文件 0x1400（rva 0x2000）
    data += struct.pack("<IIIII", 0x2200, 0, 0, 0x2100, 0)
    data += b"\x00" * 20                                      # 终止条目
    data += b"\x00" * (0x1500 - len(data))                    # 对齐文件 0x1500（rva 0x2100）
    data += b"KERNEL32.dll\x00"
    data += b"\x00" * (0x1600 - len(data))                    # 对齐文件 0x1600（rva 0x2200）
    data += struct.pack("<H", 0x8000) + b"\x00" * 8           # 序号导入（无函数名）
    data += b"\x00" * 32
    p.write_bytes(bytes(data))
    return str(p)


def test_sha256_and_size(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 100)
    s = file_intel.analyze_file(str(p))
    assert s["size"] == 100
    assert len(s["sha256"]) == 64
    assert s["entropy"] == 0.0          # 全相同字节 → 熵为 0


def test_entropy_high_for_random(tmp_path):
    p = tmp_path / "r.bin"
    p.write_bytes(bytes(range(256)) * 10)
    assert file_intel.analyze_file(str(p))["entropy"] > 7.0


def test_extract_strings():
    text = file_intel.extract_strings(b"hello world\x00" + "\u4e2d\u6587".encode("utf-16-le"))
    assert "hello world" in text


def test_pe_parse(tmp_path):
    path = _fake_pe(tmp_path)
    s = file_intel.analyze_file(path)
    assert s["pe"] is True
    assert any(x["name"] == ".text" for x in s["sections"])
    assert any("kernel32" in i.lower() for i in s["imports"]), s["imports"]


def test_non_pe(tmp_path):
    p = tmp_path / "not.exe"
    p.write_bytes(b"random text " * 100)
    s = file_intel.analyze_file(str(p))
    assert s["pe"] is False and s["imports"] == []