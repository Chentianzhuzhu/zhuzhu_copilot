/* ============================================================
   用户反馈页交互
   纯原生实现，无框架无外部依赖；提交失败时保留已输入内容，不清空表单。
   与 site.js 同一套代码风格：IIFE + 严格模式 + 中文注释。
   ============================================================ */
(function () {
  'use strict';

  var form = document.getElementById('feedbackForm');
  if (!form) { return; }

  var content = document.getElementById('feedbackContent');
  var contact = document.getElementById('feedbackContact');
  var count = document.getElementById('fbCount');
  var hint = document.getElementById('fbHint');
  var errorBox = document.getElementById('feedbackError');
  var submitBtn = document.getElementById('feedbackSubmit');
  var submitText = document.getElementById('feedbackSubmitText');

  var successBox = document.getElementById('feedbackSuccess');
  var successId = document.getElementById('feedbackSuccessId');
  var limitBox = document.getElementById('feedbackLimit');
  var limitTitle = document.getElementById('feedbackLimitTitle');
  var limitDesc = document.getElementById('feedbackLimitDesc');

  var MIN = 5, MAX = 2000;

  /* 实时字数计数与校验提示 */
  function syncCount() {
    var len = content.value.length;
    count.textContent = String(len);
    if (len === 0) {
      hint.textContent = '至少 ' + MIN + ' 个字';
      hint.className = 'fb-hint';
    } else if (len < MIN) {
      hint.textContent = '还差 ' + (MIN - len) + ' 个字';
      hint.className = 'fb-hint is-warn';
    } else {
      hint.textContent = '已达可提交长度';
      hint.className = 'fb-hint is-ok';
    }
  }

  function setBusy(busy) {
    submitBtn.disabled = busy;
    submitBtn.classList.toggle('is-busy', busy);
    submitText.textContent = busy ? '提交中…' : '提交反馈';
  }

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }

  function hideMessages() {
    errorBox.hidden = true;
    successBox.hidden = true;
    limitBox.hidden = true;
  }

  content.addEventListener('input', syncCount);

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    hideMessages();

    var text = content.value.trim();
    if (text.length < MIN) {
      showError('反馈内容至少 ' + MIN + ' 个字，请补充得再具体一些。');
      content.focus();
      return;
    }
    if (text.length > MAX) {
      showError('反馈内容不能超过 ' + MAX + ' 字。');
      return;
    }

    setBusy(true);
    fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ content: text, contact: contact.value.trim() })
    }).then(function (res) {
      return res.text().then(function (raw) {
        var data = null;
        if (raw) { try { data = JSON.parse(raw); } catch (err) { data = null; } }
        return { status: res.status, data: data };
      });
    }).then(function (r) {
      setBusy(false);
      // 成功
      if (r.status === 200 && r.data && r.data.ok) {
        successId.textContent = '反馈编号 #' + r.data.id;
        successBox.hidden = false;
        form.hidden = true;
        successBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      // 限流（429）
      if (r.status === 429) {
        limitTitle.textContent = '今日反馈次数已达上限';
        limitDesc.textContent = (r.data && r.data.error ? r.data.error : '今天提交得太多啦') + '，明天再来吧。';
        limitBox.hidden = false;
        return;
      }
      // 其他错误
      var msg = (r.data && r.data.error) ? r.data.error : '提交失败，请稍后再试';
      showError(msg);
    }).catch(function () {
      setBusy(false);
      showError('网络异常，反馈未提交成功，请检查网络后重试。');
    });
  });

  syncCount();
})();
