"""文件静态分析：SHA256 / PE 节区与导入表 / 信息熵 / 可打印字符串（纯标准库，真实文件读取）"""
import hashlib
import math
import os
import re
import struct
from typing import List, Set, Tuple

_HEAD_CAP = 32 * 1024 * 1024   # PE 结构解析读取上限
_STR_CAP = 8 * 1024 * 1024     # 字符串抽取读取上限


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def file_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = float(len(data))
    return -sum((c / n) * math.log2(c / n) for c in freq if c)


def extract_strings(data: bytes, min_len: int = 4) -> str:
    """抽取 ASCII 与 UTF-16LE 可打印字符串（去重，上限 32KB）"""
    out = set()
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        out.add(m.group().decode("ascii", "replace"))
    try:
        s16 = data.decode("utf-16-le", "ignore")
    except Exception:
        s16 = ""
    for m in re.finditer(r"[\u0020-\u007e]{%d,}" % min_len, s16):
        out.add(m.group())
    return "\n".join(sorted(out))[:32768]


def _parse_pe(buf: bytes) -> Tuple[List[dict], Set[str]]:
    """解析 PE 节区与导入表；非 PE 返回 ([], set())"""
    if len(buf) < 0x40 or buf[:2] != b"MZ":
        return [], set()
    e_lfanew = struct.unpack_from("<I", buf, 0x3C)[0]
    if e_lfanew + 24 > len(buf) or buf[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return [], set()
    nsec = struct.unpack_from("<H", buf, e_lfanew + 6)[0]
    opt = e_lfanew + 24
    if opt + 2 > len(buf):
        return [], set()
    magic = struct.unpack_from("<H", buf, opt)[0]
    if magic not in (0x10B, 0x20B):   # PE32 / PE32+
        return [], set()

    sections = []
    sec_off = opt + struct.unpack_from("<H", buf, e_lfanew + 20)[0]
    for i in range(min(nsec, 96)):
        off = sec_off + i * 40
        if off + 40 > len(buf):
            break
        name = buf[off:off + 8].rstrip(b"\0").decode("latin1", "replace")
        vsz, va, rsz, ro = struct.unpack_from("<IIII", buf, off + 8)
        sections.append({"name": name, "va": va, "vsize": vsz,
                         "raw_size": rsz, "raw_off": None if rsz == 0 else ro})

    def rva2off(rva: int):
        for s in sections:
            span = max(s["vsize"], s["raw_size"] or 0)
            if s["va"] <= rva < s["va"] + span and s["raw_off"] is not None:
                return s["raw_off"] + (rva - s["va"])
        return None

    if magic == 0x10B:
        dd_off, thunk, ordinal = opt + 96, 4, 0x80000000
    else:
        dd_off, thunk, ordinal = opt + 112, 8, 0x8000000000000000
    if dd_off + 16 > len(buf):
        return sections, set()

    imp_rva, _ = struct.unpack_from("<II", buf, dd_off + 8)
    imports: Set[str] = set()
    off = rva2off(imp_rva)
    for _ in range(64):
        if off is None or off + 20 > len(buf):
            break
        oft, _td, _fc, name_rva, ft = struct.unpack_from("<IIIII", buf, off)
        if oft == 0 and ft == 0:
            break
        if name_rva:
            no = rva2off(name_rva)
            if no is not None and no < len(buf):
                end = buf.find(b"\0", no, no + 256)
                dll = buf[no:end].decode("latin1", "replace") if end != -1 else ""
                if dll:
                    imports.add(dll)
                    to = rva2off(oft or ft)
                    for _ in range(512):
                        if to is None or to + thunk > len(buf):
                            break
                        val = struct.unpack_from("<I" if thunk == 4 else "<Q", buf, to)[0]
                        if val == 0:
                            break
                        if not (val & ordinal):
                            fo = rva2off(val)
                            if fo is not None and fo + 2 <= len(buf):
                                end2 = buf.find(b"\0", fo + 2, fo + 256)
                                if end2 != -1:
                                    fn = buf[fo + 2:end2].decode("latin1", "replace")
                                    if fn:
                                        imports.add(f"{dll}!{fn.lower()}")
                        to += thunk
        off += 20
    return sections, imports


def analyze_file(path: str) -> dict:
    """返回规则引擎样本 dict：{kind,path,name,ext,size,sha256,pe,sections,imports,entropy,strings}"""
    p = str(path)
    sample = {"kind": "file", "path": p, "name": os.path.basename(p),
              "ext": os.path.splitext(p)[1].lower(), "size": 0, "sha256": "",
              "pe": False, "sections": [], "imports": [], "entropy": 0.0, "strings": ""}
    if not os.path.isfile(p):
        return sample
    try:
        sample["size"] = os.path.getsize(p)
        sample["sha256"] = sha256_of(p)
        with open(p, "rb") as f:
            head = f.read(_HEAD_CAP)
        if not head:
            return sample
        sample["entropy"] = round(file_entropy(head[:256 * 1024]), 3)
        sample["strings"] = extract_strings(head[:_STR_CAP])
        sections, imports = _parse_pe(head)
        if sections:
            sample["pe"] = True
            sample["sections"] = sections
            sample["imports"] = sorted(imports)
    except OSError:
        pass
    return sample