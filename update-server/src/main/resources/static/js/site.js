/* 官网交互：原生实现；真实 API 失败时保留 SSR 内容。
 * 动效只写 transform / opacity，不用持续 RAF 常驻循环；
 * 数字递增与视差都在进入视口时启动一次，不做常驻逐帧计算。 */
(function () {
  'use strict';
  window.__wmSiteReady = true;
  document.documentElement.classList.add('site-ready');
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var activeLayer = null;

  function safe(name, fn) {
    try { fn(); }
    catch (e) { if (window.console) { console.warn('[site] ' + name + ' 初始化失败：', e); } }
  }
  function fmtInt(n) { return Number(n || 0).toLocaleString('en-US'); }
  function fmtSize(bytes) { return bytes ? (Number(bytes) / 1024 / 1024).toFixed(1) + ' MB' : ''; }
  function safeUrl(url) {
    var u = String(url == null ? '' : url).trim();
    return /^https?:\/\//i.test(u) || /^\/\//.test(u) || /^\/[^/]/.test(u) ? u : '';
  }
  function setText(sel, text) {
    var el = $(sel);
    if (el && text != null) { el.textContent = text; }
  }
  function focusables(root) {
    return $$('a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),video[controls],iframe,[tabindex="0"]', root)
      .filter(function (el) { return !el.closest('[hidden], [inert]') && el.getClientRects().length; });
  }
  /* 抽屉 / 弹层共同的焦点管理：背景 inert、Tab 循环、Esc 关闭、恢复焦点与原滚动状态。 */
  function lockLayer(root, focusTarget, onClose) {
    if (activeLayer) { activeLayer.close(); }
    var lastFocus = document.activeElement;
    var oldOverflow = document.documentElement.style.overflow;
    var siblings = Array.prototype.slice.call(document.body.children).filter(function (el) {
      return el !== root && !el.contains(root) && !el.classList.contains('nav-overlay') && !/^(SCRIPT|STYLE)$/.test(el.tagName);
    });
    var oldInert = siblings.map(function (el) { return el.inert; });
    siblings.forEach(function (el) { el.inert = true; });
    document.documentElement.style.overflow = 'hidden';
    function keydown(e) {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key !== 'Tab') { return; }
      var els = focusables(root), first = els[0], last = els[els.length - 1];
      if (!first) { e.preventDefault(); return; }
      if (e.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
    }
    document.addEventListener('keydown', keydown);
    var state = {
      close: onClose,
      release: function () {
        document.removeEventListener('keydown', keydown);
        siblings.forEach(function (el, i) { el.inert = oldInert[i]; });
        document.documentElement.style.overflow = oldOverflow;
        if (activeLayer === state) { activeLayer = null; }
        if (lastFocus && lastFocus.isConnected && lastFocus.getClientRects().length) { lastFocus.focus(); }
      }
    };
    activeLayer = state;
    if (focusTarget) { focusTarget.focus(); }
    return state;
  }
  function initNav() {
    var nav = $('#nav'), progress = $('#scrollProgress'), back = $('#backTop');
    var burger = $('#navBurger'), drawer = $('#navDrawer'), ticking = false;
    function update() {
      ticking = false;
      var y = window.pageYOffset;
      if (nav) { nav.classList.toggle('scrolled', y > 24); }
      if (progress) {
        var h = document.documentElement.scrollHeight - window.innerHeight;
        progress.style.width = (h > 0 ? Math.min(y / h, 1) * 100 : 0).toFixed(2) + '%';
      }
      if (back) { back.hidden = y < 400; }
    }
    function schedule() {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    update();
    if (!burger || !drawer) { return; }
    var overlay = document.createElement('div'), layer = null;
    overlay.className = 'nav-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    document.body.appendChild(overlay);
    function close() {
      drawer.hidden = true;
      burger.setAttribute('aria-expanded', 'false');
      burger.setAttribute('aria-label', '打开导航菜单');
      document.body.classList.remove('nav-open');
      if (layer) { layer.release(); layer = null; }
    }
    burger.addEventListener('click', function () {
      if (!drawer.hidden) { close(); return; }
      if (activeLayer) { activeLayer.close(); }
      drawer.hidden = false;
      burger.setAttribute('aria-expanded', 'true');
      burger.setAttribute('aria-label', '关闭导航菜单');
      document.body.classList.add('nav-open');
      layer = lockLayer(nav, $('a', drawer), close);
    });
    overlay.addEventListener('click', close);
    $$('a', drawer).forEach(function (a) { a.addEventListener('click', close); });
    window.addEventListener('resize', function () { if (window.innerWidth > 1000 && !drawer.hidden) { close(); } }, { passive: true });
  }
  function initScrollSpy() {
    if (!('IntersectionObserver' in window)) { return; }
    var links = $$('.menu a[data-spy]'), map = {}, targets = [];
    links.forEach(function (a) {
      var id = a.getAttribute('data-spy'), sec = document.getElementById(id);
      if (sec) { map[id] = a; targets.push(sec); }
    });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        links.forEach(function (a) { a.classList.remove('active'); a.removeAttribute('aria-current'); });
        var a = map[en.target.id];
        if (a) { a.classList.add('active'); a.setAttribute('aria-current', 'location'); }
      });
    }, { rootMargin: '-20% 0px -65% 0px', threshold: 0 });
    targets.forEach(function (t) { io.observe(t); });
  }
  /* 揭示动效：所有入场动画共用一个 IntersectionObserver。
     元素进入视口后立即 unobserve —— 观察器不常驻，回调不重复触发。
     没有 IntersectionObserver 的旧浏览器直接给终态，绝不留下不可见内容。 */
  function initReveal() {
    var sel = '.section-head.sr, .stats-lead.sr, .dl-block.sr, .cta-card.sr, ' +
      '.features-grid.sr, .gallery-grid.sr, .adv-grid.sr, .video-wall.sr, ' +
      '.stats-grid.sr, .changelog-card.sr, .empty-state.sr, .faq-list.sr, ' +
      '.reveal, .footer';
    var els = $$(sel);
    if (!els.length) { return; }
    if (REDUCED || !('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        en.target.classList.add('in');
        io.unobserve(en.target);
      });
    }, { threshold: .12, rootMargin: '0px 0px -8% 0px' });
    els.forEach(function (el) { io.observe(el); });
  }

  /* 数字递增：进入视口后逐帧改 textContent。
     用整数插值 + 一次 rAF 循环，循环在数字到顶时立即结束，不常驻。 */
  function initCountUp() {
    var nums = $$('.num[data-target], .num[data-raw]');
    if (!nums.length || REDUCED || !('IntersectionObserver' in window)) { return; }
    function run(el) {
      var target = parseFloat(el.getAttribute('data-target') || el.getAttribute('data-raw'));
      if (!isFinite(target) || target <= 0) { return; }
      var decimals = (String(el.getAttribute('data-raw') || '').split('.')[1] || '').length;
      var dur = 1100, start = null;
      el.classList.add('is-counting');
      function step(ts) {
        if (start === null) { start = ts; }
        var p = Math.min((ts - start) / dur, 1);
        /* 三次缓出，起步快收尾稳，读起来像「数上去」而不是「匀速爬」。 */
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = (target * eased).toFixed(decimals);
        if (p < 1) { requestAnimationFrame(step); }
        else { el.textContent = fmtInt(target); el.classList.remove('is-counting'); }
      }
      requestAnimationFrame(step);
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        run(en.target);
        io.unobserve(en.target);
      });
    }, { threshold: .4 });
    nums.forEach(function (el) { io.observe(el); });
  }

  /* 微视差：滚动时区块缓慢位移，营造层次。
     监听 scroll 只做一件事 —— 写一个 transform 变量；
     真正应用交给 CSS 的 transform 合成层，JS 不参与逐帧布局计算。
     被动监听 + rAF 节流，移动端默认关闭（低端机滚动掉帧代价太高）。 */
  function initParallax() {
    if (REDUCED || !('IntersectionObserver' in window)) { return; }
    var isNarrow = window.matchMedia('(max-width: 800px)').matches;
    var coarse = window.matchMedia('(pointer: coarse)').matches;
    if (isNarrow || coarse) { return; }
    var nodes = $$('[data-parallax]');
    if (!nodes.length) { return; }
    var ticking = false;
    function paint() {
      ticking = false;
      var vh = window.innerHeight;
      nodes.forEach(function (el) {
        var rect = el.getBoundingClientRect();
        if (rect.bottom < -80 || rect.top > vh + 80) { return; }
        /* 元素中心相对视口中心的偏移量，映射到 ±10px 以内。 */
        var offset = (rect.top + rect.height / 2 - vh / 2) / vh;
        el.style.setProperty('--py', (offset * 10).toFixed(2));
      });
    }
    function schedule() {
      if (!ticking) { ticking = true; requestAnimationFrame(paint); }
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        en.target.classList.toggle('is-parallax', en.isIntersecting);
        if (en.isIntersecting) { io.unobserve(en.target); }
      });
      schedule();
    }, { rootMargin: '120px 0px' });
    nodes.forEach(function (el) { io.observe(el); });
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    paint();
  }
  function initBackTop() {
    [$('#backTop'), $('#backTopLink')].forEach(function (el) {
      if (!el) { return; }
      el.addEventListener('click', function (e) {
        e.preventDefault();
        window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' });
      });
    });
  }
  function initFaq() {
    var items = $$('#faqList .faq-item');
    items.forEach(function (d) {
      d.addEventListener('toggle', function () {
        /* 手风琴：展开一项时收起其余项。CSS 用 grid-template-rows 过渡，
           收起动画要等本项展开完成再触发，否则两项同时过渡会互相打断。 */
        if (!d.open) { return; }
        window.setTimeout(function () {
          items.forEach(function (other) { if (other !== d) { other.open = false; } });
        }, REDUCED ? 0 : 180);
      });
    });
  }
  function initLightbox() {
    var links = $$('[data-lightbox]'), box = $('#lightbox');
    if (!links.length || !box) { return; }
    var img = $('#lbImage'), title = $('#lbTitle'), desc = $('#lbDesc');
    var counter = $('#lbCounter'), strip = $('#lbStrip'), index = 0, layer = null;
    function item(i) {
      var a = links[i];
      return { src: safeUrl(a.getAttribute('data-src') || a.getAttribute('href')),
        title: a.getAttribute('data-title') || '', desc: a.getAttribute('data-desc') || '',
        alt: (a.querySelector('img') || {}).alt || '' };
    }
    function paint() {
      var it = item(index);
      img.src = it.src;
      img.alt = it.alt || it.title || '产品界面截图';
      title.textContent = it.title;
      desc.textContent = it.desc;
      counter.textContent = (index + 1) + ' / ' + links.length;
      $$('.lb-thumb', strip).forEach(function (t, i) {
        t.classList.toggle('active', i === index);
        t.setAttribute('aria-pressed', String(i === index));
      });
    }
    function close() {
      box.hidden = true;
      img.removeAttribute('src');
      if (layer) { layer.release(); layer = null; }
    }
    function open(i) {
      if (activeLayer) { activeLayer.close(); }
      index = i;
      box.hidden = false;
      paint();
      layer = lockLayer(box, $('.lb-close', box), close);
    }
    function step(delta) { index = (index + delta + links.length) % links.length; paint(); }
    if (strip && links.length > 1) {
      links.forEach(function (a, i) {
        var it = item(i), t = document.createElement('button'), thumb = document.createElement('img');
        t.type = 'button';
        t.className = 'lb-thumb';
        t.setAttribute('aria-label', '查看第 ' + (i + 1) + ' 张：' + it.title);
        thumb.src = it.src;
        thumb.alt = '';
        thumb.loading = 'lazy';
        t.appendChild(thumb);
        t.addEventListener('click', function () { index = i; paint(); });
        strip.appendChild(t);
      });
    }
    links.forEach(function (a, i) {
      a.addEventListener('click', function (e) { e.preventDefault(); open(i); });
    });
    $$('[data-lb-close]', box).forEach(function (el) { el.addEventListener('click', close); });
    var prev = $('[data-lb-prev]', box), next = $('[data-lb-next]', box);
    if (prev) { prev.addEventListener('click', function () { step(-1); }); prev.hidden = links.length < 2; }
    if (next) { next.addEventListener('click', function () { step(1); }); next.hidden = links.length < 2; }
    document.addEventListener('keydown', function (e) {
      if (box.hidden) { return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    });
    var sx = 0, sy = 0;
    box.addEventListener('touchstart', function (e) { sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, { passive: true });
    box.addEventListener('touchend', function (e) {
      var dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
      if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy)) { step(dx < 0 ? 1 : -1); }
    }, { passive: true });
  }
  function initVideoModal() {
    var modal = $('#videoModal'), cards = $$('[data-video]');
    if (!modal || !cards.length) { return; }
    var frame = $('#vmFrame'), title = $('#vmTitle'), desc = $('#vmDesc'), layer = null;
    function close() {
      modal.hidden = true;
      frame.innerHTML = '';
      if (layer) { layer.release(); layer = null; }
    }
    function open(card) {
      if (activeLayer) { activeLayer.close(); }
      var url = safeUrl(card.getAttribute('data-video'));
      var type = (card.getAttribute('data-vtype') || 'file').toLowerCase();
      frame.innerHTML = '';
      title.textContent = card.getAttribute('data-vtitle') || '产品演示';
      desc.textContent = card.getAttribute('data-vdesc') || '';
      modal.hidden = false;
      layer = lockLayer(modal, $('.vm-close', modal), close);
      var loading = document.createElement('p');
      loading.className = 'vm-loading';
      loading.setAttribute('role', 'status');
      loading.textContent = url ? '正在加载视频…' : '视频地址无效，请联系站点管理员。';
      frame.appendChild(loading);
      if (!url) { return; }
      if (type === 'embed') {
        var iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.title = title.textContent;
        iframe.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture; fullscreen');
        iframe.setAttribute('allowfullscreen', 'true');
        iframe.addEventListener('load', function () { loading.remove(); });
        frame.appendChild(iframe);
      } else {
        var video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = !REDUCED;
        video.playsInline = true;
        video.preload = 'metadata';
        var poster = safeUrl(card.getAttribute('data-vposter'));
        if (poster) { video.poster = poster; }
        video.addEventListener('loadeddata', function () { loading.remove(); });
        video.addEventListener('error', function () { loading.textContent = '视频加载失败，请稍后重试。'; });
        frame.appendChild(video);
      }
    }
    cards.forEach(function (card) {
      var btn = $('.video-thumb', card);
      if (btn) { btn.addEventListener('click', function () { open(card); }); }
    });
    $$('[data-vm-close]', modal).forEach(function (el) { el.addEventListener('click', close); });
  }
  function extractVersion(data) {
    if (typeof data === 'string') { return data; }
    return data && (data.version || (data.latest && data.latest.version)) || '';
  }
  function applyLive(site, version) {
    var total = site && typeof site.totalDownloads === 'number' ? site.totalDownloads : null;
    var latest = site && site.latest || {}, ver = version || latest.version || '';
    if (total != null) {
      var num = $('#dlNum');
      if (num) { num.setAttribute('data-target', total); num.textContent = fmtInt(total); }
      setText('#consoleDownloads', fmtInt(total));
      setText('#dlInline', fmtInt(total));
    }
    var line = $('#versionLine');
    if (line && ver) {
      line.textContent = '最新版本 ';
      var link = document.createElement('a');
      link.className = 'v-link'; link.href = '#changelog'; link.textContent = 'v' + ver;
      line.appendChild(link);
      var size = fmtSize(latest.size);
      [size, total != null ? fmtInt(total) + ' 次下载' : ''].forEach(function (text) {
        if (!text) { return; }
        var sep = document.createElement('span'); sep.className = 'v-sep'; sep.textContent = '/';
        line.appendChild(sep); line.appendChild(document.createTextNode(text));
      });
    }
    var url = safeUrl(latest.url);
    if (url) {
      ['#navDownload', '#downloadBtn', '#clDownload', '#drawerDownload', '#mobileDownload'].forEach(function (sel) {
        var el = $(sel);
        if (el && el.tagName === 'A') { el.href = url; el.classList.remove('is-muted'); }
      });
    }
  }
  function loadLive() {
    var siteReq = fetch('/api/site', { headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); });
    var verReq = fetch('/api/version/latest', { headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); });
    Promise.allSettled([siteReq, verReq]).then(function (res) {
      var site = res[0].status === 'fulfilled' ? res[0].value : null;
      var ver = res[1].status === 'fulfilled' ? extractVersion(res[1].value) : '';
      document.documentElement.setAttribute('data-live', site ? 'ok' : 'stale');
      if (site) { applyLive(site, ver); }
    });
  }
  function init() {
    safe('nav', initNav);
    safe('reveal', initReveal);
    safe('countUp', initCountUp);
    safe('parallax', initParallax);
    safe('scrollSpy', initScrollSpy);
    safe('backTop', initBackTop);
    safe('faq', initFaq);
    safe('lightbox', initLightbox);
    safe('videoModal', initVideoModal);
    safe('liveData', loadLive);
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); }
  else { init(); }
})();
