#!/usr/bin/env python3
"""新版官网浏览器回归；只访问本地预览，反馈/媒体分支使用明确隔离的测试数据。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
BASE = "http://127.0.0.1:8099"
PAGES = [('/', 'home'), ('/gallery', 'gallery'), ('/download', 'download'),
         ('/faq', 'faq'), ('/about', 'about'), ('/feedback', 'feedback'),
         ('/sitemap', 'sitemap'), ('/missing-audit-page', 'error')]
OUT = WORKSPACE / 'outputs'
RESULTS: dict = {'scope': 'local preview only; feedback and media use browser-only fixtures',
                 'matrix': [], 'checks': [], 'failures': []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True)
    args = parser.parse_args()
    env = os.environ.copy()
    env['PATH'] = str(Path.home() / '.workbuddy-ai/binaries/node/versions/22.22.2-3') + os.pathsep + env['PATH']

    def cmd(*parts: str, session: str = 'redesign-audit'):
        p = subprocess.run([args.browser, '--session', session, '--json', *parts],
                           capture_output=True, text=True, encoding='utf-8', env=env, timeout=50)
        if p.returncode:
            raise RuntimeError(f'{parts[0]}: {p.stdout} {p.stderr}')
        result = json.loads(p.stdout)
        if not result.get('success'):
            raise RuntimeError(str(result))
        return result.get('data', {})

    def evaluate(js: str, session: str = 'redesign-audit'):
        return cmd('eval', js, session=session).get('result')

    def check(label: str, js: str):
        value = evaluate(js)
        RESULTS['checks'].append({'name': label, 'passed': value is True, 'detail': value})
        if value is not True:
            RESULTS['failures'].append(label)
        print(f"[{'OK' if value is True else 'FAIL'}] {label}", flush=True)

    def visit(path: str):
        cmd('open', BASE + path)
        cmd('snapshot', '-i')

    def submit(selector: str):
        # 聚焦后按 Enter，避免移动浏览器滚动过程中点击被页面位移吞掉。
        cmd('focus', selector)
        cmd('press', 'Enter')

    try:
        for width in [375, 768, 1024, 1440]:
            cmd('set', 'viewport', str(width), '900')
            for path, name in PAGES:
                visit(path)
                info = evaluate("""(() => {
                  const r = document.documentElement, main = document.querySelector('main') || document.body;
                  const overflow = [...document.querySelectorAll('main *,footer *')].filter(e => {
                    const b=e.getBoundingClientRect(), s=getComputedStyle(e);
                    return b.width && s.position!=='fixed' && s.display!=='none' && b.right>innerWidth+2;
                  }).map(e=>e.className).slice(0,8);
                  return {width:innerWidth, scrollWidth:r.scrollWidth,
                    h1:document.querySelectorAll('h1').length,
                    bodyVisible:getComputedStyle(main).opacity!=='0',
                    bg:getComputedStyle(document.body).backgroundColor,
                    brokenImages:[...document.images].filter(e=>e.getClientRects().length && e.complete && !e.naturalWidth).length,
                    overflow, ready:!!window.__wmSiteReady,
                    blue:document.querySelector('.btn-primary') ? getComputedStyle(document.querySelector('.btn-primary')).backgroundColor : null};
                })()""")
                errors = cmd('errors')
                info.update({'page': path, 'name': name, 'viewport': width, 'errors': errors})
                RESULTS['matrix'].append(info)
                if info['scrollWidth'] > width or info['h1'] != 1 or not info['bodyVisible'] or info['brokenImages']:
                    RESULTS['failures'].append(f'layout {path} @ {width}: {info}')
                if name != 'error' and (not info['ready'] or info['bg'] != 'rgb(11, 12, 15)'):
                    RESULTS['failures'].append(f'theme/script {path} @ {width}')
                if width in [375, 1440]:
                    cmd('screenshot', '--full', str(OUT / f'redesign-{name}-{width}.png'))
                if name == 'home' and width == 1440:
                    cmd('screenshot', str(OUT / 'redesign-home-desktop.png'))
                print(f'[LAYOUT] {path} @ {width}: {info["scrollWidth"]}', flush=True)

        cmd('set', 'viewport', '375', '900')
        visit('/')
        cmd('click', '#navBurger')
        check('drawer opens and isolates background', "!document.querySelector('#navDrawer').hidden && document.querySelector('main').inert && document.activeElement.closest('#navDrawer')!==null")
        cmd('focus', '#drawerDownload')
        cmd('press', 'Tab')
        check('drawer Tab wraps within navigation', "document.activeElement.closest('#nav')!==null && document.querySelector('main').inert")
        cmd('press', 'Escape')
        check('drawer Escape restores focus and background', "document.querySelector('#navDrawer').hidden && !document.querySelector('main').inert && document.activeElement.id==='navBurger'")
        cmd('click', '#navBurger')
        cmd('set', 'viewport', '1440', '900')
        check('drawer closes on desktop breakpoint', "document.querySelector('#navDrawer').hidden && !document.querySelector('main').inert")

        visit('/faq')
        cmd('click', '#faqList details:nth-child(1) summary')
        cmd('click', '#faqList details:nth-child(2) summary')
        check('FAQ accordion remains mutually exclusive', "document.querySelectorAll('#faqList details[open]').length===1 && document.querySelector('#faqList details:nth-child(2)').open")
        cmd('scroll', 'down', '2000')
        check('back-to-top becomes available', "!document.querySelector('#backTop').hidden")
        cmd('click', '#backTop')
        cmd('wait', '--fn', 'window.scrollY < 2')
        check('back-to-top reaches page start', 'window.scrollY<2')

        # 分支测试使用现有品牌封面作为可加载图片，不冒充真实产品截图。
        fixture = (ROOT / 'target/preview/pages/gallery.html').read_text(encoding='utf-8')
        fixture = fixture.replace('</main>', '''<section class="wrap" id="auditMedia">
          <a id="auditImage1" data-lightbox href="/og/og-cover.png" data-title="测试图片一"><img src="/og/og-cover.png" alt="浏览器测试素材"></a>
          <a id="auditImage2" data-lightbox href="/og/og-cover.png" data-title="测试图片二"><img src="/og/og-cover.png" alt="浏览器测试素材"></a>
          <article data-video="invalid" data-vtitle="无效视频"><button class="video-thumb" id="auditInvalid">测试无效视频</button></article>
          <article data-video="/missing-audit-video.mp4" data-vtitle="加载失败"><button class="video-thumb" id="auditFailed">测试加载失败</button></article>
          <article data-video="/faq" data-vtype="embed" data-vtitle="嵌入测试"><button class="video-thumb" id="auditEmbed">测试嵌入</button></article>
        </section></main>''')
        cmd('network', 'route', '**/gallery', '--body', fixture)
        visit('/gallery')
        cmd('click', '#auditImage1')
        check('lightbox opens and focuses close button', "!document.querySelector('#lightbox').hidden && document.querySelector('main').inert && document.activeElement.classList.contains('lb-close')")
        cmd('press', 'ArrowRight')
        check('lightbox keyboard advances image', "document.querySelector('#lbCounter').textContent==='2 / 2'")
        cmd('click', '#lbStrip button:first-child')
        check('thumbnail is a pressed semantic button', "document.querySelector('#lbCounter').textContent==='1 / 2' && document.querySelector('#lbStrip button').getAttribute('aria-pressed')==='true'")
        evaluate("(() => {let e=new Event('touchstart'); e.touches=[{clientX:160,clientY:200}]; document.querySelector('#lightbox').dispatchEvent(e); e=new Event('touchend'); e.changedTouches=[{clientX:60,clientY:200}]; document.querySelector('#lightbox').dispatchEvent(e);return true;})()")
        check('lightbox handles swipe direction', "document.querySelector('#lbCounter').textContent==='2 / 2'")
        cmd('focus', '#lbStrip button:last-child')
        cmd('press', 'Tab')
        check('lightbox Tab cycles inside dialog', "document.activeElement.closest('#lightbox')!==null")
        cmd('press', 'Escape')
        check('lightbox restores trigger focus', "document.querySelector('#lightbox').hidden && document.activeElement.id==='auditImage1' && !document.querySelector('main').inert")
        cmd('click', '#auditInvalid')
        check('invalid video still opens accessible error layer', "!document.querySelector('#videoModal').hidden && document.querySelector('#vmFrame').textContent.includes('视频地址无效') && document.activeElement.classList.contains('vm-close')")
        cmd('press', 'Escape')
        cmd('click', '#auditFailed')
        cmd('wait', '--fn', "document.querySelector('#vmFrame').textContent.includes('视频加载失败')")
        check('video load failure retains useful message', "document.querySelector('#vmFrame').textContent.includes('视频加载失败')")
        cmd('press', 'Escape')
        cmd('click', '#auditEmbed')
        check('embedded video has title and dialog focus', "document.querySelector('#vmFrame iframe').title==='嵌入测试' && document.activeElement.classList.contains('vm-close')")
        cmd('press', 'Escape')
        cmd('set', 'media', 'dark', 'reduced-motion')
        visit('/gallery')
        cmd('click', '#auditFailed')
        check('reduced-motion video does not autoplay', "!document.querySelector('#vmFrame video').autoplay")
        cmd('press', 'Escape')
        cmd('network', 'unroute', '**/gallery')

        cmd('set', 'viewport', '375', '900')
        visit('/feedback')
        submit('#feedbackSubmit')
        check('feedback minimum length error and focus', "!document.querySelector('#feedbackError').hidden && document.activeElement.id==='feedbackContent'")
        cmd('fill', '#queryId', 'invalid')
        submit('#querySubmit')
        check('query credential validation and focus', "!document.querySelector('#queryError').hidden && document.activeElement.id==='queryId'")
        # 所有提交/查询在浏览器内替换为测试 Response，不调用真实反馈后端。
        evaluate("window.auditFetch=window.fetch; window.fetch=function(u,o){if(String(u).startsWith('/api/feedback'))return Promise.resolve(new Response(JSON.stringify({ok:false,error:'隔离测试错误'}),{status:500}));return window.auditFetch(u,o);};true")
        cmd('fill', '#feedbackContent', '这是浏览器隔离测试反馈')
        submit('#feedbackSubmit')
        cmd('wait', '--fn', "document.querySelector('#feedbackError').textContent==='隔离测试错误'")
        check('failed feedback retains input and clears busy state', "document.querySelector('#feedbackContent').value==='这是浏览器隔离测试反馈' && !document.querySelector('#feedbackSubmit').disabled && document.querySelector('#feedbackForm').getAttribute('aria-busy')==='false'")
        evaluate("window.fetch=function(u,o){if(String(u).startsWith('/api/feedback'))return Promise.resolve(new Response(JSON.stringify({error:'测试限流'}),{status:429}));return window.auditFetch(u,o);};true")
        submit('#feedbackSubmit')
        cmd('wait', '--fn', "!document.querySelector('#feedbackLimit').hidden")
        check('feedback rate-limit notice is visible', "!document.querySelector('#feedbackLimit').hidden && document.querySelector('#feedbackContent').value.length>0")
        evaluate("window.fetch=function(u,o){if(String(u)==='/api/feedback')return Promise.resolve(new Response(JSON.stringify({ok:true,token:'a'.repeat(64)}),{status:200})); if(String(u).startsWith('/api/feedback/'))return Promise.resolve(new Response(JSON.stringify({ok:true,id:7,status:'replied',content:'测试反馈',reply:'测试回复',createdAt:'测试时间',repliedAt:'测试时间'}),{status:200}));return window.auditFetch(u,o);};true")
        submit('#feedbackSubmit')
        cmd('wait', '--fn', "!document.querySelector('#feedbackSuccess').hidden")
        check('feedback success receives focus and credential', "document.activeElement.id==='feedbackSuccess' && document.querySelector('#feedbackToken').textContent.length===64 && document.querySelector('#feedbackForm').hidden")
        cmd('screenshot', '--full', str(OUT / 'redesign-feedback-success-fixture-375.png'))
        evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('test'))}}); document.execCommand=()=>false; true")
        cmd('click', '#copyTokenBtn')
        cmd('wait', '--fn', "document.querySelector('#copyTokenText').textContent==='请手动复制'")
        check('copy failure never claims success', "document.querySelector('#copyTokenText').textContent==='请手动复制'")
        cmd('fill', '#queryId', 'a' * 64)
        submit('#querySubmit')
        cmd('wait', '--fn', "!document.querySelector('#queryResult').hidden")
        check('query renders reply and focuses result', "document.activeElement.id==='queryResult' && document.querySelector('#qrReply').textContent==='测试回复' && !document.querySelector('#qrReplyBlock').hidden")
        cmd('click', '#feedbackAgain')
        check('another feedback restores empty form and focus', "!document.querySelector('#feedbackForm').hidden && document.querySelector('#feedbackContent').value==='' && document.activeElement.id==='feedbackContent'")

        # 外部脚本不可用：页面正文与手机导航必须仍可访问。
        cmd('network', 'route', '**/js/*', '--abort')
        visit('/')
        check('missing script preserves content and mobile navigation', "!window.__wmSiteReady && getComputedStyle(document.querySelector('.menu')).display==='flex' && getComputedStyle(document.querySelector('main')).opacity==='1' && document.documentElement.scrollWidth===innerWidth")
        cmd('screenshot', '--full', str(OUT / 'redesign-no-script-375.png'))
        cmd('network', 'unroute', '**/js/*')
        cmd('network', 'route', '**/api/site', '--abort')
        cmd('network', 'route', '**/api/version/latest', '--abort')
        visit('/')
        cmd('wait', '--fn', "document.documentElement.dataset.live==='stale'")
        check('API failure preserves SSR values', "document.querySelector('#dlNum').textContent.includes('1,234') && document.documentElement.dataset.live==='stale'")
        check('reduced-motion disables animation and smooth scroll', "matchMedia('(prefers-reduced-motion: reduce)').matches && getComputedStyle(document.documentElement).scrollBehavior==='auto' && getComputedStyle(document.querySelector('.section-head')).animationName==='none'")
        cmd('network', 'unroute')
    except Exception as exc:
        RESULTS['failures'].append(str(exc))
        print(f'[ERROR] {exc}', flush=True)
    finally:
        try:
            cmd('close')
        except Exception as exc:
            RESULTS['failures'].append(f'close: {exc}')
        (OUT / 'redesign-browser-audit.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[DONE] {len(RESULTS['matrix'])} layouts, {len(RESULTS['checks'])} interactions, {len(RESULTS['failures'])} failures", flush=True)
    return 1 if RESULTS['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
