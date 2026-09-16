// ContextPack — Web UI app logic
// 기존 POST /analyze API를 그대로 사용. backend 로직 수정 없음.

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var qs = function (sel, root) { return (root || document).querySelector(sel); };
  var qsa = function (sel, root) { return (root || document).querySelectorAll(sel); };

  var DOM = {
    role: $('role'),
    task: $('task'),
    buildBtn: $('buildBtn'),
    demoBtn: $('demoBtn'),
    pipelinePanel: $('pipelinePanel'),
    statusPanel: $('statusPanel'),
    statusTitle: $('statusTitle'),
    statusMetrics: $('statusMetrics'),
    elapsedTime: $('elapsedTime'),
    errorPanel: $('errorPanel'),
    errorBody: $('errorBody'),
    errorRetryBtn: $('errorRetryBtn'),
    resultPanel: $('resultPanel'),
    resultMetrics: $('resultMetrics'),
    qualitySignals: $('qualitySignals'),
    resultBody: $('resultBody'),
    copyBtn: $('copyBtn'),
    againBtn: $('againBtn'),
    toast: $('toast'),
  };

  var DEMO_ROLE = '3주 만에 프로젝트에 복귀한 백엔드 개발자';
  var DEMO_TASK = '결제 모듈의 부분환불 기능 수정';

  var SECTION_HEADINGS = [
    '[TASK]',
    '[MUST KNOW]',
    '[CONSTRAINTS]',
    '[USEFUL IF SPACE ALLOWS]',
    '[UNRESOLVED CONFLICTS]',
    '[VERIFY BEFORE USE]',
    '[DO NOT ASSUME]',
    '[SOURCE MAP]',
  ];

  var SECTION_RE = new RegExp(
    '^(?:' + SECTION_HEADINGS.map(function (h) { return h.replace(/^\[|\]$/g, ''); }).join('|') + ')\\]',
    'i'
  );

  var SOURCE_BADGES = [
    { name: 'GitHub', re: /\bgithub\b|pr-#?\d+|pull request|diff|src\/|payment_service\.py|refund_service\.py/i },
    { name: 'Jira', re: /\bjira\b|pay-\d+|issue|ticket|story|epic/i },
    { name: 'Slack', re: /\bslack\b|payment-eng|#payments|channel|message|timestamp/i },
    { name: 'Notion', re: /\bnotion\b|아카이브|문서|가이드|아키텍처|정책|요약/i },
  ];

  function isActive(el) {
    return !el.hidden;
  }

  function hideAllPanels() {
    DOM.pipelinePanel.hidden = true;
    DOM.statusPanel.hidden = true;
    DOM.errorPanel.hidden = true;
    DOM.resultPanel.hidden = true;
  }

  function showPanel(el) {
    hideAllPanels();
    el.hidden = false;
  }

  // ---- Demo loader ----

  DOM.demoBtn.addEventListener('click', function () {
    DOM.role.value = DEMO_ROLE;
    DOM.task.value = DEMO_TASK;
    DOM.role.focus();
  });

  // ---- Build ----

  DOM.buildBtn.addEventListener('click', function () {
    runBuild();
  });

  DOM.role.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      DOM.task.focus();
    }
  });

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (isActive(DOM.statusPanel)) return;
      runBuild();
    }
  });

  // ---- Copy & Again ----

  DOM.copyBtn.addEventListener('click', function () {
    var text = extractHandoffText();
    copyToClipboard(text);
  });

  DOM.againBtn.addEventListener('click', function () {
    hideAllPanels();
    DOM.pipelinePanel.hidden = false;
    DOM.buildBtn.disabled = false;
    DOM.role.value = '';
    DOM.task.value = '';
    DOM.role.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  DOM.errorRetryBtn.addEventListener('click', function () {
    hideAllPanels();
    DOM.buildBtn.disabled = false;
    runBuild();
  });

  // ---- Core ----

  function runBuild() {
    var role = DOM.role.value.trim();
    var task = DOM.task.value.trim();

    if (!role) { flashField(DOM.role, 'Role을 입력해주세요.'); return; }
    if (!task) { flashField(DOM.task, 'Task를 입력해주세요.'); return; }

    hideAllPanels();
    showPanel(DOM.statusPanel);
    DOM.statusTitle.innerHTML = 'Building Context Pack<span class="dots"></span>';
    DOM.statusMetrics.innerHTML = '';
    DOM.elapsedTime.textContent = '00:00';
    DOM.buildBtn.disabled = true;

    // stages 표시: dim 상태로 시작 (완료 표시 절대 아님)
    var dots = qsa('.stage-dot', DOM.statusPanel);
    for (var i = 0; i < dots.length; i++) {
      dots[i].style.background = 'var(--border)';
    }

    var start = Date.now();
    var timer = setInterval(updateElapsed, 250);

    var body = JSON.stringify({ role: role, task: task });

    fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
    })
      .then(function (res) { return res.json().then(function (data) { return { status: res.status, data: data }; }); })
      .then(function (result) {
        clearInterval(timer);
        DOM.buildBtn.disabled = false;

        if (result.status >= 200 && result.status < 300 && result.data && result.data.success) {
          renderResult(result.data);
          showPanel(DOM.resultPanel);
        } else {
          var detail = result.data ? (result.data.detail || JSON.stringify(result.data)) : '알 수 없는 응답';
          showError('HTTP ' + result.status, detail);
        }
      })
      .catch(function (err) {
        clearInterval(timer);
        DOM.buildBtn.disabled = false;
        var msg = err && err.message ? err.message : '요청 실패';
        showError('Network error', msg);
      });

    function updateElapsed() {
      var sec = Math.floor((Date.now() - start) / 1000);
      DOM.elapsedTime.textContent = formatTime(sec);
    }
  }

  function renderResult(data) {
    var handoff = data.handoff || '';
    var trace = data.trace || {};

    var dur = data.duration_sec != null ? data.duration_sec : 0;
    var toolCalls = trace.mcp_tool_calls != null ? trace.mcp_tool_calls : 0;
    var skillUsed = !!trace.skill_used;

    DOM.resultMetrics.innerHTML =
      '<div class="metric"><span class="metric-label">Duration</span><span class="metric-value">' + dur.toFixed(1) + ' sec</span></div>' +
      '<div class="metric"><span class="metric-label">Tool Calls</span><span class="metric-value">' + toolCalls + '</span></div>' +
      '<div class="metric"><span class="metric-label">ContextPack Skill</span><span class="metric-value ' + (skillUsed ? 'ok' : '') + '">' + (skillUsed ? 'Active' : 'Not used') + '</span></div>';

    // quality signals: handoff 섹션 존재 기반, 신규 판단 아님
    var conflictText = extractSection(handoff, 'UNRESOLVED CONFLICTS');
    var staleText = extractSection(handoff, 'VERIFY BEFORE USE');
    var missingText = extractSection(handoff, 'DO NOT ASSUME');

    var signals = [];
    if (hasContent(conflictText)) signals.push({ type: 'conflict', label: 'CONFLICT' });
    if (hasContent(staleText)) signals.push({ type: 'stale', label: 'VERIFY' });
    if (hasContent(missingText)) signals.push({ type: 'missing', label: 'MISSING' });

    DOM.qualitySignals.innerHTML = signals.length
      ? signals.map(function (s) {
          return '<span class="signal-badge ' + s.type + '"><span class="dot"></span>' + s.label + '</span>';
        }).join('')
      : '';

    var parsed = parseHandoff(handoff);
    DOM.resultBody.innerHTML = renderSections(parsed);
  }

  function renderSections(parsed) {
    var html = '';
    for (var i = 0; i < parsed.length; i++) {
      var s = parsed[i];
      var cls = sectionClass(s.heading);
      html +=
        '<div class="handoff-section ' + cls + '">' +
          '<div class="section-head">' + escapeHTML(s.heading) + '</div>' +
          '<div class="section-body">' + escapeHTML(s.content) + '</div>' +
        '</div>';
    }
    if (!parsed.length) {
      html = '<p style="color:var(--text-muted);">Handoff Context가 비어 있습니다.</p>';
    }
    return html;
  }

  // ---- Handoff 파싱 ----

  function parseHandoff(text) {
    if (!text) return [];

    var sections = [];
    var lines = text.replace(/\r\n/g, '\n').split('\n');
    var current = null;
    var buf = [];

    function flush() {
      if (current) {
        sections.push({
          heading: current,
          content: buf.join('\n').replace(/^[\n]+/, '').replace(/[\n]+$/, ''),
        });
      }
      buf = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var m = line.match(SECTION_RE);
      if (m) {
        flush();
        current = m[0].replace(/\s+$/, '') + ']';
      } else {
        buf.push(line);
      }
    }
    flush();
    return sections;
  }

  function sectionClass(heading) {
    var h = heading.toUpperCase();
    if (h.indexOf('UNRESOLVED CONFLICTS') !== -1) return 'conflict';
    if (h.indexOf('VERIFY BEFORE USE') !== -1) return 'verify';
    if (h.indexOf('DO NOT ASSUME') !== -1) return 'missing';
    return 'plain';
  }

  function extractSection(text, heading) {
    var idx = text.indexOf('[' + heading + ']');
    if (idx === -1) return '';
    var rest = text.substring(idx + heading.length + 1);
    var nextIdx = -1;
    for (var i = 0; i < SECTION_HEADINGS.length; i++) {
      var h = SECTION_HEADINGS[i];
      if (h === heading) continue;
      var pos = rest.indexOf('[' + h + ']');
      if (pos !== -1 && (nextIdx === -1 || pos < nextIdx)) nextIdx = pos;
    }
    return nextIdx === -1 ? rest : rest.substring(0, nextIdx);
  }

  function hasContent(s) {
    return s && s.replace(/^\s*/, '').length > 0;
  }

  function extractHandoffText() {
    var secs = qsa('.handoff-section', DOM.resultBody);
    var parts = [];
    for (var i = 0; i < secs.length; i++) {
      var head = secs[i].querySelector('.section-head').textContent;
      var bodyEl = secs[i].querySelector('.section-body');
      var bodyText = bodyEl ? bodyEl.textContent : '';
      parts.push('[' + head + ']\n' + bodyText);
    }
    return parts.join('\n');
  }

  // ---- Source map badge 렌더링 (SOURCE MAP 섹션 내부) ----
  // [SOURCE MAP] 섹션을 일반 섹션으로 렌더링하되, 내용에서 실제 등장한 source만 badge로 표시.

  function renderSourceMap(srcContent) {
    var present = [];
    var lower = srcContent.toLowerCase();
    for (var i = 0; i < SOURCE_BADGES.length; i++) {
      if (SOURCE_BADGES[i].re.test(srcContent)) {
        present.push(SOURCE_BADGES[i].name);
      }
    }
    var unique = present.filter(function (v, i, a) { return a.indexOf(v) === i; });

    var html =
      '<div class="source-map">' +
        '<div class="source-map-head">Source Map — 원본 출처</div>' +
        '<div class="source-map-body">';

    if (unique.length) {
      html +=
        '<div style="margin-bottom:8px; font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.04em;">References</div>' +
        '<div style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px;">' +
          unique.map(function (n) {
            return '<span style="font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; padding:2px 8px; border-radius:4px; background:var(--bg-elev); border:1px solid var(--border); color:var(--text-muted);">' + escapeHTML(n) + '</span>';
          }).join('') +
        '</div>';
    }

    html +=
        '<div class="source-row">' +
          '<span class="src-badge">Raw</span>' +
          '<span class="src-content">' + escapeHTML(srcContent) + '</span>' +
        '</div>' +
      '</div></div>';

    return html;
  }

  // ---- Helpers ----

  function escapeHTML(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatTime(sec) {
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return ''
      + (m < 10 ? '0' : '') + m
      + ':'
      + (s < 10 ? '0' : '') + s;
  }

  function flashField(el, msg) {
    el.style.borderColor = 'var(--warn)';
    el.setAttribute('aria-invalid', 'true');
    setTimeout(function () {
      el.style.borderColor = '';
      el.removeAttribute('aria-invalid');
    }, 1200);
    el.focus();
  }

  function showError(title, detail) {
    DOM.errorBody.textContent = '[' + title + ']\n' + String(detail);
    showPanel(DOM.errorPanel);
  }

  function copyToClipboard(text) {
    if (!text) { toast('복사할 내용이 없습니다.'); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        toast('Handoff Context를 클립보드에 복사했습니다.');
      }, function () { fallbackCopy(text); });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
      toast('Handoff Context를 클립보드에 복사했습니다.');
    } catch (e) {
      toast('복사에 실패했습니다. 수동으로 선택해 복사해주세요.');
    }
    document.body.removeChild(ta);
  }

  var toastTimer = null;
  function toast(msg) {
    if (!DOM.toast) {
      DOM.toast = document.createElement('div');
      DOM.toast.className = 'toast';
      document.body.appendChild(DOM.toast);
    }
    DOM.toast.textContent = msg;
    DOM.toast.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { DOM.toast.classList.remove('show'); }, 2200);
  }

  // ---- result body 렌더링 시 [SOURCE MAP] 특별 처리 ----
  // parseHandoff는 [SOURCE MAP]을 일반 섹션으로 파싱하므로,
  // renderSections에서SOURCE MAP 섹션만 별도 렌더링하도록 훅.

  function renderSections(parsed) {
    var html = '';
    for (var i = 0; i < parsed.length; i++) {
      var s = parsed[i];
      if (s.heading.toUpperCase() === '[SOURCE MAP]') {
        html += renderSourceMap(s.content);
      } else {
        var cls = sectionClass(s.heading);
        html +=
          '<div class="handoff-section ' + cls + '">' +
            '<div class="section-head">' + escapeHTML(s.heading) + '</div>' +
            '<div class="section-body">' + escapeHTML(s.content) + '</div>' +
          '</div>';
      }
    }
    if (!parsed.length) {
      html = '<p style="color:var(--text-muted);">Handoff Context가 비어 있습니다.</p>';
    }
    return html;
  }

  // 초기화: 파이프라인 패널 표시, 데모 입력 필드 포커스
  DOM.pipelinePanel.hidden = false;
  DOM.role.focus();
})();
