/* ============================================================
   WinAppMigrator 官网前台交互
   页面正文由服务端渲染（真实库内内容），本脚本只负责：
   1) 实时数字刷新（调用 /api/site、/api/version/latest 真接口）
   2) 视觉与交互特效（灯箱 / 视频弹层 / 折叠 / 计数 / 揭示 / 视差 …）
   纯原生实现，无框架无外部依赖；任一模块缺失元素时静默跳过。
   加载失败时保留服务端渲染结果，绝不写入假数据。
   ============================================================ */
(function () {
  'use strict';

  // 尽早声明「脚本确实执行了」：head 里的超时兜底据此判断。
  // 若本文件因 CDN 拦截或缓存错配而没能执行，head 会撤回 js 标记，让正文恢复可见。
  window.__wmSiteReady = true;

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var TOUCH = !!(window.matchMedia && window.matchMedia('(hover: none)').matches);

  /* ---------- 通用工具 ---------- */
  function fmtInt(n) { return Number(n || 0).toLocaleString('en-US'); }
  function fmtNum(n) { return Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 3 }); }
  function fmtSize(bytes) {
    var b = Number(bytes || 0);
    return b ? (b / 1024 / 1024).toFixed(1) + ' MB' : '';
  }
  /** 只允许 http(s) 与站内相对地址，避免后台误填 javascript: 之类协议 */
  function safeUrl(url) {
    var u = String(url == null ? '' : url).trim();
    if (/^https?:\/\//i.test(u) || /^\/\//.test(u) || /^\/[^/]/.test(u)) { return u; }
    return '';
  }
  function extractVersion(data) {
    if (data == null) { return ''; }
    if (typeof data === 'string') { return data; }
    if (typeof data.version === 'string') { return data.version; }
    if (data.latest && typeof data.latest.version === 'string') { return data.latest.version; }
    return '';
  }

  /* ============================================================
     背景：星尘 + 光标辉光
     ============================================================ */
  function initStars() {
    var canvas = $('#starfield');
    if (!canvas || !canvas.getContext) { return; }
    var ctx = canvas.getContext('2d');
    var W = 0, H = 0, stars = [], streaks = [], nextStreak = 2600, mx = 0, my = 0;

    function resize() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = window.innerWidth; H = window.innerHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      spawn();
    }
    function spawn() {
      var count = W < 768 ? 45 : 90;
      stars = [];
      for (var i = 0; i < count; i++) {
        stars.push({
          x: Math.random() * W, y: Math.random() * H,
          r: Math.random() * 1.1 + 0.3,
          p: Math.random() * Math.PI * 2,
          sp: Math.random() * 0.22 + 0.04,
          tw: 0.006 + Math.random() * 0.012
        });
      }
    }
    function frame(t) {
      ctx.clearRect(0, 0, W, H);
      var px = mx * 8, py = my * 8;
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        s.y -= s.sp; s.p += s.tw;
        if (s.y < -6) { s.y = H + 6; s.x = Math.random() * W; }
        var a = 0.16 + Math.abs(Math.sin(s.p)) * 0.5;
        ctx.beginPath();
        ctx.arc(s.x + px * 0.25, s.y + py * 0.25, s.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(245,245,245,' + a.toFixed(3) + ')';
        ctx.fill();
      }
      if (t > nextStreak) {
        streaks.push({
          x: Math.random() * W * 0.7 + W * 0.2, y: Math.random() * H * 0.4,
          vx: -(3 + Math.random() * 3), vy: 1.6 + Math.random() * 1.6, life: 1
        });
        nextStreak = t + 3000 + Math.random() * 3500;
      }
      for (var k = streaks.length - 1; k >= 0; k--) {
        var st = streaks[k];
        st.x += st.vx; st.y += st.vy; st.life -= 0.012;
        if (st.life <= 0) { streaks.splice(k, 1); continue; }
        var g = ctx.createLinearGradient(st.x, st.y, st.x - st.vx * 12, st.y - st.vy * 12);
        g.addColorStop(0, 'rgba(245,245,245,' + (0.75 * st.life).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(245,245,245,0)');
        ctx.strokeStyle = g; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(st.x, st.y); ctx.lineTo(st.x - st.vx * 12, st.y - st.vy * 12); ctx.stroke();
      }
      if (!REDUCED) { requestAnimationFrame(frame); }
    }

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', function (e) {
      mx = e.clientX / window.innerWidth - 0.5;
      my = e.clientY / window.innerHeight - 0.5;
    }, { passive: true });
    resize();
    if (REDUCED) { frame(0); } else { requestAnimationFrame(frame); }
  }

  function initCursorGlow() {
    var glow = $('#cursorGlow');
    if (!glow || REDUCED || TOUCH) { return; }
    var tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
    function loop() {
      cx += (tx - cx) * 0.12;
      cy += (ty - cy) * 0.12;
      glow.style.transform = 'translate3d(' + cx.toFixed(1) + 'px,' + cy.toFixed(1) + 'px,0)';
      raf = requestAnimationFrame(loop);
    }
    window.addEventListener('mousemove', function (e) {
      tx = e.clientX; ty = e.clientY;
      if (!document.body.classList.contains('has-cursor')) { document.body.classList.add('has-cursor'); }
    }, { passive: true });
    document.addEventListener('mouseleave', function () {
      document.body.classList.remove('has-cursor');
      cancelAnimationFrame(raf);
    });
    loop();
  }

  /* ============================================================
     导航：滚动状态 / 顶部进度 / 移动抽屉 / 滚动高亮
     ============================================================ */
  function initNav() {
    var nav = $('#nav');
    var progress = $('#scrollProgress');
    var burger = $('#navBurger');
    var drawer = $('#navDrawer');
    var ticking = false;

    function update() {
      ticking = false;
      var y = window.pageYOffset;
      if (nav) { nav.classList.toggle('scrolled', y > 30); }
      if (progress) {
        var h = document.documentElement.scrollHeight - window.innerHeight;
        progress.style.width = (h > 0 ? Math.min(y / h, 1) * 100 : 0).toFixed(2) + '%';
      }
      var backTop = $('#backTop');
      if (backTop) { backTop.hidden = y < 400; }
    }
    window.addEventListener('scroll', function () {
      if (ticking) { return; }
      ticking = true;
      requestAnimationFrame(update);
    }, { passive: true });
    update();

    if (burger && drawer) {
      burger.addEventListener('click', function () {
        var open = burger.getAttribute('aria-expanded') === 'true';
        burger.setAttribute('aria-expanded', open ? 'false' : 'true');
        drawer.hidden = open;
      });
      $$('a', drawer).forEach(function (a) {
        a.addEventListener('click', function () {
          burger.setAttribute('aria-expanded', 'false');
          drawer.hidden = true;
        });
      });
    }
  }

  function initScrollSpy() {
    var links = $$('.menu a[data-spy]').filter(function (a) { return a.getAttribute('data-spy'); });
    if (!links.length || !('IntersectionObserver' in window)) { return; }
    var map = {};
    var targets = [];
    links.forEach(function (a) {
      var id = a.getAttribute('data-spy');
      var sec = document.getElementById(id);
      if (!sec) { return; }
      map[id] = a;
      targets.push(sec);
    });
    if (!targets.length) { return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        links.forEach(function (a) { a.classList.remove('active'); });
        var a = map[en.target.id];
        if (a) { a.classList.add('active'); }
      });
    }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
    targets.forEach(function (t) { io.observe(t); });
  }

  /* ============================================================
     滚动揭示 / 数字滚动 / 卡片光影 / 视差 / 磁吸 / 返回顶部
     ============================================================ */
  function initReveal() {
    var els = $$('.sr');
    if (!els.length) { return; }
    if (!('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add('in');
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    els.forEach(function (el) { io.observe(el); });
  }

  function roll(el, target, suffix, dur) {
    var t0 = null;
    function tick(ts) {
      if (t0 === null) { t0 = ts; }
      var p = Math.min((ts - t0) / dur, 1);
      var e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmtNum(target * e) + suffix;
      if (p < 1) { requestAnimationFrame(tick); }
    }
    requestAnimationFrame(tick);
  }

  /** 把「50+」「3 层」「100%」这类值拆成数值与后缀 */
  function splitNum(str) {
    var m = String(str == null ? '' : str).match(/^([\d.,]+)\s*(.*)$/);
    if (!m) { return null; }
    var n = parseFloat(m[1].replace(/,/g, ''));
    return isNaN(n) ? null : { n: n, suffix: m[2] };
  }

  function initCounters() {
    var els = [];
    $$('.num[data-target]').forEach(function (el) { els.push(el); });
    $$('.stat-value[data-raw]').forEach(function (el) {
      var parts = splitNum(el.getAttribute('data-raw'));
      if (parts) {
        el.setAttribute('data-target', parts.n);
        el.setAttribute('data-suffix', parts.suffix);
        els.push(el);
      }
    });
    if (!els.length) { return; }
    if (REDUCED || !('IntersectionObserver' in window)) {
      els.forEach(function (el) {
        var t = parseFloat(el.getAttribute('data-target'));
        el.textContent = fmtNum(t) + (el.getAttribute('data-suffix') || '');
      });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) { return; }
        var el = en.target;
        roll(el, parseFloat(el.getAttribute('data-target')) || 0, el.getAttribute('data-suffix') || '', 1600);
        io.unobserve(el);
      });
    }, { threshold: 0.35 });
    els.forEach(function (el) { io.observe(el); });
  }

  function initCardLight() {
    if (TOUCH) { return; }
    $$('.feature-card[data-light], .adv-card[data-light]').forEach(function (card) {
      card.addEventListener('mousemove', function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
        card.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
      });
    });
  }

  function initParallax() {
    if (REDUCED) { return; }
    var visual = $('#heroVisual'), copy = $('.hero-copy');
    if (!visual && !copy) { return; }
    var ticking = false;
    function onScroll() {
      if (ticking) { return; }
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.pageYOffset;
        if (y < window.innerHeight) {
          if (visual) { visual.style.transform = 'translate3d(0,' + (y * 0.18).toFixed(1) + 'px,0)'; }
          if (copy) { copy.style.transform = 'translate3d(0,' + (y * 0.05).toFixed(1) + 'px,0)'; }
        }
        ticking = false;
      });
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  function initMagnetic() {
    if (REDUCED || TOUCH) { return; }
    $$('.magnetic').forEach(function (el) {
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect();
        var dx = (e.clientX - (r.left + r.width / 2)) / r.width;
        var dy = (e.clientY - (r.top + r.height / 2)) / r.height;
        el.style.transform = 'translate(' + (dx * 6).toFixed(2) + 'px,' + (dy * 5 - 2).toFixed(2) + 'px)';
      });
      el.addEventListener('mouseleave', function () { el.style.transform = ''; });
    });
  }

  function initBackTop() {
    var btn = $('#backTop');
    if (!btn) { return; }
    btn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: REDUCED ? 'auto' : 'smooth' });
    });
  }

  /* ============================================================
     图片灯箱
     ============================================================ */
  function initLightbox() {
    var links = $$('[data-lightbox]');
    var box = $('#lightbox');
    if (!links.length || !box) { return; }

    var img = $('#lbImage'), title = $('#lbTitle'), desc = $('#lbDesc');
    var counter = $('#lbCounter'), strip = $('#lbStrip');
    var index = 0, lastFocus = null;

    function item(i) {
      var a = links[i];
      return {
        src: a.getAttribute('data-src') || a.getAttribute('href'),
        title: a.getAttribute('data-title') || '',
        desc: a.getAttribute('data-desc') || '',
        alt: (a.querySelector('img') || {}).alt || ''
      };
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
      });
    }

    function open(i) {
      index = i;
      lastFocus = document.activeElement;
      box.hidden = false;
      document.documentElement.style.overflow = 'hidden';
      paint();
      var close = $('.lb-close', box);
      if (close) { close.focus(); }
    }

    function close() {
      box.hidden = true;
      document.documentElement.style.overflow = '';
      img.removeAttribute('src');
      if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
    }

    function step(delta) {
      index = (index + delta + links.length) % links.length;
      paint();
    }

    if (strip && links.length > 1) {
      links.forEach(function (a, i) {
        var it = item(i);
        var t = document.createElement('img');
        t.className = 'lb-thumb';
        t.src = it.src;
        t.alt = it.title || ('预览 ' + (i + 1));
        t.addEventListener('click', function () { index = i; paint(); });
        strip.appendChild(t);
      });
    }

    links.forEach(function (a, i) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        open(i);
      });
    });

    $$('[data-lb-close]', box).forEach(function (el) { el.addEventListener('click', close); });
    var prev = $('[data-lb-prev]', box), next = $('[data-lb-next]', box);
    if (prev) { prev.addEventListener('click', function () { step(-1); }); }
    if (next) { next.addEventListener('click', function () { step(1); }); }

    document.addEventListener('keydown', function (e) {
      if (box.hidden) { return; }
      if (e.key === 'Escape') { close(); }
      if (e.key === 'ArrowLeft') { step(-1); }
      if (e.key === 'ArrowRight') { step(1); }
    });

    /* 移动端左右滑动切换 */
    var sx = 0, sy = 0;
    box.addEventListener('touchstart', function (e) {
      sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    }, { passive: true });
    box.addEventListener('touchend', function (e) {
      var dx = e.changedTouches[0].clientX - sx;
      var dy = e.changedTouches[0].clientY - sy;
      if (Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy)) { step(dx < 0 ? 1 : -1); }
    }, { passive: true });
  }

  /* ============================================================
     视频弹层（本地上传 <video> / 外链嵌入 <iframe>）
     ============================================================ */
  function initVideoModal() {
    var cards = $$('[data-video]');
    var modal = $('#videoModal');
    if (!cards.length || !modal) { return; }

    var frame = $('#vmFrame'), title = $('#vmTitle'), desc = $('#vmDesc');
    var lastFocus = null;

    function open(card) {
      var url = safeUrl(card.getAttribute('data-video'));
      var type = (card.getAttribute('data-vtype') || 'file').toLowerCase();
      var poster = safeUrl(card.getAttribute('data-vposter'));
      if (!url) {
        frame.innerHTML = '';
        var bad = document.createElement('p');
        bad.className = 'vm-loading';
        bad.textContent = 'ERR / 视频地址无效，请在后台重新配置';
        frame.appendChild(bad);
        modal.hidden = false;
        return;
      }
      frame.innerHTML = '';
      var loading = document.createElement('p');
      loading.className = 'vm-loading';
      loading.textContent = 'LOADING';
      frame.appendChild(loading);

      if (type === 'embed') {
        var iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.title = card.getAttribute('data-vtitle') || '产品演示视频';
        iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; picture-in-picture; fullscreen');
        iframe.setAttribute('allowfullscreen', 'true');
        iframe.setAttribute('loading', 'lazy');
        iframe.addEventListener('load', function () { loading.remove(); });
        frame.appendChild(iframe);
      } else {
        var video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = 'metadata';
        if (poster) { video.poster = poster; }
        video.addEventListener('loadeddata', function () { loading.remove(); });
        video.addEventListener('error', function () {
          loading.textContent = 'ERR / 视频加载失败';
        });
        frame.appendChild(video);
      }

      title.textContent = card.getAttribute('data-vtitle') || '产品演示';
      desc.textContent = card.getAttribute('data-vdesc') || '';
      lastFocus = document.activeElement;
      modal.hidden = false;
      document.documentElement.style.overflow = 'hidden';
      var closeBtn = $('.vm-close', modal);
      if (closeBtn) { closeBtn.focus(); }
    }

    function close() {
      modal.hidden = true;
      frame.innerHTML = '';
      document.documentElement.style.overflow = '';
      if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
    }

    cards.forEach(function (card) {
      var btn = $('.video-thumb', card);
      if (btn) { btn.addEventListener('click', function () { open(card); }); }
    });
    $$('[data-vm-close]', modal).forEach(function (el) { el.addEventListener('click', close); });
    document.addEventListener('keydown', function (e) {
      if (!modal.hidden && e.key === 'Escape') { close(); }
    });
  }

  /* ============================================================
     常见问题：手风琴（原生 details 打底，JS 只做互斥与平滑）
     ============================================================ */
  function initFaq() {
    var items = $$('#faqList .faq-item');
    if (items.length < 2) { return; }
    items.forEach(function (d) {
      d.addEventListener('toggle', function () {
        if (!d.open) { return; }
        items.forEach(function (o) { if (o !== d) { o.open = false; } });
      });
    });
  }

  /* ============================================================
     实时数据：真实 API 刷新（失败保持服务端渲染结果）
     ============================================================ */
  function setText(sel, text) {
    var el = $(sel);
    if (el && text != null) { el.textContent = text; }
  }

  function applyLive(site, version) {
    var total = site && typeof site.totalDownloads === 'number' ? site.totalDownloads : null;
    var latest = (site && site.latest) || {};
    var ver = version || latest.version || '';

    if (total != null) {
      var num = $('#dlNum');
      if (num) {
        num.setAttribute('data-target', total);
        num.textContent = fmtInt(total);
      }
      setText('#consoleDownloads', fmtInt(total));
      setText('#dlInline', fmtInt(total));
    }

    var line = $('#versionLine');
    if (line && ver) {
      var size = fmtSize(latest.size);
      line.textContent = '';
      var label = document.createTextNode('最新版本 ');
      line.appendChild(label);
      var link = document.createElement('a');
      link.className = 'v-link';
      link.href = '#changelog';
      link.textContent = 'v' + ver;
      line.appendChild(link);
      if (size) {
        var sep = document.createElement('span');
        sep.className = 'v-sep';
        sep.textContent = '/';
        line.appendChild(sep);
        line.appendChild(document.createTextNode(size));
      }
      if (total != null) {
        var sep2 = document.createElement('span');
        sep2.className = 'v-sep';
        sep2.textContent = '/';
        line.appendChild(sep2);
        line.appendChild(document.createTextNode(fmtInt(total) + ' 次下载'));
      }
    }

    var url = safeUrl(latest.url);
    if (url) {
      ['#navDownload', '#downloadBtn', '#clDownload'].forEach(function (sel) {
        var el = $(sel);
        if (el && el.tagName === 'A') {
          el.setAttribute('href', url);
          el.classList.remove('is-muted');
        }
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
      if (!site) {
        /* 服务端已渲染内容，接口失败时仅记录，不覆盖真实数据 */
        if (window.console) { console.warn('实时数据接口不可用，页面保留服务端渲染结果'); }
        document.documentElement.setAttribute('data-live', 'stale');
        return;
      }
      document.documentElement.setAttribute('data-live', 'ok');
      applyLive(site, ver);
    });
  }

  /** 兜底：正文可见性优先于装饰动画。
   *  若样式表不匹配（例如 CDN 缓存了旧版 CSS，动画首帧 opacity:0 无法播完），
   *  或 IntersectionObserver 未触发，则在 2.5 秒后强制显示，绝不让内容留在空白状态。 */
  function initVisibilityFallback() {
    setTimeout(function () {
      $$('.reveal').forEach(function (el) {
        if (parseFloat(window.getComputedStyle(el).opacity) < 0.05) {
          el.classList.add('reveal-forced');
        }
      });
      $$('.sr').forEach(function (el) {
        if (parseFloat(window.getComputedStyle(el).opacity) < 0.05) {
          el.classList.add('in');
        }
      });
    }, 2500);
  }

  /* ---------- 启动 ---------- */
  /** 单个模块初始化失败不应牵连后续模块（尤其不能让滚动揭示失效导致整屏空白） */
  function safe(name, fn) {
    try {
      fn();
    } catch (e) {
      if (window.console) { console.warn('[site] ' + name + ' 初始化失败：', e); }
    }
  }

  function init() {
    setText('#year', new Date().getFullYear());
    safe('stars', initStars);
    safe('cursorGlow', initCursorGlow);
    safe('nav', initNav);
    safe('scrollSpy', initScrollSpy);
    safe('reveal', initReveal);
    safe('counters', initCounters);
    safe('cardLight', initCardLight);
    safe('parallax', initParallax);
    safe('magnetic', initMagnetic);
    safe('backTop', initBackTop);
    safe('lightbox', initLightbox);
    safe('videoModal', initVideoModal);
    safe('faq', initFaq);
    safe('visibilityFallback', initVisibilityFallback);
    safe('liveData', loadLive);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
