# -*- coding: utf-8 -*-
"""二分定位：生成只含前 N 页的文件，找出出问题的页"""
import zipfile
import os
import re
import subprocess

SRC = r'transformer_ppt/Transformer架构详解.pptx'
OUTDIR = r'transformer_ppt/bisect2'
os.makedirs(OUTDIR, exist_ok=True)


def make_truncated(keep, dst):
    with zipfile.ZipFile(SRC) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}
    pres = data['ppt/presentation.xml'].decode('utf-8')
    m = re.search(r'<p:sldIdLst>(.*?)</p:sldIdLst>', pres, re.S)
    if not m:
        return False
    ids = re.findall(r'<p:sldId[^>]*/>', m.group(1))
    new_block = '<p:sldIdLst>' + ''.join(ids[:keep]) + '</p:sldIdLst>'
    data['ppt/presentation.xml'] = pres.replace(m.group(0), new_block).encode('utf-8')
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(n, data[n])
    return True


targets = [1, 19, 20, 24]
for keep in targets:
    dst = os.path.join(OUTDIR, f'k{keep}.pptx')
    make_truncated(keep, dst)
print('生成完成')

# 逐页测试
lines = ['$ErrorActionPreference = "Continue"',
         '$app = New-Object -ComObject PowerPoint.Application']
for keep in targets:
    src = os.path.abspath(os.path.join(OUTDIR, f'k{keep}.pptx'))
    lines.append(f'$p = Join-Path $env:TEMP "b{keep}.pptx"')
    lines.append(f'Copy-Item "{src}" $p -Force')
    lines.append('try {')
    lines.append('  $pr = $app.Presentations.Open($p,$true,$false,$false)')
    lines.append(f'  Write-Output "keep{keep} -> OK ({keep} pages)"')
    lines.append('  $pr.Close()')
    lines.append('} catch {')
    lines.append(f'  Write-Output "keep{keep} -> FAIL"')
    lines.append('}')
lines.append('try { $app.Quit() } catch {}')

ps = os.path.join(OUTDIR, 'test.ps1')
with open(ps, 'w', encoding='utf-8-sig') as f:
    f.write('\n'.join(lines))

r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
print(r.stdout.strip())
if r.stderr.strip():
    print('ERR:', r.stderr.strip()[:300])
