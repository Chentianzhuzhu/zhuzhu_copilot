import ast
import subprocess
import sys
import time

PY = sys.executable

# 1) 冷导入 agent_panel，重复 3 次（独立进程）
CODE = "import time;t=time.perf_counter();import zhuzhu_Copilot.ui.agent_panel;print('%.1f'%((time.perf_counter()-t)*1000))"
for i in range(3):
    r = subprocess.run([PY, "-c", CODE], cwd="src", capture_output=True, text=True)
    print(f"cold import #{i+1}: {r.stdout.strip()} ms   {r.stderr.strip()[-120:]}")

# 2) 合成 f-string 复现（合法语法）
def gen(n, use_f):
    out = []
    for i in range(n):
        if use_f:
            out.append("s%d = f\"val {d['k%d']} tail {x + 1} end\"" % (i, i))
        else:
            out.append("s%d = \"val {d['k%d']} tail {x + 1} end\"" % (i, i))
    return "\n".join(out)

print()
for n in (250, 500, 1000, 2000, 4000):
    a = time.perf_counter(); ast.parse(gen(n, True)); t1 = (time.perf_counter() - a) * 1000
    a = time.perf_counter(); ast.parse(gen(n, False)); t2 = (time.perf_counter() - a) * 1000
    print(f"n={n:5d}  f-string {t1:8.1f} ms   普通串 {t2:7.1f} ms   比值 {t1/max(t2,0.01):5.1f}x")
