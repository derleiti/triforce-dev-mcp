/* ============================================================
   NOVA AI FRONTEND — JS v6.3
   12 Themes (dark/light), WP-Theme-Sync, Claude.ai Chat,
   Keyboard Shortcuts, Model Loader, Video Polling, Global Theme Picker
   ============================================================ */
(function () {
  'use strict';

  const API   = window.novaAiConfig?.apiBase || '/wp-json/nova-ai/v1';
  const NONCE = () => window.novaAiConfig?.nonce || null;
  const nonceHeader = () => { var n = NONCE(); return n ? {'X-WP-Nonce': n} : {}; };
  const DEBUG = false;
  // Prefer quota-backed chat models; first available entry becomes the UI default.
  const DEFAULT_CHAT_MODELS = [
    'mistral/mistral-small-latest',
    'groq/llama-3.1-8b-instant',
    'openrouter/inclusionai/ling-3.0-flash:free',
  ];
  const MODEL_KIND_ORDER = ['chat','vision','media_image','media_video','audio','ocr','embedding','code','reasoning'];
  const PROVIDER_ORDER = {
    chat: ['openai','anthropic','gemini','mistral','groq','cerebras','cohere','openrouter','ollama','cloudflare','github','together','fireworks','other'],
    vision: ['openai','anthropic','gemini','mistral','cohere','openrouter','ollama','cloudflare','github','together','fireworks','other'],
    media_image: ['openai','gemini','cloudflare','openrouter','together','fireworks','replicate','huggingface','other'],
    media_video: ['openai','gemini','cloudflare','openrouter','replicate','other'],
    audio: ['openai','gemini','groq','mistral','cloudflare','other'],
    ocr: ['mistral','cohere','openai','gemini','other'],
    embedding: ['openai','cohere','mistral','gemini','cloudflare','fireworks','other'],
    code: ['openai','anthropic','mistral','ollama','groq','openrouter','github','other'],
    reasoning: ['openai','anthropic','gemini','ollama','groq','mistral','cerebras','cohere','openrouter','other'],
  };

  function providerRank(kind, provider) {
    const order = PROVIDER_ORDER[kind] || PROVIDER_ORDER.chat;
    const p = (provider || 'other').toLowerCase();
    const idx = order.indexOf(p);
    return idx >= 0 ? idx : 99;
  }


  function selectPreferredChatModel(selectEl) {
    for (const modelId of DEFAULT_CHAT_MODELS) {
      const option = selectEl.querySelector(`option[value="${modelId}"]`);
      if (option) {
        option.selected = true;
        return modelId;
      }
    }
    return '';
  }

  const PROVIDER_LABELS = {
    openai: 'OpenAI', anthropic: 'Anthropic', gemini: 'Google Gemini',
    mistral: 'Mistral AI', groq: 'Groq', cerebras: 'Cerebras',
    cohere: 'Cohere', kimi: 'Kimi / Moonshot AI', openrouter: 'OpenRouter',
    ollama: 'Ollama (local)', cloudflare: 'Cloudflare Workers AI',
    github: 'GitHub Models', together: 'Together AI', fireworks: 'Fireworks AI',
    replicate: 'Replicate', huggingface: 'Hugging Face', other: 'Other providers',
  };

  function providerLabel(provider) {
    const key = (provider || 'other').toLowerCase();
    return PROVIDER_LABELS[key] || key.replace(/(^|[-_])([a-z])/g, (_, sep, char) => (sep ? ' ' : '') + char.toUpperCase());
  }

  function modelLabel(m) {
    return m.name || m.id || m.model || '';
  }

  function modelIdentity(m) {
    const provider = (m.provider || 'other').toLowerCase();
    let id = String(m.id || m.model || m.name || '').toLowerCase();
    if (id.startsWith(provider + '/')) id = id.slice(provider.length + 1);
    const routeMatch = id.match(/:(batch|free|thinking|fastest|preferred|cheapest)$/);
    const route = routeMatch ? routeMatch[0] : '';
    let base = route ? id.slice(0, -route.length) : id;
    base = base.replace(/-(20\d{2}-\d{2}-\d{2}|20\d{6}|20\d{4}|\d{4})$/, '');
    base = base.replace(/-(latest|preview)$/, '');
    return provider + '|' + base + route;
  }

  function modelPreference(m) {
    const id = String(m.id || m.model || m.name || '').toLowerCase().replace(/:(batch|free|thinking|fastest|preferred|cheapest)$/, '');
    if (/-latest$/.test(id)) return 0;
    if (!/-(preview|20\d{2}-\d{2}-\d{2}|20\d{6}|20\d{4}|\d{4})$/.test(id)) return 1;
    if (/-preview$/.test(id)) return 2;
    return 3;
  }

  function dedupeModels(models) {
    const unique = new Map();
    models.forEach(m => {
      const key = modelIdentity(m);
      const current = unique.get(key);
      const score = modelPreference(m);
      const currentScore = current ? modelPreference(current) : Number.POSITIVE_INFINITY;
      if (!current || score < currentScore ||
          (score === currentScore && modelLabel(m).localeCompare(modelLabel(current)) > 0)) {
        unique.set(key, m);
      }
    });
    return Array.from(unique.values());
  }

  function normalizeCategories(m) {
    const cats = Array.isArray(m.categories) ? m.categories.slice() : [];
    if (!cats.length) {
      if (m.media_video) cats.push('media_video');
      if (m.media_image || m.image_gen) cats.push('media_image');
      if (m.audio) cats.push('audio');
      if (m.ocr) cats.push('ocr');
      if (m.embedding) cats.push('embedding');
      if (m.code) cats.push('code');
      if (m.reasoning) cats.push('reasoning');
      if (m.vision) cats.push('vision');
      if (m.chat !== false && !m.media_image && !m.media_video && !m.embedding) cats.push('chat');
    }
    return cats.filter((cat, idx) => MODEL_KIND_ORDER.includes(cat) && cats.indexOf(cat) === idx);
  }

  function sortModelGroups(groups, kind) {
    return Object.entries(groups).sort(([a],[b]) => {
      const ar = providerRank(kind, a);
      const br = providerRank(kind, b);
      if (ar !== br) return ar - br;
      return providerLabel(a).localeCompare(providerLabel(b));
    });
  }

  function appendModelGroups(selectEl, list, kind) {
    const groups = {};
    dedupeModels(list).forEach(m => {
      const p = (m.provider || 'other').toLowerCase();
      (groups[p] = groups[p] || []).push(m);
    });
    selectEl.innerHTML = '';
    sortModelGroups(groups, kind).forEach(([prov, ms]) => {
      const og = document.createElement('optgroup');
      og.label = providerLabel(prov);
      ms.sort((a,b) => modelLabel(a).localeCompare(modelLabel(b))).forEach(m => {
        const o = document.createElement('option');
        o.value = m.id || m.model || m.name;
        o.textContent = modelLabel(m);
        og.appendChild(o);
      });
      selectEl.appendChild(og);
    });
  }

  // ── Themes ──────────────────────────────────────────────────
  const DARK_THEMES  = ['dark-github','dark-dracula','dark-monokai','dark-nord','dark-solarized','dark-onedark'];
  const LIGHT_THEMES = ['light-clean','light-warm','light-contrast','light-solarized','light-paper','light-mint'];
  const THEME_LABELS = {
    'auto':'Follow site theme',
    'dark-github':'GitHub Dark','dark-dracula':'Dracula','dark-monokai':'Monokai',
    'dark-nord':'Nord','dark-solarized':'Solarized Dark','dark-onedark':'One Dark',
    'light-clean':'Clean Light','light-warm':'Warm','light-contrast':'High Contrast',
    'light-solarized':'Solarized Light','light-paper':'Paper','light-mint':'Mint',
  };
  const THEME_SWATCHES = {
    'auto':'linear-gradient(135deg,var(--accent-blue,#3aa0ff),var(--accent-green,#44d19a))',
    'dark-github':'#58a6ff','dark-dracula':'#bd93f9','dark-monokai':'#66d9e8',
    'dark-nord':'#88c0d0','dark-solarized':'#268bd2','dark-onedark':'#61afef',
    'light-clean':'#0969da','light-warm':'#c45c00','light-contrast':'#0000cc',
    'light-solarized':'#268bd2','light-paper':'#5050d0','light-mint':'#0a7c3e',
  };
  let currentTheme = null;

  function detectMode() { return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
  function rndTheme(mode) {
    const pool = mode === 'light' ? LIGHT_THEMES : DARK_THEMES;
    return pool[Math.floor(Math.random() * pool.length)];
  }
  function applyTheme(theme, persist = true) {
    const resolved = THEME_LABELS[theme] ? theme : 'auto';
    currentTheme = resolved;
    if (persist) localStorage.setItem('nova-theme', resolved);
    document.querySelectorAll('.nova-ai-shell,.nova-downloads-shell').forEach(el => {
      el.setAttribute('data-nova-theme', resolved);
    });
    document.querySelectorAll('.nova-theme-option').forEach(o => o.classList.toggle('active', o.dataset.theme === resolved));
    document.querySelectorAll('[data-nova-theme-label]').forEach(el => el.textContent = THEME_LABELS[resolved] || 'Theme');
  }
  function initTheme() {
    const explicit = localStorage.getItem('nova-theme-explicit') === '1';
    const saved = explicit ? localStorage.getItem('nova-theme') : null;
    // Legacy builds persisted a random theme without recording user intent.
    // Migrate those values to site inheritance once; future explicit choices
    // carry nova-theme-explicit=1 and are preserved.
    if (!explicit) localStorage.removeItem('nova-theme');
    applyTheme(saved && THEME_LABELS[saved] ? saved : 'auto', false);
    // The default/auto theme inherits WordPress CSS variables live. Explicit
    // Nova themes remain explicit and are never randomized on a site mode change.
    new MutationObserver(() => {
      if (currentTheme === 'auto') applyTheme('auto', false);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-theme-profile'] });
  }

  // ── Theme-Picker ─────────────────────────────────────────────
  function buildThemePicker(containerOrPicker) {
    const picker = containerOrPicker.classList && containerOrPicker.classList.contains('nova-theme-picker')
      ? containerOrPicker
      : containerOrPicker.querySelector && containerOrPicker.querySelector('.nova-theme-picker');
    if (!picker || picker.dataset.built) return;
    picker.dataset.built = '1';
    const btn = picker.querySelector('.nova-theme-btn');
    const dd  = picker.querySelector('.nova-theme-dropdown');
    if (!dd) return;
    const autoHeader = document.createElement('div');
    autoHeader.className = 'nova-theme-dropdown-header';
    autoHeader.textContent = 'Site';
    dd.appendChild(autoHeader);
    const autoOption = document.createElement('div');
    autoOption.className = 'nova-theme-option' + (currentTheme === 'auto' ? ' active' : '');
    autoOption.dataset.theme = 'auto';
    autoOption.innerHTML = '<span class="nova-theme-swatch" style="background:linear-gradient(135deg,var(--accent-blue,#3aa0ff),var(--accent-green,#44d19a))"></span>Follow site theme';
    autoOption.addEventListener('click', () => { localStorage.removeItem('nova-theme-explicit'); applyTheme('auto'); dd.classList.remove('open'); });
    dd.appendChild(autoOption);

    ['dark','light'].forEach(mode => {
      const h = document.createElement('div');
      h.className = 'nova-theme-dropdown-header';
      h.textContent = mode === 'dark' ? '🌙 Dark' : '☀️ Light';
      dd.appendChild(h);
      (mode === 'dark' ? DARK_THEMES : LIGHT_THEMES).forEach(t => {
        const o = document.createElement('div');
        o.className = 'nova-theme-option' + (t === currentTheme ? ' active' : '');
        o.dataset.theme = t;
        o.innerHTML = `<span class="nova-theme-swatch" style="background:${THEME_SWATCHES[t]}"></span>${THEME_LABELS[t]}`;
        o.addEventListener('click', () => { localStorage.setItem('nova-theme-explicit', '1'); applyTheme(t); dd.classList.remove('open'); });
        dd.appendChild(o);
      });
    });
    if (btn) btn.addEventListener('click', e => { e.stopPropagation(); dd.classList.toggle('open'); });
    document.addEventListener('click', () => dd.classList.remove('open'));
  }

  // ── Tab-Navigation ───────────────────────────────────────────
  function initTabs(container, tabSel, panelSel) {
    const tabs   = container.querySelectorAll(tabSel);
    const panels = container.querySelectorAll(panelSel);
    tabs.forEach(tab => tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const target = container.querySelector('#' + tab.dataset.tab);
      if (target) target.classList.add('active');
    }));
  }

  // ── Model-Loader ─────────────────────────────────────────────
  async function loadModels(container) {
    try {
      const resp = await fetch(API + '/models', { headers: nonceHeader() });
      if (!resp.ok) return;
      const data = await resp.json();
      const models = Array.isArray(data.models) ? data.models : (Array.isArray(data) ? data : []);
      const kinds = {};
      MODEL_KIND_ORDER.forEach(k => { kinds[k] = []; });
      models.forEach(m => {
        const cats = normalizeCategories(m);
        cats.forEach(t => {
          (kinds[t] || kinds.chat).push(m);
        });  // cats.forEach
      });
      container.querySelectorAll('[data-model]').forEach(sel => {
        const list = kinds[sel.dataset.model] || [];
        sel.innerHTML = '';
        if (!list.length) { sel.innerHTML = '<option value="">— no models —</option>'; return; }
        appendModelGroups(sel, list, sel.dataset.model);
        if (sel.dataset.model === 'chat') selectPreferredChatModel(sel);
      });
    } catch(e) { DEBUG && console.warn('[Nova] Model load failed:', e); }
  }

  // ── Chat ─────────────────────────────────────────────────────
  function initChat(container) {
    const history  = container.querySelector('.nova-chat-history');
    const prompt   = container.querySelector('.nova-chat-prompt');
    const sendBtn  = container.querySelector('.nova-chat-send');
    const modelSel = container.querySelector('.nova-chat-model');
    const sysSel   = container.querySelector('.nova-chat-system');
    if (!history || !prompt || !sendBtn) return;
    let messages = [], inputHistory = [], histIdx = -1, busy = false;

    async function sendMessage() {
      const text = prompt.value.trim();
      if (!text || busy) return;
      busy = true; sendBtn.disabled = true;
      inputHistory.unshift(text); if (inputHistory.length > 50) inputHistory.pop(); histIdx = -1;
      prompt.value = ''; prompt.style.height = 'auto';
      appendMsg('user', text);
      const typingEl = appendTyping();
      messages.push({ role: 'user', content: text });
      try {
        const resp = await fetch(API + '/chat', {
          method: 'POST',
          headers: Object.assign({'Content-Type': 'application/json'}, nonceHeader()),
          body: JSON.stringify({ model: modelSel?.value||'', messages: (sysSel?.value?.trim() ? [{role:'system',content:sysSel.value.trim()},...messages] : messages), stream: false }),
        });
        const data = await resp.json();
        typingEl.remove();
        if (!resp.ok || data.error) {
          appendMsg('assistant', '⚠️ Error: ' + (data.message||data.error||data.detail||JSON.stringify(data)), true);
          messages.pop();
        } else {
          let reply = data.content || data.response || data.message || data.text || JSON.stringify(data);
          if (Array.isArray(data.sources) && data.sources.length) {
            reply += '\n\n**Sources**\n' + data.sources.map(s => '- [' + (s.title || s.url || 'Source') + '](' + s.url + ')').join('\n');
          }
          messages.push({ role: 'assistant', content: reply });
          appendMsg('assistant', reply);
          container.dispatchEvent(new CustomEvent('nova:chat-result', { bubbles:true, detail:data }));
          if (messages.length > 80) messages = messages.slice(-80);
        }
      } catch(e) { typingEl.remove(); appendMsg('assistant', '⚠️ Connection error: '+e.message, true); messages.pop(); }
      busy = false; sendBtn.disabled = false; prompt.focus();
    }

    function appendMsg(role, content, isError=false) {
      const wrap   = document.createElement('div'); wrap.className = 'nova-msg ' + role;
      const bubble = document.createElement('div');
      bubble.className = 'nova-msg-bubble' + (isError ? ' error' : '');
      if (role === 'assistant') { bubble.classList.add('nova-md'); bubble.innerHTML = renderMarkdown(content); }
      else bubble.textContent = content;
      const meta = document.createElement('div'); meta.className = 'nova-msg-meta';
      meta.textContent = new Date().toLocaleTimeString('de-DE', {hour:'2-digit',minute:'2-digit'});
      wrap.appendChild(bubble); wrap.appendChild(meta); history.appendChild(wrap);
      history.scrollTop = history.scrollHeight; return wrap;
    }
    function appendTyping() {
      const el = document.createElement('div'); el.className = 'nova-msg assistant';
      el.innerHTML = '<div class="nova-typing"><span></span><span></span><span></span></div>';
      history.appendChild(el); history.scrollTop = history.scrollHeight; return el;
    }

    prompt.addEventListener('keydown', e => {
      if ((e.ctrlKey||e.metaKey) && e.key==='Enter') { e.preventDefault(); sendMessage(); return; }
      if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); return; }
      if (e.key==='ArrowUp' && prompt.value==='') {
        e.preventDefault();
        if (histIdx < inputHistory.length-1) { histIdx++; prompt.value = inputHistory[histIdx]; }
        return;
      }
      if (e.key==='ArrowDown') {
        e.preventDefault();
        if (histIdx > 0) { histIdx--; prompt.value = inputHistory[histIdx]; }
        else { histIdx = -1; prompt.value = ''; }
        return;
      }
      if (e.key==='Escape') { prompt.value = ''; histIdx = -1; }
    });
    prompt.addEventListener('input', () => {
      prompt.style.height = 'auto';
      prompt.style.height = Math.min(prompt.scrollHeight, 200) + 'px';
    });
    sendBtn.addEventListener('click', sendMessage);
  }

  // ── Chat-Extras (Clear, System-Toggle, Ctrl+K) ───────────────
  function initChatExtras(container) {
    const clearBtn = container.querySelector('.nova-chat-clear');
    const showSys  = container.querySelector('.nova-system-show');
    const sysGroup = container.querySelector('.nova-system-group');
    const hideSys  = container.querySelector('.nova-system-toggle');
    const history  = container.querySelector('.nova-chat-history');
    const welcome  = container.querySelector('.nova-chat-welcome');

    if (clearBtn) clearBtn.addEventListener('click', () => {
      history?.querySelectorAll('.nova-msg').forEach(el => el.remove());
      if (welcome) welcome.style.display = '';
    });
    if (showSys && sysGroup) showSys.addEventListener('click', () => {
      sysGroup.classList.remove('nova-hidden'); showSys.classList.add('nova-hidden');
    });
    if (hideSys && sysGroup) hideSys.addEventListener('click', () => {
      sysGroup.classList.add('nova-hidden'); showSys?.classList.remove('nova-hidden');
    });
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey||e.metaKey) && e.key==='k') { e.preventDefault(); clearBtn?.click(); }
    });
    if (history && welcome) {
      new MutationObserver(() => {
        if (history.querySelector('.nova-msg')) welcome.style.display = 'none';
      }).observe(history, { childList: true });
    }
  }

  // ── Markdown ─────────────────────────────────────────────────
  function renderMarkdown(text) {
    if (!text) return '';
    // BUG-FIX 2026-03-11: protect code blocks from nl->br conversion
    var codeBlocks = [];
    var s = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, function(_,lang,code) {
      var slot = '\x00CODEBLOCK' + codeBlocks.length + '\x00';
      codeBlocks.push('<pre><code class="lang-'+lang+'">'+code.trim()+'</code></pre>');
      return slot;
    });
    s = s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>').replace(/^## (.+)$/gm, '<h2>$1</h2>').replace(/^# (.+)$/gm, '<h1>$1</h1>')
      .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
      .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
      .replace(/\[(.+?)\]\((https?:\/\/[^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // FIX: group consecutive <li> into a single <ul>
    s = s.replace(/(<li>.*?<\/li>\s*)+/gs, function(m) { return '<ul>' + m + '</ul>'; });
    // FIX: block-aware paragraph wrapping
    var _blockRe = /^(<h[1-6]>|<ul>|<\/ul>|<li>|<\/li>|<blockquote>|<\/blockquote>|<pre>|<\/pre>)/;
    var _lines = s.split('\n'), _result = [], _para = [];
    _lines.forEach(function(line) {
      if (_blockRe.test(line.trim())) {
        if (_para.length) { _result.push('<p>' + _para.join('<br>') + '</p>'); _para = []; }
        _result.push(line);
      } else if (line.trim() === '') {
        if (_para.length) { _result.push('<p>' + _para.join('<br>') + '</p>'); _para = []; }
      } else { _para.push(line); }
    });
    if (_para.length) _result.push('<p>' + _para.join('<br>') + '</p>');
    s = _result.join('\n');
    // Re-inject code blocks unmodified
    codeBlocks.forEach(function(block, i) { s = s.split('\x00CODEBLOCK'+i+'\x00').join(block); });
    return s;
  }

  // ── Vision ──────────────────────────────────────────────────────────────────
  function initVision(container) {
    const btn      = container.querySelector('.nova-vision-btn');
    const imgUrl   = container.querySelector('.nova-vision-url');
    const fileInput= container.querySelector('.nova-vision-file');
    const task     = container.querySelector('.nova-vision-task');
    const model    = container.querySelector('.nova-vision-model');
    const out      = container.querySelector('.nova-vision-output');
    const preview  = container.querySelector('.nova-vision-preview');
    if (!btn || !out) return;

    // Live-Vorschau bei Dateiauswahl
    if (fileInput) {
      fileInput.addEventListener('change', () => {
        const f = fileInput.files[0];
        if (!f) return;
        if (preview) {
          const objUrl = URL.createObjectURL(f);
          preview.innerHTML = `<img src="${objUrl}" alt="Vorschau" style="max-width:220px;max-height:160px;border-radius:8px;margin-top:6px;border:1px solid #333;">`;
        }
        if (imgUrl) imgUrl.value = ''; // URL leeren wenn Datei gewählt
      });
    }
    if (imgUrl) imgUrl.addEventListener('input', () => {
      if (preview) preview.innerHTML = '';
      if (fileInput) fileInput.value = '';
    });

    btn.addEventListener('click', async () => {
      const url    = imgUrl?.value.trim() || '';
      const file   = fileInput?.files?.[0] || null;
      const prompt = task?.value.trim() || 'Describe this image in detail.';
      const mdl    = model?.value || '';
      if (!url && !file) { setOutput(out, '⚠️ Enter an image URL or upload a file.', 'error'); return; }
      btn.disabled = true; setOutput(out, '⏳ Analysiere…', '');
      try {
        let json, httpOk = true;
        if (file) {
          // Multipart-Upload — temporär, keine dauerhafte Speicherung
          const fd = new FormData();
          fd.append('model', mdl);
          fd.append('prompt', prompt);
          fd.append('image_file', file, file.name);
          const resp = await fetch(API + '/vision-upload', {
            method: 'POST', headers: nonceHeader(), body: fd
          });
          httpOk = resp.ok;
          try {
            json = await resp.json();
          } catch(_je) {
            // Backend war kurz offline (Neustart) — HTML statt JSON erhalten
            json = { ok: false, error: 'Server temporarily unavailable. Please try again.' };
            httpOk = false;
          }
        } else {
          // URL-basiert
          const resp = await fetch(API + '/vision', {
            method: 'POST',
            headers: Object.assign({'Content-Type': 'application/json'}, nonceHeader()),
            body: JSON.stringify({ model: mdl, prompt, image_url: url })
          });
          httpOk = resp.ok;
          json = await resp.json();
        }
        // Robuste Fehler-Extraktion — verhindert [object Object]
        const safeErr = (v) => {
          if (!v) return null;
          if (typeof v === 'string') return v;
          const inner = v?.message || v?.detail || v?.error;
          return typeof inner === 'string' ? inner : (inner ? JSON.stringify(inner) : JSON.stringify(v));
        };
        if (!httpOk || json.ok === false) {
          const errStr = safeErr(json.message) || safeErr(json.error) || safeErr(json.detail) || safeErr(json) || 'Unknown error';
          setOutput(out, '❌ ' + errStr, 'error');
          return;
        }
        // Backend gibt {"ok":true,"mode":"vision","raw":{...}} zurück
        const raw = json.raw || json;
        const text = raw.response || raw.content || raw.text ||
                     (typeof raw.result === 'string' ? raw.result : null) ||
                     raw.candidates?.[0]?.content?.parts?.[0]?.text ||
                     (typeof raw === 'string' ? raw : null);
        if (text) setOutput(out, renderMd(text), 'success');
        else setOutput(out, '<pre style="font-size:11px;text-align:left">' + JSON.stringify(raw, null, 2) + '</pre>', 'success');
      } catch (e) { setOutput(out, '🔌 Connection error: ' + e.message, 'error'); }
      finally {
        btn.disabled = false;
        if (preview) preview.innerHTML = '';
        if (fileInput) fileInput.value = '';
      }
    });
  }

  // ── Media ────────────────────────────────────────────────────
  function initMedia(container) {
    const subBild  = container.querySelector('[data-subtab="bild"]');
    const subVideo = container.querySelector('[data-subtab="video"]');
    const panelBild  = container.querySelector('#nova-media-bild');
    const panelVideo = container.querySelector('#nova-media-video');
    function activateSub(mode) {
      subBild?.classList.toggle('active', mode==='bild');
      subVideo?.classList.toggle('active', mode==='video');
      panelBild?.classList.toggle('active', mode==='bild');
      panelBild?.classList.toggle('nova-hidden', mode!=='bild');
      panelVideo?.classList.toggle('active', mode==='video');
      panelVideo?.classList.toggle('nova-hidden', mode!=='video');
    }
    subBild?.addEventListener('click', () => activateSub('bild'));
    subVideo?.addEventListener('click', () => activateSub('video'));

    // BILD
    const imgBtn    = container.querySelector('.nova-img-btn');
    const imgModel  = container.querySelector('.nova-img-model');
    const imgPrompt = container.querySelector('.nova-img-prompt');
    const imgCount  = container.querySelector('.nova-img-count');
    const imgSize   = container.querySelector('.nova-img-size');
    const imgOut    = container.querySelector('.nova-img-output');
    // === Aspect / Resolution Maps ===
    const IMG_RES = {
      '1:1':  ['512x512','768x768','1024x1024'],
      '16:9': ['640x360','1280x720','1920x1080'],
      '4:3':  ['640x480','800x600','1024x768'],
      '3:4':  ['480x640','600x800','768x1024'],
      '9:16': ['360x640','480x854','720x1280','1080x1920'],
    };
    const VID_RES = {
      '16:9': ['640x360','1280x720','1920x1080'],
      '9:16': ['360x640','720x1280','1080x1920'],
    };
    const imgAspect = container.querySelector('.nova-img-aspect');
    function fillImgRes() {
      if (!imgAspect || !imgSize) return;
      const ar = imgAspect.value, opts = IMG_RES[ar] || [];
      imgSize.innerHTML = opts.map((r,i)=>`<option value="${r}"${i===0?' selected':''}>${r.replace('x','×')}</option>`).join('');
    }
    fillImgRes();
    imgAspect?.addEventListener('change', fillImgRes);
    const imgProg   = container.querySelector('.nova-img-progress');
    if (imgBtn && imgOut) imgBtn.addEventListener('click', async () => {
      const prompt = imgPrompt?.value.trim()||'';
      if (!prompt) { setOutput(imgOut,'Error: Enter a prompt.','error'); return; }
      imgBtn.disabled = true; imgProg?.classList.add('active');
      imgOut.querySelectorAll('.nova-img-result,.nova-img-wrap').forEach(i=>i.remove());
      setOutput(imgOut,'⏳ Generating image…','');
      try {
        const resp = await fetch(API+'/media/image', {method:'POST',headers:Object.assign({'Content-Type':'application/json'},nonceHeader()),
          body:JSON.stringify({model:imgModel?.value||'',prompt,n:parseInt(imgCount?.value||'1'),size:imgSize?.value||'1024x1024'})});
        const json = await resp.json();
        imgProg?.classList.remove('active');
        // Robuste Fehler-Extraktion — verhindert [object Object]
        const safeErrImg = (v) => { if (!v) return null; if (typeof v==='string') return v; const i=v?.message||v?.detail||v?.error; return typeof i==='string'?i:(i?JSON.stringify(i):JSON.stringify(v)); };
        if (!resp.ok || json.ok===false || json.error) {
          setOutput(imgOut,'Error: '+(safeErrImg(json.message)||safeErrImg(json.error)||safeErrImg(json.detail)||safeErrImg(json)||'Unknown'),'error');
        } else {
          setOutput(imgOut,'','success');
          // Extrahiere Bild-URLs auch aus verschachteltem result (CF Workers AI Format)
          const extractB64 = (b64) => { try { const d=JSON.parse(atob(b64)); return d?.result?.image||d?.image||null; } catch { return null; } };
          const res = json.result || json;
          const rawImgs = res.images || res.data || (res.url?[{url:res.url}]:null) || (json.url?[{url:json.url}]:[]);
          // FIX 2026-04-28: support all image-gen response shapes across providers:
          //  - object form  : [{url:...}], [{b64_json:...}], [{b64:...}]   (OpenAI / Replicate / older CF)
          //  - string form  : ["data:image/png;base64,..."]                (Cloudflare Flux 2 / @cf/black-forest-labs/*)
          //  - bare b64     : ["iVBORw0KG..."]                             (some Workers-AI variants)
          const urls = rawImgs.map(i => {
            if (typeof i === 'string') return i;
            if (!i) return null;
            return i.url || extractB64(i.b64_json||i.b64||'') || i.b64_json || i.b64 || null;
          }).filter(Boolean);
          // Detect MIME from data-URL prefix or base64 magic bytes (first chars after `;base64,`)
          const detectExt = (s) => {
            if (typeof s !== 'string') return 'png';
            if (s.indexOf('data:image/jpeg')===0 || s.indexOf(';base64,/9j/')>=0) return 'jpg';
            if (s.indexOf('data:image/webp')===0 || s.indexOf(';base64,UklGR')>=0) return 'webp';
            if (s.indexOf('data:image/gif')===0  || s.indexOf(';base64,R0lGOD')>=0) return 'gif';
            return 'png';
          };
          const extToMime = ext => ext==='jpg' ? 'image/jpeg' : ('image/'+ext);
          // Random 20-char filename generator
          const rndName = () => {
            const chars='abcdefghijklmnopqrstuvwxyz0123456789';
            let s=''; for(let i=0;i<20;i++) s+=chars[Math.floor(Math.random()*chars.length)];
            return s;
          };
          urls.forEach(src => {
            const wrap = document.createElement('div'); wrap.className='nova-img-wrap';
            wrap.style.cssText='display:inline-flex;flex-direction:column;align-items:center;gap:6px;margin:4px;';
            const isHttp = src.startsWith('http');
            const isData = src.startsWith('data:');
            const ext = detectExt(src);
            const mime = extToMime(ext);
            const img = document.createElement('img'); img.className='nova-img-result';
            // FIX 2026-04-28: avoid double-prefix when src is already a complete data:URL
            img.src = (isHttp || isData) ? src : 'data:'+mime+';base64,'+src;
            img.alt = prompt;
            img.style.cssText = 'max-width:100%;height:auto;border-radius:8px;display:block;';
            // Download button — uses correct extension matching detected format
            const dlBtn = document.createElement('a');
            const fname = rndName() + '.' + ext;
            dlBtn.textContent = '⬇ ' + fname;
            dlBtn.style.cssText='font-size:11px;color:#60a5fa;cursor:pointer;text-decoration:underline;word-break:break-all;';
            dlBtn.title = 'Download image';
            if (isHttp) {
              // For URL-based images: direct download attribute, fallback to new tab on CORS
              dlBtn.href = src; dlBtn.target='_blank'; dlBtn.download = fname; dlBtn.rel='noopener';
            } else {
              // For base64 / data:URL: create blob and download directly
              try {
                const b64 = isData ? src.split(',')[1] : src;
                const byteStr = atob(b64);
                const ab = new Uint8Array(byteStr.length);
                for(let i=0;i<byteStr.length;i++) ab[i]=byteStr.charCodeAt(i);
                const blob = new Blob([ab], {type: mime});
                dlBtn.href = URL.createObjectURL(blob);
                dlBtn.download = fname;
              } catch(ex) {
                dlBtn.href = isData ? src : 'data:'+mime+';base64,'+src;
                dlBtn.download = fname;
              }
            }
            wrap.appendChild(img); wrap.appendChild(dlBtn);
            imgOut.appendChild(wrap);
          });
          if (!urls.length) setOutput(imgOut,JSON.stringify(json,null,2),'success');
        }
      } catch(e) { imgProg?.classList.remove('active'); setOutput(imgOut,'Connection error: '+e.message,'error'); }
      imgBtn.disabled = false;
    });

    // VIDEO
    const vidBtn    = container.querySelector('.nova-vid-btn');
    const vidModel  = container.querySelector('.nova-vid-model');
    const vidPrompt = container.querySelector('.nova-vid-prompt');
    const vidDur    = container.querySelector('.nova-vid-duration');
    const vidRes    = container.querySelector('.nova-vid-resolution');
    const vidOut    = container.querySelector('.nova-vid-output');
    const vidAspect = container.querySelector('.nova-vid-aspect');
    function fillVidRes() {
      if (!vidAspect || !vidRes) return;
      const ar = vidAspect.value, opts = VID_RES[ar] || [];
      vidRes.innerHTML = opts.map((r,i)=>`<option value="${r}"${i===0?' selected':''}>${r.replace('x','×')}</option>`).join('');
    }
    fillVidRes();
    vidAspect?.addEventListener('change', fillVidRes);
    const vidProg   = container.querySelector('.nova-vid-progress');
    if (vidBtn && vidOut) vidBtn.addEventListener('click', async () => {
      const prompt = vidPrompt?.value.trim()||'';
      if (!prompt) { setOutput(vidOut,'Error: Enter a prompt.','error'); return; }
      vidBtn.disabled = true; vidProg?.classList.add('active');
      vidOut.querySelectorAll('.nova-video-result').forEach(v=>v.remove());
      setOutput(vidOut,'⏳ Starte Video-Generierung…','');
      try {
        const resp = await fetch(API+'/media/video', {method:'POST',headers:Object.assign({'Content-Type':'application/json'},nonceHeader()),
          body:JSON.stringify({model:vidModel?.value||'',prompt,seconds:parseInt(vidDur?.value||'8'),size:vidRes?.value||'1280x720'})});
        const json = await resp.json();
        const safeErrVid = (v) => { if (!v) return null; if (typeof v==='string') return v; const i=v?.message||v?.detail||v?.error; return typeof i==='string'?i:(i?JSON.stringify(i):JSON.stringify(v)); };
        if (!resp.ok||json.ok===false||json.error) {
          vidProg?.classList.remove('active'); vidBtn.disabled=false;
          setOutput(vidOut,'Error: '+(safeErrVid(json.message)||safeErrVid(json.error)||safeErrVid(json.detail)||safeErrVid(json)||'Unknown'),'error'); return;
        }
        const jobId = json.job_id||json.id||null;
        const _vidB64 = json.result?.data?.[0]?.b64_json || json.b64_json || null;
        const _vidCT  = json.result?.data?.[0]?.content_type || 'video/mp4';
        if (jobId) { setOutput(vidOut,'⏳ Video läuft… (Job: '+jobId+')',''); pollVideoJob(jobId,vidOut,vidProg,vidBtn); }
        else if (_vidB64) {
          const ab=Uint8Array.from(atob(_vidB64),c=>c.charCodeAt(0));
          const blob=new Blob([ab],{type:_vidCT});
          const burl=URL.createObjectURL(blob);
          vidProg?.classList.remove('active'); setOutput(vidOut,'✅ Fertig.','success');
          appendVideo(vidOut,burl); vidBtn.disabled=false;
        } else if (json.url||json.video_url) {
          vidProg?.classList.remove('active'); setOutput(vidOut,'✅ Fertig.','success');
          appendVideo(vidOut, json.url||json.video_url); vidBtn.disabled=false;
        } else { vidProg?.classList.remove('active'); setOutput(vidOut,JSON.stringify(json),'success'); vidBtn.disabled=false; }
      } catch(e) { vidProg?.classList.remove('active'); setOutput(vidOut,'Connection error: '+e.message,'error'); vidBtn.disabled=false; }
    });
  }

  async function pollVideoJob(jobId, out, prog, btn) {
    let attempts = 0;
    const timer = setInterval(async () => {
      if (++attempts > 60) { clearInterval(timer); prog?.classList.remove('active'); setOutput(out,'Error: Timeout.','error'); if(btn) btn.disabled=false; return; }
      try {
        const resp = await fetch(API+'/media/video/status/'+jobId, {headers:nonceHeader()});
        const json = await resp.json();
        const status = json.status||json.state||'';
        const pct = json.progress||json.percent||0;
        if (prog) { const bar=prog.querySelector('.nova-progress-bar'); if(bar&&pct) bar.style.width=pct+'%'; }
        if (status==='completed'||status==='done'||json.url||json.video_url) {
          clearInterval(timer); prog?.classList.remove('active'); if(btn) btn.disabled=false;
          setOutput(out,'✅ Video fertig.','success');
          const url=json.url||json.video_url; if(url) appendVideo(out,url); else setOutput(out,JSON.stringify(json),'success');
        } else if (status==='failed'||status==='error') {
          clearInterval(timer); prog?.classList.remove('active'); if(btn) btn.disabled=false;
          setOutput(out,'Error: '+(json.error||json.message||status),'error');
        } else setOutput(out,'⏳ Status: '+(status||'läuft')+(pct?' ('+pct+'%)':''),'');
      } catch(e) {}
    }, 5000);
  }

  // ── Downloads ─────────────────────────────────────────────────
  function initDownloads(container) {
    const table = container.querySelector('.nova-table tbody');
    if (!table) return;
    container.querySelectorAll('.nova-table th[data-sort]').forEach(th => {
      th.style.cursor='pointer';
      th.addEventListener('click', () => {
        const col=parseInt(th.dataset.sort), rows=Array.from(table.querySelectorAll('tr'));
        const asc = th.dataset.sortDir!=='asc'; th.dataset.sortDir=asc?'asc':'desc';
        rows.sort((a,b)=>(asc?1:-1)*(a.cells[col]?.textContent.trim()||'').localeCompare(b.cells[col]?.textContent.trim()||''));
        rows.forEach(r=>table.appendChild(r));
      });
    });
  }

  // ── Health ────────────────────────────────────────────────────
  async function updateHealth(container) {
    const badge = container.querySelector('.nova-health-badge,[data-health-badge]');
    if (!badge) return;
    try {
      const resp = await fetch(API + '/health', { headers: nonceHeader() });
      const json = await resp.json();
      if (json.ok||json.status==='ok'||json.status==='healthy') {
        badge.textContent='Backend: ok'; badge.className='nova-badge ok';
        const mb=container.querySelector('.nova-model-count,[data-model-count]');
        if(mb&&json.model_count) mb.textContent='Models: '+json.model_count;
      } else { badge.textContent='Backend: ⚠️'; badge.className='nova-badge warn'; }
    } catch { badge.textContent='Backend: offline'; badge.className='nova-badge err'; }
  }

  // ── Utility ───────────────────────────────────────────────────
  // Simple Markdown renderer (no external dep needed)
  function renderMd(text) {
    if (!text) return '';
    // FIX 2026-03-11: protect code blocks from nl→br conversion using placeholder array
    var blocks = [];
    var s = String(text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Extract fenced code blocks to placeholders BEFORE any other processing
    s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, function(_, lang, code) {
      var slot = '\x00RMDCB' + blocks.length + '\x00';
      blocks.push('<pre><code' + (lang ? ' class="lang-' + lang + '"' : '') + '>' + code.trim() + '</code></pre>');
      return slot;
    });
    s = s
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      .replace(/^[-*] (.+)$/gm, '<li>$1</li>');
    // wrap consecutive li elements
    s = s.replace(/(<li>.*?<\/li>\s*)+/gs, m => '<ul>' + m + '</ul>');
    // FIX: block-aware paragraph wrapping (mirrors renderMarkdown fix)
    var _bRe = /^(<\/?h[1-6]>|<\/?ul>|<\/?li>|<\/?blockquote>|<\/?pre>)/;
    var _ls = s.split('\n'), _res = [], _buf = [];
    _ls.forEach(function(line) {
      if (_bRe.test(line.trim())) {
        if (_buf.length) { _res.push('<p>' + _buf.join('<br>') + '</p>'); _buf = []; }
        _res.push(line);
      } else if (line.trim() === '') {
        if (_buf.length) { _res.push('<p>' + _buf.join('<br>') + '</p>'); _buf = []; }
      } else { _buf.push(line); }
    });
    if (_buf.length) _res.push('<p>' + _buf.join('<br>') + '</p>');
    s = _res.join('\n');
    // Re-inject code blocks unmodified
    blocks.forEach(function(block, i) { s = s.split('\x00RMDCB' + i + '\x00').join(block); });
    return s;
  }
  function setOutput(el, html, cls) {
    if (!el) return;
    const out = el.querySelector('.nova-output-text') || el;
    out.innerHTML = html;
    out.className = 'nova-output-text' + (cls?' '+cls:'');
  }
  function appendVideo(container, url) {
    const chars='abcdefghijklmnopqrstuvwxyz0123456789';
    let fname=''; for(let i=0;i<20;i++) fname+=chars[Math.floor(Math.random()*chars.length)];
    fname += '.mp4';
    const wrap = document.createElement('div');
    wrap.style.cssText='display:flex;flex-direction:column;align-items:center;gap:6px;margin:4px;';
    const video = document.createElement('video');
    video.className='nova-video-result'; video.controls=true; video.src=url;
    const dlBtn = document.createElement('a');
    dlBtn.href=url; dlBtn.download=fname; dlBtn.target='_blank';
    dlBtn.textContent='⬇ '+fname;
    dlBtn.style.cssText='font-size:11px;color:#60a5fa;cursor:pointer;text-decoration:underline;word-break:break-all;';
    dlBtn.title='Video herunterladen';
    wrap.appendChild(video); wrap.appendChild(dlBtn);
    container.appendChild(wrap);
  }

  // ── Init ──────────────────────────────────────────────────────
  // ── Account Panel ─────────────────────────────────────────────────────────
  async function initAccount(container) {
    const panel = container.querySelector('#nova-account-panel');
    if (!panel) return;
    // FIX 2026-03-11: Corrected tier map (free/paid only), added subscription management
    const TIER_COLORS = { free:'#64748b', paid:'#8b5cf6' };
    const TIER_ICONS  = { free:'\u{1F513}', paid:'\u2B50' };
    try {
      // Fetch account + subscription status in parallel
      const [accResp, subResp] = await Promise.all([
        fetch(API + '/account',      { headers: nonceHeader() }),
        fetch(API + '/subscription', { headers: nonceHeader() }).catch(() => null)
        // NOTE: /subscription route is optional — missing route returns null, handled below
      ]);
      const d   = await accResp.json();
      const sub = subResp && subResp.ok ? await subResp.json().catch(() => ({})) : {};
      if (!d.ok) throw new Error(d.error || 'Error');
      if (!d.logged_in) {
        panel.innerHTML = `
          <div style="text-align:center;padding:2rem">
            <div style="font-size:3rem;margin-bottom:1rem">\u{1F510}</div>
            <h3 style="color:#f1f5f9;margin-bottom:.5rem">Nicht angemeldet</h3>
            <p style="color:#94a3b8;margin-bottom:1.5rem">Melde dich an um dein Abo, gekaufte Apps und Downloads zu sehen.</p>
            <a href="${d.login_url||'https://ailinux.me/account'}" target="_blank"
               style="display:inline-block;padding:.75rem 2rem;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:white;border-radius:10px;text-decoration:none;font-weight:600">
              \u{1F511} Sign in / Register
            </a>
          </div>`;
        return;
      }
      const tier  = (d.tier||'free').toLowerCase() === 'free' ? 'free' : 'paid';
      const tclr  = TIER_COLORS[tier] || '#64748b';
      const tico  = TIER_ICONS[tier]  || '\u{1F513}';
      const ents  = Array.isArray(d.entitlements) ? d.entitlements : [];
      const dls   = Array.isArray(d.downloads)    ? d.downloads    : [];
      // BUG-FIX 2026-03-11: API returns sub.data.status (not sub.status) — normalize both shapes
      const subStatus = sub?.data?.status || sub.status || 'none';
      const subActive = subStatus === 'active';

      // Build subscription management section
      let subHTML = '';
      if (tier === 'paid' && subActive) {
        const _renewRaw = sub?.data?.renews_at || sub.renews_at || null;
        const renewDate = _renewRaw ? new Date(_renewRaw).toLocaleDateString() : '';
        subHTML = `
          <div style="background:rgba(0,0,0,.3);border:1px solid #2a2a3a;border-radius:12px;padding:1.25rem;margin-bottom:1.25rem">
            <h4 style="color:#f1f5f9;margin:0 0 .75rem;font-size:.95rem">\u{1F4B3} Subscription</h4>
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem">
              <div>
                <div style="color:#f1f5f9;font-weight:500">AILinux Pro — Active</div>
                ${renewDate ? `<div style="color:#64748b;font-size:.8rem">Renews: ${renewDate}</div>` : ''}
              </div>
              <button id="nova-sub-cancel-btn"
                style="padding:.4rem 1rem;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#ef4444;border-radius:8px;font-size:.8rem;cursor:pointer">
                Abo kündigen
              </button>
            </div>
          </div>`;
      } else if (tier === 'free') {
        subHTML = `
          <div style="background:rgba(59,130,246,.05);border:1px solid rgba(59,130,246,.2);border-radius:12px;padding:1.25rem;margin-bottom:1.25rem">
            <h4 style="color:#f1f5f9;margin:0 0 .75rem;font-size:.95rem">\u{1F4B3} Subscription</h4>
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.75rem">
              <div>
                <div style="color:#93c5fd;font-weight:500">Free — €0/month</div>
                <div style="color:#64748b;font-size:.8rem">57 models · Ollama + small models</div>
              </div>
              <a href="${d.shop_url||'https://ailinux.me/shop'}" target="_blank"
                 style="padding:.5rem 1.25rem;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:white;border-radius:8px;text-decoration:none;font-size:.85rem;font-weight:600">
                \u2B06\uFE0F Upgrade to Pro — €17.99/month
              </a>
            </div>
          </div>`;
      }

      const dlHTML = dls.length ? dls.map(f=>`
        <div style="display:flex;align-items:center;justify-content:space-between;padding:.75rem 1rem;background:rgba(0,0,0,.3);border:1px solid #2a2a3a;border-radius:8px;margin-bottom:.5rem">
          <div>
            <div style="color:#f1f5f9;font-weight:500;font-size:.9rem">${f.name||f.file||'Download'}</div>
            <div style="color:#64748b;font-size:.75rem">${f.type||''} ${f.size_formatted||''}</div>
          </div>
          ${f.url?`<a href="${f.url}" download style="padding:.4rem 1rem;background:#3b82f6;color:white;border-radius:6px;text-decoration:none;font-size:.8rem;font-weight:600">\u2193 Download</a>`:'<span style="color:#64748b;font-size:.8rem">Not available</span>'}
        </div>`).join('') : '<div style="color:#64748b;font-size:.875rem">No downloads available.</div>';
      const entHTML = ents.length ? `<div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem">${ents.map(e=>`<span style="padding:.25rem .75rem;background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);border-radius:20px;font-size:.75rem;color:#93c5fd">${e}</span>`).join('')}</div>` : '<div style="color:#64748b;font-size:.875rem">No additional permissions.</div>';

      panel.innerHTML = `
        <div style="max-width:600px;margin:0 auto">
          <div style="background:rgba(0,0,0,.3);border:1px solid #2a2a3a;border-radius:16px;padding:1.5rem;margin-bottom:1.25rem">
            <div style="display:flex;align-items:center;gap:1rem">
              <div style="width:60px;height:60px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.75rem">\u{1F464}</div>
              <div>
                <div style="color:#f1f5f9;font-weight:600;font-size:1.125rem">${d.display_name||d.email}</div>
                <div style="color:#94a3b8;font-size:.875rem">${d.email}</div>
                <div style="display:inline-block;margin-top:.25rem;padding:.2rem .75rem;background:${tclr}22;border:1px solid ${tclr}44;border-radius:20px;color:${tclr};font-size:.75rem;font-weight:600">${tico} ${tier.toUpperCase()}</div>
              </div>
              <div style="margin-left:auto">
                <a href="${d.account_url||'https://ailinux.me/account'}" target="_blank"
                   style="padding:.5rem 1rem;background:rgba(255,255,255,.05);border:1px solid #2a2a3a;color:#94a3b8;border-radius:8px;text-decoration:none;font-size:.8rem">\u2699\uFE0F Account</a>
              </div>
            </div>
          </div>
          ${subHTML}
          <div style="background:rgba(0,0,0,.3);border:1px solid #2a2a3a;border-radius:12px;padding:1.25rem;margin-bottom:1.25rem">
            <h4 style="color:#f1f5f9;margin:0 0 .75rem;font-size:.95rem">\u{1F381} Unlocked Apps & Features</h4>
            ${entHTML}
          </div>
          <div style="background:rgba(0,0,0,.3);border:1px solid #2a2a3a;border-radius:12px;padding:1.25rem">
            <h4 style="color:#f1f5f9;margin:0 0 .75rem;font-size:.95rem">\u{1F4E6} AILinux Client Downloads</h4>
            ${dlHTML}
          </div>
        </div>`;

      // Wire up cancel button
      const cancelBtn = panel.querySelector('#nova-sub-cancel-btn');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', async function() {
          if (!confirm('Cancel your subscription? Your Pro access will remain active until the end of the billing period.')) return;
          cancelBtn.disabled = true; cancelBtn.textContent = 'Cancelling…';
          try {
            const cr = await fetch(API + '/subscription/cancel', {
              method: 'POST', headers: nonceHeader()
            });
            const cd = await cr.json();
            if (cr.ok) {
              cancelBtn.style.background = 'rgba(34,197,94,.1)';
              cancelBtn.style.borderColor = 'rgba(34,197,94,.3)';
              cancelBtn.style.color = '#4ade80';
              cancelBtn.textContent = '\u2713 Cancellation confirmed';
            } else {
              cancelBtn.disabled = false;
              cancelBtn.textContent = 'Error: ' + (cd.error||cd.message||'Retry');
            }
          } catch(err) {
            cancelBtn.disabled = false; cancelBtn.textContent = 'Connection error';
          }
        });
      }
    } catch(e) {
      panel.innerHTML = `<div style="color:#ef4444;padding:1rem">\u274C Error loading: ${e.message}</div>`;
    }
  }


  // ── Try AILinux Guided Demo ───────────────────────────────────────────────
  function initGuidedDemo(root) {
    const shell = root.querySelector('.nova-ai-shell');
    if (!shell) return;
    const prompt = shell.querySelector('.nova-chat-prompt');
    const send = shell.querySelector('.nova-chat-send');
    const progress = root.querySelector('[data-demo-progress]');
    const kicker = root.querySelector('[data-demo-kicker]');
    const title = root.querySelector('[data-demo-title]');
    const description = root.querySelector('[data-demo-description]');
    const run = root.querySelector('[data-demo-run]');
    const skip = root.querySelector('[data-demo-skip]');
    const restart = root.querySelector('[data-demo-restart]');
    const status = root.querySelector('[data-demo-status]');
    const activityList = root.querySelector('[data-demo-activity]');
    const stack = root.querySelector('[data-demo-stack]');
    const complete = root.querySelector('[data-demo-complete]');
    const stepCard = root.querySelector('[data-demo-step-card]');
    const account = root.querySelector('[data-demo-account]');
    const storageKey = 'ailinux_try_demo_v1';

    const steps = [
      {
        title:'Talk to Nova',
        description:'Start with a normal model response. Nothing simulated.',
        action:'Ask Nova',
        prompt:'Explain in one sentence what TriForce does for an AILinux user.',
        validate:data => !!data?.answer && (data.activity||[]).some(a => a.type==='model' && a.status==='completed')
      },
      {
        title:'Research the web',
        description:'Ask Nova to research something current. This step completes only when TriForce reports a real web_search.',
        action:'Run web research',
        prompt:'Search the web for the latest public AILinux information on ailinux.me and show the sources.',
        validate:data => (data.activity||[]).some(a => a.name==='web_search' && a.status==='completed' && Number(a.result_count||0)>0)
      },
      {
        title:'Read a website',
        description:'Give Nova a URL. TriForce fetches the public page and Nova answers from what it actually read.',
        action:'Read ailinux.me',
        prompt:'Read https://ailinux.me/ and tell me in two sentences what a new user should try first.',
        validate:data => (data.activity||[]).some(a => a.name==='crawl_url' && a.status==='completed')
      },
      {
        title:'Understand the stack',
        description:'Inspect the verified activity trail from the previous steps and see how Browser → WordPress → TriForce → model/tools fits together.',
        action:'Show the stack',
        local:true
      },
      {
        title:'Continue with AILinux',
        description:'The guided layer now gets out of your way. Keep using this same live playground, or open an account for persistent access and clients.',
        action:'Finish tour',
        local:true
      }
    ];

    let state = { step:0, completed:[false,false,false,false,false], activity:[] };
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey)||'null');
      if (saved && Number.isInteger(saved.step) && Array.isArray(saved.completed)) {
        state = { step:Math.max(0,Math.min(5,saved.step)), completed:saved.completed.slice(0,5), activity:Array.isArray(saved.activity)?saved.activity.slice(-12):[] };
        while (state.completed.length < 5) state.completed.push(false);
      }
    } catch(_e) {}

    function emit(name, detail={}) {
      root.dispatchEvent(new CustomEvent('nova:demo-event', { bubbles:true, detail:Object.assign({name},detail) }));
    }
    function save() { try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch(_e) {} }
    function renderActivity() {
      if (!activityList) return;
      if (!state.activity.length) {
        activityList.innerHTML = '<span class="nova-guided-muted">Verified backend activity will appear here.</span>';
        return;
      }
      activityList.innerHTML = '';
      state.activity.slice(-8).forEach(a => {
        const row=document.createElement('div'); row.className='nova-guided-activity-row';
        const name=document.createElement('strong'); name.textContent=a.name||a.type||'activity';
        const meta=document.createElement('span');
        if (a.name==='web_search') meta.textContent=(a.result_count||0)+' sources';
        else if (a.name==='crawl_url') meta.textContent=a.url||'page fetched';
        else meta.textContent=a.status||'completed';
        row.append(name,meta); activityList.appendChild(row);
      });
    }
    function render() {
      const done = state.step >= steps.length;
      if (stepCard) stepCard.hidden = done;
      if (complete) complete.hidden = !done;
      if (progress) progress.textContent = done ? '5 / 5 ✓' : (state.step+1)+' / 5';
      if (!done) {
        const step=steps[state.step];
        if (kicker) kicker.textContent='STEP '+(state.step+1);
        if (title) title.textContent=step.title;
        if (description) description.textContent=step.description;
        if (run) run.textContent=step.action;
        if (status) status.textContent='Ready.';
        if (stack) stack.hidden = state.step < 3;
      } else if (stack) stack.hidden=false;
      renderActivity();
    }
    function advance(reason) {
      if (state.step >= steps.length) return;
      const idx=state.step;
      state.completed[idx]=true;
      state.step=Math.min(steps.length,state.step+1);
      save(); render(); emit(idx===0?'first_answer':idx===1?'research_completed':idx===2?'website_read':idx===4?'tutorial_completed':'tutorial_step_completed',{step:idx+1,reason});
    }
    function runStep() {
      if (state.step >= steps.length) return;
      const step=steps[state.step];
      if (step.local) {
        if (state.step===3 && stack) stack.hidden=false;
        advance('local_explanation');
        return;
      }
      if (!prompt || !send) return;
      prompt.value=step.prompt;
      prompt.dispatchEvent(new Event('input',{bubbles:true}));
      if (status) status.textContent='Running on the live backend…';
      send.click();
    }

    root.addEventListener('nova:chat-result', e => {
      const data=e.detail||{};
      const incoming=Array.isArray(data.activity)?data.activity:[];
      if (incoming.length) {
        state.activity=state.activity.concat(incoming.map(a => Object.assign({},a))).slice(-12);
        save(); renderActivity();
      }
      if (state.step < 3) {
        if (steps[state.step].validate(data)) advance('verified_backend_activity');
        else if (status) status.textContent='Response received, but this step needs the matching verified tool activity. Try the suggested action.';
      }
    });
    run?.addEventListener('click',runStep);
    skip?.addEventListener('click',() => advance('skipped'));
    restart?.addEventListener('click',() => {
      state={step:0,completed:[false,false,false,false,false],activity:[]}; save(); render(); emit('demo_restarted');
    });
    account?.addEventListener('click',() => emit('signup_clicked',{url:account.href}));
    if (state.step===0 && !sessionStorage.getItem(storageKey)) emit('demo_started');
    render();
  }


  function initShell(container) {
    buildThemePicker(container);
    initTabs(container, '.nova-tab', '.nova-panel');
    initChatExtras(container);
    initChat(container);
    initVision(container);
    initMedia(container);
    initAccount(container);
    updateHealth(container);
    loadModels(container);
  }
  function initDownloadsShell(container) {
    buildThemePicker(container);
    initTabs(container, '.nova-tab', '.nova-panel');
    initDownloads(container);
    initAccount(container);
  }

  // ── Nonce Refresh ──────────────────────────────────────────────────────────
  async function refreshNonce() {
    var url = window.novaAiConfig && window.novaAiConfig.nonceUrl;
    if (!url) return;
    try {
      var resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
      if (!resp.ok) return;
      var json = await resp.json();
      if (json.nonce && window.novaAiConfig) {
        window.novaAiConfig.nonce = json.nonce;
      }
    } catch (_e) { /* keep existing nonce on network error */ }
  }

  // ── Boot ───────────────────────────────────────────────────────────────────
  // FIX 2026-03-10: Cloudflare Rocket Loader / WP Page Cache may call
  // DOMContentLoaded callbacks in a scope where IIFE closures are lost.
  // Solution: detect ready state and use a single entry point (bootNova).
  // setInterval is moved INSIDE bootNova so it runs in the correct closure.

  // ── Discuss with AI (Article Chat) ──────────────────────────────────────────
  // Initialisiert den "Discuss with AI" Button auf Posts/Pages.
  // Lädt alle Chat-Modelle wie der AI Playground und nutzt /wp-json/nova-ai/v1/article-chat.

  function initDiscuss() {
    const btn    = document.getElementById('ai-discuss-btn');
    const panel  = document.getElementById('ai-discuss-panel');
    const closeEl = document.getElementById('ai-discuss-close');
    const input  = document.getElementById('ai-discuss-input');
    const sendEl = document.getElementById('ai-discuss-send');
    const output = document.getElementById('ai-discuss-output');
    const modelSel = document.getElementById('ai-model-select');

    if (!btn || !panel) return; // Not on a post/page with discuss feature

    const API = window.novaAiConfig && window.novaAiConfig.apiBase
      ? window.novaAiConfig.apiBase
      : '/wp-json/nova-ai/v1';

    const nonce = () => (window.novaAiConfig && window.novaAiConfig.nonce) || '';

    // ── Load chat models into modelSel ──────────────────────────────────────
    // In-memory cache damit Mobile nicht bei jedem Panel-Öffnen neu lädt
    let _modelsCache = null;

    async function loadDiscussModels() {
      if (!modelSel) return;
      // Zeige sofort Lade-Indikator
      if (modelSel.options.length <= 1) {
        const loadOpt = document.createElement('option');
        loadOpt.textContent = '⏳ Loading models…';
        loadOpt.disabled = true;
        modelSel.appendChild(loadOpt);
      }
      // Cache nutzen wenn vorhanden
      if (_modelsCache) {
        _populateModelSel(_modelsCache);
        return;
      }
      try {
        const resp = await fetch(API + '/models', { method: 'GET', headers: { 'Accept': 'application/json' } });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const all = data.models || data.data || (Array.isArray(data) ? data : []);
        _modelsCache = all; // Cache für nächstes Öffnen
        // Only chat models (not image/video gen)
        const chatModels = all.filter(m => {
          const cats = normalizeCategories(m);
          const isImg = m.media_image || cats.includes('media_image');
          const isVid = m.media_video || cats.includes('media_video');
          const isChat = m.chat !== false || cats.includes('chat') || cats.includes('vision');
          return isChat && !isImg && !isVid;
        });
        if (!chatModels.length) return;
        _populateModelSel(chatModels);
      } catch(e) { /* keep default */ }
    }

    function _populateModelSel(chatModels) {
      if (!modelSel) return;
      // Filter: nur Chat-Modelle
      const filtered = chatModels.filter(m => {
        const cats = normalizeCategories(m);
        const isImg = m.media_image || cats.includes('media_image');
        const isVid = m.media_video || cats.includes('media_video');
        return !isImg && !isVid;
      });
      if (!filtered.length) return;
      appendModelGroups(modelSel, filtered, 'chat');
      selectPreferredChatModel(modelSel);
    }

    // ── Panel open/close ─────────────────────────────────────────────────────
    function openPanel() {
      panel.classList.add('open');
      panel.setAttribute('aria-hidden', 'false');
      if (modelSel && modelSel.options.length <= 1) loadDiscussModels();
      if (input) input.focus();
    }
    function closePanel() {
      panel.classList.remove('open');
      panel.setAttribute('aria-hidden', 'true');
    }

    btn.addEventListener('click', () => panel.classList.contains('open') ? closePanel() : openPanel());
    if (closeEl) closeEl.addEventListener('click', closePanel);
    panel.addEventListener('click', e => { if (e.target === panel) closePanel(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && panel.classList.contains('open')) closePanel(); });

    // ── Chat history ─────────────────────────────────────────────────────────
    const history = [];
    const chatEl  = document.getElementById('ai-discuss-chat');

    function addMessage(role, text) {
      history.push({ role, content: text });
      if (!chatEl) return;
      const div = document.createElement('div');
      div.className = 'nov-msg nov-msg-' + role;
      if (role === 'assistant') { div.classList.add('nova-md'); div.innerHTML = renderMd(text); } else { div.textContent = text; }
      chatEl.appendChild(div);
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    // ── Send ─────────────────────────────────────────────────────────────────
    async function send() {
      const msg = input ? input.value.trim() : '';
      if (!msg) return;
      if (input) input.value = '';

      addMessage('user', msg);

      // Article context
      const context = (() => {
        const aiCtx = window.AIContext;
        if (aiCtx && aiCtx.contextPrompt) return aiCtx.contextPrompt;
        const title = document.querySelector('h1.entry-title, h1.post-title, h1');
        const body  = document.querySelector('.entry-content, .post-content, article');
        return (title ? title.textContent.trim() : '') + '\n' +
               (body  ? body.innerText.slice(0, 1500) : '');
      })();

      const model = (modelSel && modelSel.value) ||
                    'gemini/gemini-2.5-flash';

      const payload = {
        model,
        message: msg,
        context,
        history: history.slice(0, -1).slice(-10), // BUG-FIX 2026-03-11: addMessage already pushed current msg
      };

      if (output) { output.textContent = '⏳ …'; output.style.color = ''; }

      try {
        const resp = await fetch(API + '/article-chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-WP-Nonce': nonce(),
          },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
          const err = data?.error?.message || data?.detail || JSON.stringify(data);
          if (output) { output.textContent = '❌ ' + err; output.style.color = 'var(--error-color,#ef4444)'; }
          return;
        }
        let text = data?.content || data?.text ||
                     data?.choices?.[0]?.message?.content || '';
        if (Array.isArray(data?.sources) && data.sources.length) {
          text += '\n\n**Sources**\n' + data.sources.map(s => '- [' + (s.title || s.url || 'Source') + '](' + s.url + ')').join('\n');
        }
        addMessage('assistant', text);
        if (output) output.textContent = '';
      } catch(e) {
        if (output) { output.textContent = '❌ ' + e.message; output.style.color = 'var(--error-color,#ef4444)'; }
      }
    }

    if (sendEl) sendEl.addEventListener('click', send);
    if (input)  input.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') send();
    });
  }

  function bootNova() {
    refreshNonce();
    setInterval(refreshNonce, 10 * 60 * 1000);
    initTheme();
    document.querySelectorAll('.nova-ai-shell').forEach(initShell);
    document.querySelectorAll('[data-nova-guided-demo]').forEach(initGuidedDemo);
    document.querySelectorAll('.nova-downloads-shell').forEach(initDownloadsShell);
    initDiscuss();
    // Modelle sofort beim Page-Load im Hintergrund vorladen (nicht erst beim Panel-Öffnen)
    // Damit der native Android <select>-Picker bereits alle Optionen hat
    setTimeout(function() {
      const modelSel = document.getElementById('ai-model-select');
      if (modelSel) {
        const API = window.novaAiConfig && window.novaAiConfig.apiBase
          ? window.novaAiConfig.apiBase : '/wp-json/nova-ai/v1';
        fetch(API + '/models', { method: 'GET', headers: { 'Accept': 'application/json' } })
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (!data) return;
            const all = data.models || data.data || [];
            if (!all.length) return;
            // Cache setzen (wird von _populateModelSel in initDiscuss genutzt)
            // Direkt befüllen falls modelSel noch leer
            if (modelSel.options.length <= 1) {
              const chatModels = all.filter(m => {
                const cats = normalizeCategories(m);
                return !m.media_image && !m.media_video && !cats.includes('media_image') && !cats.includes('media_video') && !cats.includes('embedding');
              });
              appendModelGroups(modelSel, chatModels, 'chat');
              selectPreferredChatModel(modelSel);
            }
          }).catch(function(){});
      }
    }, 500); // 500ms nach DOMContentLoaded — nicht blockierend
    // Init global header theme pickers
    document.querySelectorAll('.nova-theme-picker').forEach(buildThemePicker);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootNova);
  } else {
    // Already ready (Rocket Loader deferred execution or script at page bottom)
    bootNova();
  }

})();
