# scripts/_verify_signature.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.signature import signer_subject
from zhuzhu_Copilot.core import security as _sec

# Windows 系统自带签名文件的真实验证（kernel32.dll 恒为 Microsoft 签名）
k32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "kernel32.dll")
if os.path.isfile(k32):
    ok = _sec._has_valid_signature(k32)
    subject = signer_subject(k32) if ok else ""
    check("系统文件签名有效", ok is True, str(ok))
    check("签发者提取含 Microsoft", "microsoft" in subject.lower(), subject or "(empty)")
else:
    check("系统文件存在", False, k32)

# 无签名文件 → 提取为空
tmp = os.path.join(os.environ.get("TEMP", "."), "_verify_signature_unsigned.bin")
with open(tmp, "wb") as f:
    f.write(b"MZ" + os.urandom(128))
check("无签名签发者为空", signer_subject(tmp) == "")
os.remove(tmp)

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)