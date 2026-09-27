/* ============================================================
   WinAppMigrator · 管理后台
   纯原生 JS，无框架无外部依赖；所有数据真实调用后端 API。

   设计要点：
   1) 列表编辑器由「字段模式 SCHEMAS」驱动，新增一类可编辑内容只需加一条模式；
   2) 媒体字段统一走媒体选择弹窗（可上传 / 可挑选 / 可手填），避免手工拼路径；
   3) 所有写入值都通过 DOM 属性赋值，不拼接 HTML，杜绝注入。
   ============================================================ */

(function () {
  "use strict";

  // 声明后台脚本已执行：index.html 的超时提示据此判断是否需要显示"脚本未加载"提示。
  window.__wmAdminReady = true;

  /* ---------- 接口与状态 ---------- */

  var API = {
    login: "/admin/api/login",
    site: "/admin/api/site",
    siteMedia: "/admin/api/site/media",
    version: "/admin/api/version",
    stats: "/admin/api/stats"
  };

  var TOKEN_KEY = "wm_admin_token";
  var token = localStorage.getItem(TOKEN_KEY) || null;

  var state = {
    content: {},        // 已保存的官网内容（用于保留未在表单中暴露的字段）
    icons: [],          // 图标白名单
    versions: [],
    trend: [],
    media: { image: [], video: [] }
  };

  var chart = { canvas: null, ctx: null, data: [], hover: null };

  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ============================================================
     字段模式：字段类型 text / textarea / select / icon / media
     ============================================================ */

  function f(key, label, type, options) {
    return { key: key, label: label, type: type || "text", options: options || null };
  }
  function media(key, label, kind) {
    return { key: key, label: label, type: "media", mediaKind: kind || "image" };
  }

  var SCHEMAS = {
    features: [
      f("icon", "图标", "icon"),
      f("title", "标题"),
      f("desc", "描述", "textarea")
    ],
    gallery: [
      media("url", "图片", "image"),
      f("title", "标题"),
      f("desc", "描述")
    ],
    videos: [
      f("type", "来源", "select", [["file", "本地上传"], ["embed", "外链嵌入"]]),
      media("url", "视频地址", "auto"),
      media("poster", "封面图", "image"),
      f("title", "标题"),
      f("desc", "描述", "textarea")
    ],
    advantages: [
      f("title", "标题"),
      f("desc", "描述", "textarea")
    ],
    faq: [
      f("q", "问题"),
      f("a", "回答", "textarea")
    ],
    stats: [
      f("label", "标签"),
      f("value", "数值")
    ],
    requirements: [
      f("label", "项目"),
      f("value", "说明")
    ],
    footerLinks: [
      f("label", "文字"),
      f("href", "链接")
    ],
    aboutContacts: [
      f("label", "名称"),
      f("href", "链接 / 邮箱")
    ],
    aboutMembers: [
      f("role", "角色"),
      f("name", "姓名"),
      media("avatar", "头像", "image")
    ],
    sections: [
      f("tag", "标签 / 序号"),
      f("title", "标题"),
      f("sub", "副标题")
    ]
  };

  /* 分区文案的固定键与中文名（与后端 sections 结构一一对应） */
  var SECTION_KEYS = ["features", "gallery", "videos", "stats", "advantages", "faq", "changelog"];
  var SECTION_LABELS = {
    features: "功能特性", gallery: "界面实拍", videos: "视频演示",
    stats: "数据一览", advantages: "产品优势", faq: "常见问题", changelog: "更新日志"
  };

  /* 列表编辑器渲染顺序（sections 固定行、不可增删） */
  var EDITOR_ORDER = ["sections", "features", "gallery", "videos", "advantages", "faq", "stats", "requirements", "footerLinks", "aboutContacts", "aboutMembers"];

  /* ============================================================
     fetch 统一封装
     ============================================================ */

  function apiFetch(url, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    if (token) { headers["Authorization"] = "Bearer " + token; }
    if (options.body instanceof FormData) {
      delete headers["Content-Type"];
    } else if (options.body && typeof options.body !== "string") {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    return fetch(url, Object.assign({}, options, { headers: headers })).then(function (res) {
      if (res.status === 401) {
        logout();
        throw new Error("登录状态已失效，请重新登录");
      }
      return res.text().then(function (text) {
        var data = null;
        if (text) { try { data = JSON.parse(text); } catch (e) { data = text; } }
        if (!res.ok) {
          var msg = "请求失败（" + res.status + "）";
          if (data && typeof data === "object" && data.error) { msg = data.error; }
          else if (typeof data === "string" && data) { msg = data; }
          var err = new Error(msg);
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  /* ============================================================
     登录 / 登出
     ============================================================ */

  function showLogin() {
    $("mainView").classList.add("hidden");
    $("loginView").classList.remove("hidden");
    $("password").value = "";
    $("loginError").classList.add("hidden");
  }

  function showMain() {
    $("loginView").classList.add("hidden");
    $("mainView").classList.remove("hidden");
    loadAll();
  }

  function logout() {
    token = null;
    localStorage.removeItem(TOKEN_KEY);
    showLogin();
    toast("已退出登录", "err");
  }

  function doLogin() {
    var password = $("password").value;
    if (!password) { showLoginError("请输入访问密码"); return; }
    setBtnBusy($("loginBtn"), true, "登录中…");
    apiFetch(API.login, { method: "POST", body: { password: password } })
      .then(function (data) {
        token = data && data.token ? data.token : null;
        if (!token) { throw new Error("服务端未返回有效令牌"); }
        localStorage.setItem(TOKEN_KEY, token);
        showMain();
        toast("登录成功", "ok");
      })
      .catch(function (err) { showLoginError(err.message || "登录失败"); })
      .finally(function () { setBtnBusy($("loginBtn"), false, "登 录"); });
  }

  function showLoginError(msg) {
    var box = $("loginError");
    box.textContent = msg;
    box.classList.remove("hidden");
  }

  function setBtnBusy(btn, busy, text) {
    if (!btn) { return; }
    if (busy) {
      btn.dataset.origin = btn.textContent;
      btn.disabled = true;
      btn.textContent = text;
    } else {
      btn.disabled = false;
      btn.textContent = text || btn.dataset.origin || btn.textContent;
    }
  }

  /* ============================================================
     数据加载
     ============================================================ */

  function loadAll() {
    // 先并行拉取内容与媒体库，再检查引用是否还有效
    Promise.all([loadSite(), loadMedia()]).then(function () {
      try { warnBrokenMediaRefs(); } catch (e) { /* 仅提示，异常不影响后台使用 */ }
    });
    loadVersions();
  }

  function loadSite() {
    return apiFetch(API.site)
      .then(function (data) {
        state.content = (data && data.content) || {};
        state.icons = (data && data.icons) || [];
        fillBasicFields();
        renderAllEditors();
      })
      .catch(function (err) { toast("加载官网内容失败：" + err.message, "err"); });
  }

  function fillBasicFields() {
    var c = state.content;
    $("siteTitle").value = c.title || "";
    $("siteSlogan").value = c.slogan || "";
    $("siteDescription").value = c.description || "";
    setMediaValue("heroImage", c.heroImage || "");

    var footer = c.footer || {};
    $("footerIcp").value = footer.icp || "";
    $("footerContactLabel").value = footer.contactLabel || "";

    var seo = c.seo || {};
    $("seoKeywords").value = seo.keywords || "";
    setMediaValue("seoOgImage", seo.ogImage || "");
  }

  /* ============================================================
     列表编辑器
     ============================================================ */

  function renderAllEditors() {
    EDITOR_ORDER.forEach(function (kind) {
      var box = document.querySelector('[data-editor="' + kind + '"]');
      if (box) { renderEditor(kind, box); }
    });
  }

  function renderEditor(kind, box) {
    box.innerHTML = "";
    var schema = SCHEMAS[kind];
    if (!schema) { return; }
    if (kind === "sections") {
      renderSectionRows(box);
      return;
    }
    var items = itemsOf(kind);
    items.forEach(function (item, i) {
      box.appendChild(buildRow(kind, schema, item, i, { count: items.length }));
    });
    if (!items.length) { appendEmptyHint(box, kind); }
  }

  function itemsOf(kind) {
    var c = state.content || {};
    if (kind === "footerLinks") { return (c.footer && c.footer.links) || []; }
    if (kind === "aboutContacts") { return (c.about && c.about.contacts) || []; }
    if (kind === "aboutMembers") { return (c.about && c.about.members) || []; }
    return Array.isArray(c[kind]) ? c[kind] : [];
  }

  function appendEmptyHint(box, kind) {
    var p = document.createElement("p");
    p.className = "list-empty";
    p.textContent = "暂无内容，点击右上角按钮添加";
    p.dataset.emptyFor = kind;
    box.appendChild(p);
  }

  function renderSectionRows(box) {
    var sections = (state.content && state.content.sections) || {};
    SECTION_KEYS.forEach(function (key) {
      var val = sections[key] || {};
      var row = buildRow("sections", SCHEMAS.sections, val, 0, {
        sectionKey: key,
        label: SECTION_LABELS[key] + "  " + key
      });
      box.appendChild(row);
    });
    $("sectionCount").textContent = String(SECTION_KEYS.length);
  }

  /**
   * 构建一行编辑器
   * @param {string} kind 列表类型
   * @param {Array} schema 字段模式
   * @param {Object} values 初始值
   * @param {number} index 序号
   * @param {Object} [opts] {sectionKey, label}
   */
  function buildRow(kind, schema, values, index, opts) {
    opts = opts || {};
    var row = document.createElement("div");
    row.className = "list-row" + (kind === "sections" ? " is-section" : "");
    row.dataset.kind = kind;
    if (opts.sectionKey) { row.dataset.section = opts.sectionKey; }

    var label = document.createElement("span");
    label.className = "list-row-label";
    label.textContent = opts.label || ("#" + String(index + 1).padStart(2, "0"));
    if (opts.sectionKey) { label.classList.add("is-fixed"); }
    row.appendChild(label);

    var fields = document.createElement("div");
    fields.className = "list-row-fields";
    schema.forEach(function (field) {
      fields.appendChild(buildField(field, (values || {})[field.key]));
    });
    row.appendChild(fields);

    if (!opts.sectionKey) {
      var tools = document.createElement("div");
      tools.className = "list-row-tools";

      tools.appendChild(iconButton("up", "上移", function () {
        var prev = row.previousElementSibling;
        if (prev && prev.classList.contains("list-row")) {
          row.parentElement.insertBefore(row, prev);
          refreshRowIndexes(row.parentElement);
        }
      }, index === 0));
      tools.appendChild(iconButton("down", "下移", function () {
        var next = row.nextElementSibling;
        if (next && next.classList.contains("list-row")) {
          row.parentElement.insertBefore(next, row);
          refreshRowIndexes(row.parentElement);
        }
      }, opts.count != null && index === opts.count - 1));
      tools.appendChild(iconButton("del", "删除该行", function () {
        var box = row.parentElement;
        row.remove();
        refreshRowIndexes(box);
        if (!$$(".list-row", box).length) { appendEmptyHint(box, kind); }
      }));
      row.appendChild(tools);
    }
    return row;
  }

  var ICON_SVG = {
    up: '<path d="M12 19V5m0 0-6 6m6-6 6 6"/>',
    down: '<path d="M12 5v14m0 0 6-6m-6 6-6-6"/>',
    del: '<path d="M5 5l14 14M19 5L5 19"/>'
  };

  function iconButton(kind, title, onClick, disabled) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "list-tool list-tool-" + kind;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.disabled = !!disabled;
    b.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + ICON_SVG[kind] + "</svg>";
    if (!disabled) { b.addEventListener("click", onClick); }
    return b;
  }

  function buildField(field, value) {
    var wrap = document.createElement("div");
    wrap.className = "list-field" + (field.type === "textarea" ? " is-wide" : "");

    var lab = document.createElement("span");
    lab.className = "list-field-label";
    lab.textContent = field.label;
    wrap.appendChild(lab);

    if (field.type === "textarea") {
      var ta = document.createElement("textarea");
      ta.className = "input textarea";
      ta.rows = 2;
      ta.dataset.key = field.key;
      wrap.appendChild(ta);
      ta.value = value == null ? "" : String(value);
      return wrap;
    }

    if (field.type === "select") {
      var sel = document.createElement("select");
      sel.className = "input select";
      sel.dataset.key = field.key;
      (field.options || []).forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt[0];
        o.textContent = opt[1];
        sel.appendChild(o);
      });
      wrap.appendChild(sel);
      sel.value = value == null ? "" : String(value);
      return wrap;
    }

    if (field.type === "icon") {
      var isel = document.createElement("select");
      isel.className = "input select";
      isel.dataset.key = field.key;
      var names = state.icons.length ? state.icons : ["square"];
      names.forEach(function (name) {
        var o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        isel.appendChild(o);
      });
      wrap.appendChild(isel);
      isel.value = names.indexOf(value) >= 0 ? value : names[0];
      return wrap;
    }

    if (field.type === "media") {
      var mf = document.createElement("div");
      mf.className = "media-field";
      mf.dataset.mediaField = field.key;
      mf.dataset.kind = field.mediaKind || "image";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "input";
      input.dataset.key = field.key;
      input.placeholder = field.mediaKind === "image" ? "/uploads/img/…" : "/uploads/video/… 或 https://…";
      input.value = value == null ? "" : String(value);
      mf.appendChild(input);
      wrap.appendChild(mf);
      enhanceMediaField(mf);
      return wrap;
    }

    var inp = document.createElement("input");
    inp.type = "text";
    inp.className = "input";
    inp.dataset.key = field.key;
    inp.value = value == null ? "" : String(value);
    wrap.appendChild(inp);
    return wrap;
  }

  function refreshRowIndexes(container) {
    if (!container) { return; }
    $$(".list-row", container).forEach(function (row, i) {
      var label = row.querySelector(".list-row-label");
      if (label && !label.classList.contains("is-fixed")) {
        label.textContent = "#" + String(i + 1).padStart(2, "0");
      }
    });
  }

  /* ---------- 媒体字段：附加「选择/上传」按钮与预览 ---------- */

  function enhanceMediaField(mf) {
    if (mf.dataset.enhanced === "1") { return; }
    mf.dataset.enhanced = "1";
    var input = mf.querySelector("input[data-key]");
    if (!input) { return; }

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secondary btn-sm media-pick-btn";
    btn.textContent = "选择 / 上传";
    mf.appendChild(btn);

    var preview = document.createElement("div");
    preview.className = "media-preview";
    mf.appendChild(preview);

    function sync() {
      var url = input.value.trim();
      preview.innerHTML = "";
      if (!url) { return; }
      if (/\.(mp4|webm|ogg|mov)$/i.test(url) || mf.dataset.kind === "video") {
        var v = document.createElement("video");
        v.src = url;
        v.muted = true;
        v.preload = "metadata";
        preview.appendChild(v);
      } else {
        var img = document.createElement("img");
        img.src = url;
        img.alt = "预览";
        img.loading = "lazy";
        img.addEventListener("error", function () { preview.innerHTML = ""; });
        preview.appendChild(img);
      }
    }

    btn.addEventListener("click", function () {
      openPicker(mf.dataset.kind || "image", function (url) {
        input.value = url;
        sync();
      }, input.value.trim());
    });
    input.addEventListener("change", sync);
    sync();
  }

  /** 为页面里静态写好的媒体字段（主图 / 分享图）绑定选择能力 */
  function initStaticMediaFields() {
    $$("[data-media-field]").forEach(function (mf) { enhanceMediaField(mf); });
  }

  function setMediaValue(fieldId, value) {
    var wrap = document.querySelector('[data-media-field="' + fieldId + '"]');
    if (!wrap) { return; }
    var input = wrap.querySelector("input[data-key], input");
    if (!input) { return; }
    input.value = value || "";
    input.dispatchEvent(new Event("change"));
  }

  /* ============================================================
     收集表单内容
     ============================================================ */

  function collectRows(kind, keys) {
    var box = document.querySelector('[data-editor="' + kind + '"]');
    if (!box) { return []; }
    var out = [];
    $$(".list-row", box).forEach(function (row) {
      var item = {};
      var empty = true;
      keys.forEach(function (key) {
        var el = row.querySelector('[data-key="' + key + '"]');
        var val = el ? String(el.value || "").trim() : "";
        item[key] = val;
        if (val) { empty = false; }
      });
      if (!empty) { out.push(item); }
    });
    return out;
  }

  function collectSections() {
    var box = document.querySelector('[data-editor="sections"]');
    var out = {};
    if (!box) { return out; }
    $$(".list-row[data-section]", box).forEach(function (row) {
      var key = row.dataset.section;
      out[key] = {
        tag: valueOf(row, "tag"),
        title: valueOf(row, "title"),
        sub: valueOf(row, "sub")
      };
    });
    return out;
  }

  function valueOf(row, key) {
    var el = row.querySelector('[data-key="' + key + '"]');
    return el ? String(el.value || "").trim() : "";
  }

  function collectSiteContent() {
    var prevSeo = (state.content && state.content.seo) || {};
    var prevFooter = (state.content && state.content.footer) || {};
    var prevAbout = (state.content && state.content.about) || {};
    return {
      title: $("siteTitle").value.trim(),
      slogan: $("siteSlogan").value.trim(),
      description: $("siteDescription").value.trim(),
      heroImage: mediaValue("heroImage"),
      sections: collectSections(),
      features: collectRows("features", ["icon", "title", "desc"]),
      gallery: collectRows("gallery", ["url", "title", "desc"]),
      videos: collectRows("videos", ["type", "url", "poster", "title", "desc"]),
      advantages: collectRows("advantages", ["title", "desc"]),
      faq: collectRows("faq", ["q", "a"]),
      stats: collectRows("stats", ["label", "value"]),
      requirements: collectRows("requirements", ["label", "value"]),
      seo: {
        keywords: $("seoKeywords").value.trim(),
        ogImage: mediaValue("seoOgImage"),
        lang: prevSeo.lang || "zh-CN"
      },
      footer: {
        icp: $("footerIcp").value.trim(),
        contactLabel: $("footerContactLabel").value.trim() || "联系我们",
        links: collectRows("footerLinks", ["label", "href"])
      },
      about: {
        sectionTag: prevAbout.sectionTag || "// 关于我们",
        sectionTitle: prevAbout.sectionTitle || "联系我们",
        sectionSub: prevAbout.sectionSub || "团队介绍与联系方式",
        contacts: collectRows("aboutContacts", ["label", "href"]),
        members: collectRows("aboutMembers", ["role", "name", "avatar"])
      }
    };
  }

  function mediaValue(fieldId) {
    var wrap = document.querySelector('[data-media-field="' + fieldId + '"]');
    if (!wrap) { return ""; }
    var input = wrap.querySelector("input[data-key], input");
    return input ? input.value.trim() : "";
  }

  function saveSite() {
    var content = collectSiteContent();
    setBtnBusy($("saveSiteBtn"), true, "保存中…");
    apiFetch(API.site, { method: "PUT", body: { content: content } })
      .then(function () {
        state.content = content;
        toast("官网内容已保存，刷新官网即可看到更新", "ok");
      })
      .catch(function (err) { toast("保存失败：" + err.message, "err"); })
      .finally(function () { setBtnBusy($("saveSiteBtn"), false, "保存修改"); });
  }

  /* ============================================================
     媒体选择弹窗
     ============================================================ */

  var picker = { kind: "image", onPick: null, tabs: ["image", "video"] };

  function openPicker(kind, onPick, current) {
    picker.kind = kind === "video" ? "video" : "image";
    picker.onPick = onPick;
    picker.tabs = kind === "auto" ? ["image", "video", "url"] : [picker.kind, "url"];
    renderPickerTabs();
    $("pickerUrl").value = current || "";
    $("pickerMask").classList.remove("hidden");
    switchPickerTab(kind === "video" ? "video" : (kind === "auto" ? "image" : "image"));
  }

  function closePicker() {
    $("pickerMask").classList.add("hidden");
    picker.onPick = null;
  }

  function renderPickerTabs() {
    var box = $("pickerTabs");
    box.innerHTML = "";
    picker.tabs.forEach(function (kind) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "picker-tab";
      b.dataset.kind = kind;
      b.textContent = kind === "image" ? "图片" : kind === "video" ? "视频" : "手填地址";
      b.addEventListener("click", function () { switchPickerTab(kind); });
      box.appendChild(b);
    });
  }

  function switchPickerTab(kind) {
    picker.kind = kind;
    $$("#pickerTabs .picker-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.kind === kind);
    });
    var manual = kind === "url";
    $("pickerManual").classList.toggle("hidden", !manual);
    $("pickerGrid").classList.toggle("hidden", manual);
    if (!manual) { loadPickerGrid(kind); }
  }

  function loadPickerGrid(kind) {
    var grid = $("pickerGrid");
    grid.innerHTML = '<p class="picker-empty">正在加载…</p>';
    apiFetch(API.siteMedia + "?kind=" + kind)
      .then(function (data) {
        var items = (data && data.items) || [];
        state.media[kind] = items;
        renderPickerGrid(kind, items);
      })
      .catch(function (err) {
        grid.innerHTML = "";
        var p = document.createElement("p");
        p.className = "picker-empty";
        p.textContent = err.message;
        grid.appendChild(p);
      });
  }

  function renderPickerGrid(kind, items) {
    var grid = $("pickerGrid");
    grid.innerHTML = "";
    if (!items.length) {
      var p = document.createElement("p");
      p.className = "picker-empty";
      p.textContent = "该分类暂无资源，可点击左下角上传";
      grid.appendChild(p);
      return;
    }
    items.forEach(function (item) {
      var cell = document.createElement("button");
      cell.type = "button";
      cell.className = "picker-cell";
      cell.title = item.name;
      if (kind === "image") {
        var img = document.createElement("img");
        img.src = item.url;
        img.alt = item.name;
        img.loading = "lazy";
        cell.appendChild(img);
      } else {
        var v = document.createElement("video");
        v.src = item.url;
        v.muted = true;
        v.preload = "metadata";
        cell.appendChild(v);
      }
      var meta = document.createElement("span");
      meta.className = "picker-cell-meta";
      meta.textContent = formatSize(item.size);
      cell.appendChild(meta);
      cell.addEventListener("click", function () {
        $("pickerUrl").value = item.url;
        $$(".picker-cell", grid).forEach(function (c) { c.classList.remove("active"); });
        cell.classList.add("active");
      });
      cell.addEventListener("dblclick", function () {
        if (picker.onPick) { picker.onPick(item.url); }
        closePicker();
      });
      grid.appendChild(cell);
    });
  }

  function pickerUpload(file) {
    var kind = picker.kind === "video" ? "video" : picker.kind === "url" ? "image" : picker.kind;
    var fd = new FormData();
    fd.append("file", file);
    var btn = $("pickerUploadBtn");
    setBtnBusy(btn, true, "上传中…");
    apiFetch(API.siteMedia + "?kind=" + kind, { method: "POST", body: fd })
      .then(function (data) {
        if (!data || !data.url) { throw new Error("服务端未返回地址"); }
        $("pickerUrl").value = data.url;
        toast("上传成功", "ok");
        loadPickerGrid(kind);
      })
      .catch(function (err) { toast("上传失败：" + err.message, "err"); })
      .finally(function () { setBtnBusy(btn, false, "上传到当前分类"); });
  }

  function confirmPicker() {
    var url = $("pickerUrl").value.trim();
    if (!url) { toast("请先选择或填写媒体地址", "err"); return; }
    if (picker.onPick) { picker.onPick(url); }
    closePicker();
  }

  /* ============================================================
     媒体库面板
     ============================================================ */

  function loadMedia() {
    return Promise.all(["image", "video"].map(function (kind) {
      return apiFetch(API.siteMedia + "?kind=" + kind)
        .then(function (data) {
          var items = (data && data.items) || [];
          state.media[kind] = items;
          renderMediaGrid(kind, items);
        })
        .catch(function (err) { toast("加载" + (kind === "image" ? "图片" : "视频") + "失败：" + err.message, "err"); });
    }));
  }

  /**
   * 检查内容里引用的媒体是否仍存在。
   *
   * 为什么需要：媒体被删除后，若某个字段仍指向它，页面就会出现空白图；
   * 而旧的后台页面保存时还会把这个失效地址原样写回去（本次线上就出现过这种反复）。
   */
  function warnBrokenMediaRefs() {
    var broken = [];
    [["主视觉图", mediaValue("heroImage")], ["社交分享图", mediaValue("seoOgImage")]]
      .forEach(function (pair) { if (isMissingMedia(pair[1])) { broken.push(pair[0]); } });

    [["gallery", "界面实拍", ["url"], "图片"], ["videos", "视频演示", ["url", "poster"], "视频"]]
      .forEach(function (spec) {
        var box = document.querySelector('[data-editor="' + spec[0] + '"]');
        if (!box) { return; }
        $$(".list-row", box).forEach(function (row, i) {
          spec[2].forEach(function (key) {
            if (isMissingMedia(valueOf(row, key))) {
              broken.push(spec[1] + " #" + (i + 1) + " 的" + (key === "poster" ? "封面" : spec[3] + "地址"));
            }
          });
        });
      });

    if (broken.length) {
      toast("以下位置引用的文件已不存在，保存前请重新选择或清空：" + broken.join("、"), "err");
    }
  }

  /** 站内上传资源：媒体库里找不到即视为失效；外链与空值不判定 */
  function isMissingMedia(url) {
    if (!url || url.indexOf("/uploads/") !== 0) { return false; }
    var all = (state.media.image || []).concat(state.media.video || []);
    return !all.some(function (item) { return item.url === url; });
  }

  function renderMediaGrid(kind, items) {
    var grid = $("mediaImageGrid");
    var empty = $("imageEmpty");
    var count = $("imageCount");
    if (kind === "video") {
      grid = $("mediaVideoGrid");
      empty = $("videoEmpty");
      count = $("videoCount");
    }
    count.textContent = String(items.length);
    empty.hidden = items.length > 0;
    grid.innerHTML = "";
    items.forEach(function (item) {
      var card = document.createElement("figure");
      card.className = "media-card";

      var thumb = document.createElement("div");
      thumb.className = "media-card-thumb";
      if (kind === "image") {
        var img = document.createElement("img");
        img.src = item.url;
        img.alt = item.name;
        img.loading = "lazy";
        thumb.appendChild(img);
      } else {
        var v = document.createElement("video");
        v.src = item.url;
        v.muted = true;
        v.preload = "metadata";
        thumb.appendChild(v);
      }
      card.appendChild(thumb);

      var body = document.createElement("figcaption");
      body.className = "media-card-body";
      var name = document.createElement("span");
      name.className = "media-card-name";
      name.textContent = item.name;
      name.title = item.url;
      var meta = document.createElement("span");
      meta.className = "media-card-meta";
      meta.textContent = formatSize(item.size) + " · " + formatDate(new Date(item.updatedAt).toISOString());
      body.appendChild(name);
      body.appendChild(meta);

      var actions = document.createElement("div");
      actions.className = "media-card-actions";
      var copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn btn-ghost btn-sm";
      copyBtn.textContent = "复制地址";
      copyBtn.addEventListener("click", function () { copyText(item.url); });
      var delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn btn-danger btn-sm";
      delBtn.textContent = "删除";
      delBtn.addEventListener("click", function () { deleteMedia(kind, item); });
      actions.appendChild(copyBtn);
      actions.appendChild(delBtn);
      body.appendChild(actions);
      card.appendChild(body);
      grid.appendChild(card);
    });
  }

  function deleteMedia(kind, item) {
    var refs = referencesOf(item.url);
    var warn = refs.length
      ? "；注意：该文件仍被「" + refs.join("、") + "」引用，删除后这些位置会出现空白图片"
      : "";
    confirmDialog("删除媒体文件", "确定删除 " + item.name + " 吗？" + warn)
      .then(function (ok) {
        if (!ok) { return; }
        apiFetch(API.siteMedia + "?kind=" + kind + "&name=" + encodeURIComponent(item.name), { method: "DELETE" })
          .then(function () {
            toast("已删除 " + item.name, "ok");
            loadMedia();
          })
          .catch(function (err) { toast("删除失败：" + err.message, "err"); });
      });
  }

  /** 找出内容里引用了该媒体地址的位置（删除前提醒，避免留下空白图） */
  function referencesOf(url) {
    var where = [];
    var c = state.content || {};
    if (c.heroImage === url) { where.push("主视觉图"); }
    if ((c.seo || {}).ogImage === url) { where.push("社交分享图"); }
    (c.gallery || []).forEach(function (g, i) {
      if (g && g.url === url) { where.push("界面实拍 #" + (i + 1)); }
    });
    (c.videos || []).forEach(function (v, i) {
      if (v && v.url === url) { where.push("视频演示 #" + (i + 1)); }
      if (v && v.poster === url) { where.push("视频封面 #" + (i + 1)); }
    });
    return where;
  }

  function uploadMedia(kind, file) {
    var fd = new FormData();
    fd.append("file", file);
    var btn = kind === "image" ? $("uploadImageBtn") : $("uploadVideoBtn");
    setBtnBusy(btn, true, "上传中…");
    apiFetch(API.siteMedia + "?kind=" + kind, { method: "POST", body: fd })
      .then(function (data) {
        if (!data || !data.url) { throw new Error("服务端未返回地址"); }
        toast("上传成功：" + data.name, "ok");
        loadMedia();
      })
      .catch(function (err) { toast("上传失败：" + err.message, "err"); })
      .finally(function () { setBtnBusy(btn, false, kind === "image" ? "上传图片" : "上传视频"); });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(function () { toast("已复制：" + text, "ok"); })
        .catch(function () { fallbackCopy(text); });
      return;
    }
    fallbackCopy(text);
  }

  /** 剪贴板 API 不可用时的兜底：临时选中文本并提示手动复制（不使用原生 prompt） */
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.className = "copy-helper";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    toast(ok ? "已复制：" + text : "自动复制不可用，请手动复制：" + text, ok ? "ok" : "err");
  }

  /* ============================================================
     版本发布
     ============================================================ */

  function loadVersions() {
    return apiFetch(API.version)
      .then(function (data) {
        state.versions = Array.isArray(data) ? data : [];
        renderVersionTable();
        renderStatsVersionTable();
      })
      .catch(function (err) { toast("加载版本列表失败：" + err.message, "err"); });
  }

  function renderVersionTable() {
    $("versionCount").textContent = String(state.versions.length);
    if (!state.versions.length) {
      $("versionTbody").innerHTML = '<tr class="empty-row"><td colspan="7">暂无已发布版本</td></tr>';
      return;
    }
    var html = "";
    state.versions.forEach(function (v) {
      html += "<tr>" +
        '<td class="mono">' + esc(v.version) + "</td>" +
        '<td class="mono">' + esc(v.fileName) + "</td>" +
        '<td class="mono">' + formatSize(v.fileSize) + "</td>" +
        "<td>" + esc(v.notes || "") + "</td>" +
        '<td class="mono">' + formatDate(v.createdAt) + "</td>" +
        '<td><span class="tag-force' + (v.forceUpdate ? "" : " no") + '">' +
        (v.forceUpdate ? "强制" : "可选") + "</span></td>" +
        '<td class="td-right"><button class="btn btn-danger btn-sm" data-del-id="' + v.id + '">删除</button></td>' +
        "</tr>";
    });
    $("versionTbody").innerHTML = html;
    $$("#versionTbody [data-del-id]").forEach(function (btn) {
      btn.addEventListener("click", function () { deleteVersion(Number(btn.dataset.delId)); });
    });
  }

  function renderStatsVersionTable() {
    if (!state.versions.length) {
      $("statsVersionTbody").innerHTML = '<tr class="empty-row"><td colspan="4">暂无版本数据</td></tr>';
      return;
    }
    var html = "";
    state.versions.forEach(function (v) {
      html += "<tr>" +
        '<td class="mono">' + esc(v.version) + "</td>" +
        '<td class="mono">' + esc(v.fileName) + "</td>" +
        '<td class="mono">' + formatDate(v.createdAt) + "</td>" +
        '<td class="mono">' + formatSize(v.fileSize) + "</td>" +
        "</tr>";
    });
    $("statsVersionTbody").innerHTML = html;
  }

  function publishVersion() {
    var file = $("releaseFile").files && $("releaseFile").files[0];
    var version = $("releaseVersion").value.trim();
    if (!file) { toast("请选择安装包文件", "err"); return; }
    if (!version) { toast("请填写版本号", "err"); return; }

    var fd = new FormData();
    fd.append("file", file);
    fd.append("version", version);
    fd.append("notes", $("releaseNotes").value.trim());
    fd.append("force", $("releaseForce").checked ? "true" : "false");

    setBtnBusy($("releaseBtn"), true, "发布中…");
    var msg = $("releaseMsg");
    msg.classList.remove("hidden", "ok", "err");
    msg.textContent = "正在上传并发布：" + file.name;

    apiFetch(API.version, { method: "POST", body: fd })
      .then(function () {
        msg.classList.add("ok");
        msg.textContent = "发布成功";
        $("releaseForm").reset();
        $("releaseFileName").textContent = "选择安装包文件";
        toast("版本 " + version + " 发布成功", "ok");
        loadVersions();
      })
      .catch(function (err) {
        msg.classList.add("err");
        msg.textContent = "发布失败：" + err.message;
        toast("发布失败：" + err.message, "err");
      })
      .finally(function () { setBtnBusy($("releaseBtn"), false, "发布版本"); });
  }

  function deleteVersion(id) {
    var target = state.versions.find(function (v) { return v.id === id; });
    var label = target ? target.version : String(id);
    confirmDialog("删除版本", "确定删除版本 " + label + " 吗？该操作不可恢复。")
      .then(function (ok) {
        if (!ok) { return; }
        apiFetch(API.version + "/" + id, { method: "DELETE" })
          .then(function () {
            toast("版本 " + label + " 已删除", "ok");
            loadVersions();
          })
          .catch(function (err) { toast("删除失败：" + err.message, "err"); });
      });
  }

  /* ============================================================
     统计图表
     ============================================================ */

  function loadStats() {
    return apiFetch(API.stats)
      .then(function (data) {
        data = data || {};
        state.trend = Array.isArray(data.trend) ? data.trend : [];
        state.versions = Array.isArray(data.versions) ? data.versions : state.versions;
        $("totalDownloads").textContent = formatNumber(data.totalDownloads || 0);
        renderStatsVersionTable();
        drawTrendChart();
      })
      .catch(function (err) { toast("加载统计数据失败：" + err.message, "err"); });
  }

  function drawTrendChart() {
    var canvas = $("trendCanvas");
    var box = $("chartBox");
    if (!canvas || !box) { return; }
    var data = state.trend || [];
    $("chartEmpty").classList.toggle("hidden", data.length > 0);
    canvas.style.display = data.length ? "block" : "none";
    hideTip();
    if (!data.length) { return; }

    var dpr = window.devicePixelRatio || 1;
    var rect = box.getBoundingClientRect();
    if (rect.width <= 0) { return; }

    var pad = { top: 24, right: 18, bottom: 38, left: 46 };
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    var w = rect.width, h = rect.height;
    var plotW = w - pad.left - pad.right;
    var plotH = h - pad.top - pad.bottom;

    var values = data.map(function (d) { return d.downloads || 0; });
    var maxV = Math.max.apply(null, values);
    var minV = Math.min.apply(null, values);
    if (maxV === minV) { maxV = minV + 1; }
    var yMax = niceCeil(maxV), yMin = 0;

    var xStep = data.length > 1 ? plotW / (data.length - 1) : 0;
    var px = function (i) { return pad.left + (data.length > 1 ? i * xStep : plotW / 2); };
    var py = function (v) { return pad.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH; };

    ctx.font = '10px "Cascadia Mono", Consolas, monospace';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    var ticks = 4;
    for (var t = 0; t <= ticks; t++) {
      var val = yMin + ((yMax - yMin) * t) / ticks;
      var y = py(val);
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(w - pad.right, y);
      ctx.stroke();
      ctx.fillStyle = "#5c5c5c";
      ctx.fillText(formatNumber(Math.round(val)), pad.left - 8, y);
    }

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    var labelEvery = Math.max(1, Math.ceil(data.length / 7));
    data.forEach(function (d, i) {
      if (i % labelEvery !== 0 && i !== data.length - 1) { return; }
      ctx.fillStyle = "#5c5c5c";
      ctx.fillText(shortDate(d.date), px(i), pad.top + plotH + 10);
    });

    var grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
    grad.addColorStop(0, "rgba(30,64,175,0.34)");
    grad.addColorStop(0.6, "rgba(30,64,175,0.09)");
    grad.addColorStop(1, "rgba(30,64,175,0)");

    ctx.beginPath();
    data.forEach(function (d, i) {
      var x = px(i), y = py(d.downloads || 0);
      if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
    });
    ctx.lineTo(px(data.length - 1), pad.top + plotH);
    ctx.lineTo(px(0), pad.top + plotH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    data.forEach(function (d, i) {
      var x = px(i), y = py(d.downloads || 0);
      if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
    });
    ctx.strokeStyle = "#1E40AF";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    data.forEach(function (d, i) {
      var x = px(i), y = py(d.downloads || 0);
      ctx.beginPath();
      ctx.arc(x, y, i === data.length - 1 ? 3.5 : 2.2, 0, Math.PI * 2);
      ctx.fillStyle = i === data.length - 1 ? "#ffffff" : "#5B7BE8";
      ctx.fill();
    });

    chart.ctx = ctx;
    chart.data = data;
    chart.hover = { pad: pad, px: px, py: py };
  }

  function onChartMove(ev) {
    if (!chart.hover || !chart.data.length) { return; }
    var rect = $("chartBox").getBoundingClientRect();
    var mx = ev.clientX - rect.left;
    var my = ev.clientY - rect.top;
    var best = 18, idx = -1;
    chart.data.forEach(function (d, i) {
      var dist = Math.hypot(chart.hover.px(i) - mx, chart.hover.py(d.downloads || 0) - my);
      if (dist < best) { best = dist; idx = i; }
    });
    if (idx < 0) { hideTip(); return; }
    var d = chart.data[idx];
    var x = chart.hover.px(idx);
    var y = chart.hover.py(d.downloads || 0);
    var tip = $("chartTip");
    tip.innerHTML = '<span class="tip-date">' + esc(d.date) + "</span>" +
      '<span class="tip-value">下载 ' + formatNumber(d.downloads || 0) + " 次</span>";
    tip.classList.remove("hidden");
    var tipW = tip.offsetWidth;
    var tipX = Math.max(tipW / 2 + 8, Math.min(x, rect.width - tipW / 2 - 8));
    tip.style.left = tipX + "px";
    tip.style.top = y + "px";
  }

  function hideTip() { $("chartTip").classList.add("hidden"); }

  /* ============================================================
     工具函数
     ============================================================ */

  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function formatSize(bytes) {
    var n = Number(bytes) || 0;
    if (n <= 0) { return "-"; }
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? String(Math.round(n)) : n.toFixed(1)) + " " + units[i];
  }

  function formatDate(iso) {
    if (!iso) { return "-"; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return String(iso).slice(0, 16).replace("T", " "); }
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function shortDate(dateStr) {
    var s = String(dateStr || "");
    return s.length >= 10 ? s.slice(5) : s;
  }

  function formatNumber(n) {
    return String(Number(n) || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function niceCeil(v) {
    if (v <= 0) { return 1; }
    var pow = Math.pow(10, Math.floor(Math.log10(v)));
    var m = v / pow;
    var nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
    return nice * pow;
  }

  function pad2(n) { return n < 10 ? "0" + n : String(n); }

  var toastTimer = 0;
  function toast(msg, type) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast" + (type ? " " + type : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.add("hidden"); }, 3200);
  }

  var confirmResolve = null;

  function confirmDialog(title, text) {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmMask").classList.remove("hidden");
    return new Promise(function (resolve) { confirmResolve = resolve; });
  }

  function closeConfirm(result) {
    $("confirmMask").classList.add("hidden");
    if (confirmResolve) {
      var resolve = confirmResolve;
      confirmResolve = null;
      resolve(result);
    }
  }

  /* ============================================================
     标签页切换
     ============================================================ */

  /** 切换到指定标签页（同时刷新该页数据） */
  function switchTab(name) {
    $$(".tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    $$(".panel").forEach(function (p) { p.classList.remove("active"); });
    $("panel-" + name).classList.add("active");
    if (name === "content") { loadSite(); }
    else if (name === "media") { loadMedia(); }
    else if (name === "release") { loadVersions(); }
    else if (name === "stats") { loadStats(); }
  }

  function bindEvents() {
    $("loginForm").addEventListener("submit", function (e) { e.preventDefault(); doLogin(); });
    $("togglePwd").addEventListener("click", function () {
      var input = $("password");
      input.type = input.type === "password" ? "text" : "password";
    });
    $("logoutBtn").addEventListener("click", logout);

    $$(".tab").forEach(function (tab) {
      tab.addEventListener("click", function () { switchTab(tab.dataset.tab); });
    });

    // 内容管理
    $("saveSiteBtn").addEventListener("click", saveSite);
    $$("[data-add]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.dataset.add;
        var box = document.querySelector('[data-editor="' + kind + '"]');
        if (!box) { return; }
        var empty = box.querySelector(".list-empty");
        if (empty) { empty.remove(); }
        var count = $$(".list-row", box).length;
        box.appendChild(buildRow(kind, SCHEMAS[kind], {}, count));
      });
    });

    // 媒体库
    $("uploadImageBtn").addEventListener("click", function () { uploadMediaFile("image"); });
    $("uploadVideoBtn").addEventListener("click", function () { uploadMediaFile("video"); });
    $("refreshMediaBtn").addEventListener("click", loadMedia);
    $("mediaUploadInput").addEventListener("change", function (e) {
      var f = e.target.files && e.target.files[0];
      if (f) { uploadMedia(pendingUploadKind(), f); }
      e.target.value = "";
    });

    // 媒体选择弹窗
    $("pickerUploadBtn").addEventListener("click", function () { $("pickerFileInput").click(); });
    $("pickerFileInput").addEventListener("change", function (e) {
      var f = e.target.files && e.target.files[0];
      if (f) { pickerUpload(f); }
      e.target.value = "";
    });
    $("pickerCancel").addEventListener("click", closePicker);
    $("pickerConfirm").addEventListener("click", confirmPicker);
    $("pickerMask").addEventListener("click", function (e) {
      if (e.target === $("pickerMask")) { closePicker(); }
    });

    // 确认对话框
    $("confirmOk").addEventListener("click", function () { closeConfirm(true); });
    $("confirmCancel").addEventListener("click", function () { closeConfirm(false); });
    $("confirmMask").addEventListener("click", function (e) {
      if (e.target === $("confirmMask")) { closeConfirm(false); }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") { return; }
      if (!$("pickerMask").classList.contains("hidden")) { closePicker(); }
      else if (!$("confirmMask").classList.contains("hidden")) { closeConfirm(false); }
    });

    // 版本发布
    $("releaseFile").addEventListener("change", function () {
      var f = $("releaseFile").files && $("releaseFile").files[0];
      $("releaseFileName").textContent = f ? f.name : "选择安装包文件";
    });
    var dropLabel = $("releaseFile").parentElement;
    ["dragover", "dragenter"].forEach(function (evName) {
      dropLabel.addEventListener(evName, function (e) {
        e.preventDefault();
        dropLabel.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (evName) {
      dropLabel.addEventListener(evName, function (e) {
        e.preventDefault();
        dropLabel.classList.remove("dragover");
      });
    });
    dropLabel.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) {
        var dt = new DataTransfer();
        dt.items.add(files[0]);
        $("releaseFile").files = dt.files;
        $("releaseFileName").textContent = files[0].name;
      }
    });
    $("releaseForm").addEventListener("submit", function (e) { e.preventDefault(); publishVersion(); });

    // 媒体库上传按钮需要先记录目标分类
    $("chartBox").addEventListener("mousemove", onChartMove);
    $("chartBox").addEventListener("mouseleave", hideTip);

    var resizeTimer = 0;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (!$("panel-stats").classList.contains("active")) { return; }
        if (state.trend.length) { drawTrendChart(); }
      }, 180);
    });
  }

  var uploadKind = "image";

  function uploadMediaFile(kind) {
    uploadKind = kind;
    var input = $("mediaUploadInput");
    input.accept = kind === "image" ? "image/*" : "video/*";
    input.click();
  }

  function pendingUploadKind() { return uploadKind; }

  /* ============================================================
     启动
     ============================================================ */

  function init() {
    bindEvents();
    initStaticMediaFields();
    if (token) { showMain(); } else { showLogin(); }
    $("password").focus();
  }

  init();
})();
