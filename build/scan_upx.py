import os

DIST = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "zhuzhu Copilot"))

UPX_FOUND = []
dll_count = 0
for root, dirs, files in os.walk(DIST):
    if "runtime" in root.split(os.sep):
        continue
    for f in files:
        if not f.lower().endswith((".dll", ".pyd")):
            continue
        p = os.path.join(root, f)
        dll_count += 1
        try:
            with open(p, "rb") as fh:
                head = fh.read(2048)
            if b"UPX0" in head or b"UPX!" in head or b"UPX1" in head:
                size = os.path.getsize(p)
                UPX_FOUND.append((p, size))
        except Exception:
            pass

print(f"扫描 DLL/pyd 总数: {dll_count}")
print(f"UPX 压缩文件数: {len(UPX_FOUND)}")
for p, s in UPX_FOUND:
    rel = p.replace(DIST, "")
    print(f"  {rel}  ({s} bytes)")
