/* Nova AI — Account Suite JS v2.0 — Management Suite Edition */
(function(){
'use strict';
const CFG=window.novaAccountConfig||{};
const API=CFG.apiBase||'/wp-json/nova-ai/v1';
const LOGIN_URL=CFG.loginUrl||'https://ailinux.me/account';

const TIER_LABELS={free:'FREE',pro:'PRO',enterprise:'ENTERPRISE',unlimited:'UNLIMITED',admin:'ADMIN',paid:'PAID'};
const TIER_CLASS={free:'nas-tier-free',pro:'nas-tier-pro',enterprise:'nas-tier-enterprise',unlimited:'nas-tier-unlimited',admin:'nas-tier-admin',paid:'nas-tier-paid'};
const TIER_FEATURES={
  free:['33+ local models (Ollama)','Chat & Vision analysis','Community Support','AILinux beta access'],
  paid:['617+ AI models (9 providers)','Media generation (image & video)','Priority Support','Full MCP tools','Unlimited API access'],
  pro:['617+ AI models (9 providers)','Media generation (image & video)','Priority Support','Full MCP tools','Unlimited API access'],
  enterprise:['All 617+ models','Full admin access','288+ MCP tools','System control','Vault & CLI-Agents','Dedicated Support'],
  admin:['All 617+ models','Full admin access','288+ MCP tools','System control','Vault & CLI-Agents','Dedicated Support'],
  unlimited:['Alles in Pro','Unbegrenzte Agents','Codebase Tools','Dedicated Support']
};
function tierLabel(t){return TIER_LABELS[t?.toLowerCase()]||t?.toUpperCase()||'FREE'}
function tierClass(t){return TIER_CLASS[t?.toLowerCase()]||'nas-tier-free'}
function tierFeatures(t){return TIER_FEATURES[t?.toLowerCase()]||TIER_FEATURES.free}
function isAdminTier(t){return ['enterprise','admin','unlimited'].includes(t?.toLowerCase())}
function isPaidTier(t){return ['paid','pro','enterprise','admin','unlimited'].includes(t?.toLowerCase())}

function wrap(el){return el?document.getElementById(el):null}
function html(id,h){const el=wrap(id);if(el)el.innerHTML=h}
function show(id){const el=wrap(id);if(el)el.style.display=''}
function hide(id){const el=wrap(id);if(el)el.style.display='none'}

document.querySelectorAll('#nova-account-suite,.nova-account-suite,[data-nova-account-suite]').forEach(function(root){
  if(root.dataset.nasInit)return; root.dataset.nasInit='1';
  const isLoggedIn=root.dataset.loggedIn==='1'||CFG.isLoggedIn;
  if(!isLoggedIn){
    const loginBtn=root.querySelector('.nas-login-btn');
    if(loginBtn){loginBtn.addEventListener('click',function(){window.location.href=LOGIN_URL+'?redirect='+encodeURIComponent(location.href)})}
    initAuthForms(root);
    return;
  }
  initDashboard(root);
});

function initAuthForms(root){
  const TRIFORCE=CFG.triforceApi||'https://api.ailinux.me/v1/auth';
  const WP_SYNC=CFG.wpLoginSync||'/wp-json/nova-ai/v1/auth/wp-login';
  const msgEl=root.querySelector('#nas-msg');
  function showAuthMsg(text,type){
    if(!msgEl)return;
    msgEl.textContent=text;msgEl.className='nas-msg '+(type||'error');
    msgEl.style.display='block';
    if(type==='ok')setTimeout(function(){msgEl.style.display='none'},3000);
  }
  function hideAuthMsg(){if(msgEl)msgEl.style.display='none'}
  function getTurnstileToken(container){
    const resp=container?.querySelector('[name="cf-turnstile-response"]');
    return resp?resp.value:'';
  }

  // Tab switching (login/register)
  root.querySelectorAll('.nas-tab').forEach(function(btn){
    btn.addEventListener('click',function(){
      root.querySelectorAll('.nas-tab').forEach(function(x){x.classList.remove('active')});
      btn.classList.add('active');
      const tab=btn.dataset.tab;
      root.querySelectorAll('.nas-form').forEach(function(f){f.style.display='none'});
      const f=root.querySelector('#nas-'+tab+'-form');
      if(f)f.style.display='block';
      hideAuthMsg();
    });
  });

  // Password toggles
  root.querySelectorAll('.nas-pw-toggle').forEach(function(btn){
    btn.addEventListener('click',function(){
      const inp=btn.closest('.nas-pw-wrap')?.querySelector('input');
      if(inp){inp.type=inp.type==='password'?'text':'password';btn.textContent=inp.type==='password'?'\ud83d\udc41':'\ud83d\ude48'}
    });
  });

  // ── LOGIN SUBMIT ──
  const loginForm=root.querySelector('#nas-login-form');
  if(loginForm){
    loginForm.addEventListener('submit',async function(e){
      e.preventDefault();
      const btn=root.querySelector('#nas-login-btn');
      const email=root.querySelector('#nas-email')?.value?.trim()||'';
      const pw=root.querySelector('#nas-pass')?.value||'';
      const cfToken=getTurnstileToken(loginForm);
      if(!email||!pw){showAuthMsg('Please fill in all fields.');return}
      if(!cfToken){showAuthMsg('Please complete the captcha.');return}
      btn.disabled=true;btn.textContent='…';hideAuthMsg();
      try{
        const r=await fetch(TRIFORCE+'/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email,password:pw,cf_turnstile_response:cfToken})});
        const d=await r.json();
        if(r.ok&&d.token){
          localStorage.setItem('ailinux_token',d.token);
          localStorage.setItem('ailinux_email',d.user_id||email);
          localStorage.setItem('ailinux_tier',d.tier||'free');
          localStorage.setItem('ailinux_client_id',d.client_id||'');
          showAuthMsg('Welcome! Loading dashboard…','ok');
          // FIX 2026-04-24: Full-Page-Navigation statt fetch.
          // Backend-Endpoint macht wp_set_auth_cookie() + wp_redirect() + exit.
          // fetch() folgt dem 302 transparent aber setzt den Set-Cookie-Header
          // im Redirect-Response nicht im Browser-Cookie-Jar (SameSite/CORS).
          // Native Navigation behandelt den 302+Cookie wie jede andere Nav.
          window.location.href = WP_SYNC
            + '?token='    + encodeURIComponent(d.token)
            + '&email='    + encodeURIComponent(d.user_id || email)
            + '&tier='     + encodeURIComponent(d.tier || 'free')
            + '&redirect=' + encodeURIComponent(location.href);
          return;
        }else{
          showAuthMsg(d.detail||d.message||'Login failed.');
          if(typeof turnstile!=='undefined')turnstile.reset();
        }
      }catch(err){
        showAuthMsg('Connection error. Please try again.');
        if(typeof turnstile!=='undefined')turnstile.reset();
      }
      btn.disabled=false;btn.textContent='Sign in';
    });
  }

  // ── REGISTER SUBMIT ──
  const regForm=root.querySelector('#nas-register-form');
  if(regForm){
    regForm.addEventListener('submit',async function(e){
      e.preventDefault();
      const btn=root.querySelector('#nas-reg-btn');
      const email=root.querySelector('#nas-reg-email')?.value?.trim()||'';
      const pw=root.querySelector('#nas-reg-pass')?.value||'';
      const name=root.querySelector('#nas-reg-name')?.value?.trim()||'';
      const code=root.querySelector('#nas-reg-code')?.value?.trim()||'';
      const cfToken=getTurnstileToken(regForm);
      if(!email||!pw){showAuthMsg('Please enter email and password.');return}
      if(pw.length<8){showAuthMsg('Password must be at least 8 characters.');return}
      if(!cfToken){showAuthMsg('Please complete the captcha.');return}
      btn.disabled=true;btn.textContent='…';hideAuthMsg();
      try{
        const body={email:email,password:pw,cf_turnstile_response:cfToken};
        if(name)body.name=name;
        if(code)body.invite_code=code;
        const r=await fetch(TRIFORCE+'/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const d=await r.json();
        if(r.ok&&(d.token||d.ok)){
          showAuthMsg('Account created! Signing in…','ok');
          if(d.token){
            localStorage.setItem('ailinux_token',d.token);
            localStorage.setItem('ailinux_email',d.user_id||email);
            localStorage.setItem('ailinux_tier',d.tier||'free');
            window.location.href = WP_SYNC
              + '?token='    + encodeURIComponent(d.token)
              + '&email='    + encodeURIComponent(d.user_id || email)
              + '&tier='     + encodeURIComponent(d.tier || 'free')
              + '&redirect=' + encodeURIComponent(location.href);
            return;
          }else{
            // Auto-login after registration
            root.querySelector('#nas-email').value=email;
            root.querySelector('#nas-pass').value=pw;
            root.querySelector('.nas-tab[data-tab="login"]')?.click();
            setTimeout(function(){root.querySelector('#nas-login-form')?.dispatchEvent(new Event('submit'))},300);
          }
        }else{
          showAuthMsg(d.detail||d.message||'Registration failed.');
          if(typeof turnstile!=='undefined')turnstile.reset();
        }
      }catch(err){
        showAuthMsg('Connection error. Please try again.');
        if(typeof turnstile!=='undefined')turnstile.reset();
      }
      btn.disabled=false;btn.textContent='Create Account';
    });
  }
}

function initDashboard(root){
  // Nav switching
  root.querySelectorAll('.nas-nav-item').forEach(function(btn){
    btn.addEventListener('click',function(){
      root.querySelectorAll('.nas-nav-item').forEach(function(x){x.classList.remove('active')});
      btn.classList.add('active');
      root.querySelectorAll('.nas-panel').forEach(function(p){p.classList.remove('active')});
      const panel=root.querySelector('#nas-panel-'+btn.dataset.panel);
      if(panel){panel.classList.add('active');loadPanel(btn.dataset.panel,root)}
    });
  });

  // ?view= URL-Parameter beim Seitenload auswerten
  (function(){
    var view = new URLSearchParams(location.search).get('view');
    // Mapping: view= -> data-panel Wert
    var MAP = {
      profile: 'overview', account: 'overview', overview: 'overview',
      subscription: 'subscription', abonnement: 'subscription',
      purchases: 'downloads', einkaufe: 'downloads', downloads: 'downloads'
    };
    var panelName = view ? (MAP[view.toLowerCase()] || view) : null;
    if (panelName) {
      var targetBtn = root.querySelector('[data-panel="' + panelName + '"]');
      if (targetBtn) {
        root.querySelectorAll('.nas-nav-item').forEach(function(x){x.classList.remove('active')});
        targetBtn.classList.add('active');
        root.querySelectorAll('.nas-panel').forEach(function(p){p.classList.remove('active')});
        var panel = root.querySelector('#nas-panel-' + panelName);
        if (panel) { panel.classList.add('active'); loadPanel(panelName, root); }
      }
    }
  })();

  // Logout
  const logoutBtn=root.querySelector('#nas-logout');
  if(logoutBtn){
    logoutBtn.addEventListener('click',async function(){
      try{await fetch(API+'/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-WP-Nonce':CFG.nonce||''}})}catch{}
      localStorage.removeItem('ailinux_token');
      localStorage.removeItem('ailinux_email');
      localStorage.removeItem('ailinux_tier');
      localStorage.removeItem('ailinux_client_id');
      location.href=LOGIN_URL;
    });
  }

  // Copy buttons
  root.querySelectorAll('.nas-copy-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      const text=btn.dataset.clipboard||(root.querySelector('#'+btn.dataset.target)?.textContent||'');
      navigator.clipboard?.writeText(text).then(function(){btn.textContent='✓';setTimeout(function(){btn.textContent='📋'},1500)}).catch(function(){});
    });
  });

  // Settings form
  const settingsForm=root.querySelector('#nas-settings-form');
  if(settingsForm){
    settingsForm.addEventListener('submit',async function(e){
      e.preventDefault();
      const nameInput=root.querySelector('#nas-set-name');
      const pwInput=root.querySelector('#nas-set-pw');
      const body={};
      if(nameInput?.value.trim())body.display_name=nameInput.value.trim();
      if(pwInput?.value&&pwInput.value.length>=8)body.new_password=pwInput.value;
      if(!Object.keys(body).length){showMsg(root,'#nas-settings-msg','Nothing to update','error');return}
      const r=await fetch(API+'/profile/update',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-WP-Nonce':CFG.nonce||''},body:JSON.stringify(body)}).catch(function(e){return{ok:false,json:function(){return{error:e.message}}}});
      const d=await r.json().catch(function(){return{}});
      showMsg(root,'#nas-settings-msg',d.ok?'Saved ✅':'Error: '+(d.error||'unbekannt'),d.ok?'ok':'error');
    });
  }

  // Password toggles (dashboard)
  root.querySelectorAll('.nas-pw-toggle').forEach(function(btn){
    btn.addEventListener('click',function(){
      const inp=btn.closest('.nas-pw-wrap')?.querySelector('input');
      if(inp){inp.type=inp.type==='password'?'text':'password';btn.textContent=inp.type==='password'?'👁':'🙈'}
    });
  });

  // Cancel subscription
  const cancelBtn=root.querySelector('#nas-cancel-sub-btn');
  if(cancelBtn){
    cancelBtn.addEventListener('click',async function(){
      if(!confirm('Cancel subscription?'))return;
      const r=await fetch(API+'/subscription/cancel',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-WP-Nonce':CFG.nonce||''}}).catch(function(){return{json:function(){return{ok:false}}}});
      const d=await r.json().catch(function(){return{ok:false}});
      if(d.ok){alert('Subscription cancelled successfully.');location.reload()}
      else{alert('Error: '+(d.error||'unbekannt'))}
    });
  }

  // Load initial data
  loadOverview(root);
}

function showMsg(root,selector,text,type){
  const el=root.querySelector(selector);
  if(!el)return;
  el.textContent=text;el.className='nas-msg '+(type||'err');
  el.style.display='block';
  setTimeout(function(){el.style.display='none'},4000);
}

const _loaded={};
function loadPanel(name,root){
  const key=name+'_'+(root.id||'r');
  if(_loaded[key])return; _loaded[key]=1;
  if(name==='subscription')loadSubscription(root);
  if(name==='downloads')loadDownloads(root);
  if(name==='admin')loadAdminOverview(root);
  if(name==='system')loadSystem(root);
  if(name==='agents')loadAgents(root);
  if(name==='mcp')loadMcpTools(root);
  if(name==='vault')loadVault(root);
  if(name==='logs')loadLogs(root);
}

function loadOverview(root){
  fetch(API+'/health',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
    const el=root.querySelector('#nas-backend-status');
    if(el)el.textContent=(d.status==='ok'||d.status==='healthy'||d.ok)?'✅ Online':'⚠ '+d.status;
  }).catch(function(){
    const el=root.querySelector('#nas-backend-status');
    if(el)el.textContent='⚠ Offline';
  });
  fetch(API+'/account',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}}).then(function(r){return r.json()}).then(function(d){
    if(!d.ok)return;
    const t=(d.tier||'free').toLowerCase();
    const lbl=tierLabel(t);
    const cls=tierClass(t);
    const cid=d.client_id||'—';
    const cidEl=root.querySelector('#nas-client-id-val');
    const apiCidEl=root.querySelector('#nas-api-client-id');
    if(cidEl)cidEl.textContent=cid;
    if(apiCidEl)apiCidEl.textContent=cid;
    const featsEl=root.querySelector('#nas-features-list');
    if(featsEl){
      const feats=tierFeatures(t);
      featsEl.innerHTML=feats.map(function(f){return '<span class="nas-feature-item">✓ '+f+'</span>'}).join('');
    }
    const tierCardEl=root.querySelector('.nas-card-tier .nas-card-value');
    if(tierCardEl)tierCardEl.innerHTML='<span class="nas-tier-badge '+cls+'">'+lbl+'</span>';
    if(t==='free'){const upEl=root.querySelector('.nas-upgrade-link');if(upEl)upEl.style.display='inline-block'}
    // Show admin nav items if admin tier
    if(isAdminTier(t)){
      root.querySelectorAll('[data-admin-only]').forEach(function(el){el.style.display=''});
    }
  }).catch(function(){});
}

async function loadSubscription(root){
  const loadEl=root.querySelector('#nas-sub-loading');
  const contentEl=root.querySelector('#nas-sub-content');
  try{
    const r=await fetch(API+'/subscription',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const data=d.data||{};
    const t=data.tier||'free';
    const sid=data.subscription_id||data.payment_ref||'';
    const statusEl=root.querySelector('#nas-sub-status');
    if(statusEl){
      statusEl.innerHTML=sid
        ?'<span class="status-active">● Active</span> · '+tierLabel(t)
        :'<span style="color:var(--nas-muted)">No active subscription</span>';
    }
    if(sid){const cancelSection=root.querySelector('#nas-cancel-section');if(cancelSection)cancelSection.style.display='block'}
    if(loadEl)loadEl.style.display='none';
    if(contentEl)contentEl.style.display='block';
  }catch(e){
    // Show server-rendered content even if API fails (manual tier users)
    if(loadEl)loadEl.style.display='none';
    if(contentEl)contentEl.style.display='block';
  }
}

async function loadDownloads(root){
  const loadEl=root.querySelector('#nas-dl-loading');
  const contentEl=root.querySelector('#nas-dl-content');
  const filesEl=root.querySelector('#nas-downloads-table');
  const purchasesEl=root.querySelector('#nas-purchases-list');
  try{
    const r=await fetch(API+'/purchases',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const files=d.downloads||[];
    const purchases=d.purchases||[];
    if(purchasesEl&&purchases.length){
      purchasesEl.innerHTML='<h3 style="margin-bottom:.75rem">Purchases</h3>'+purchases.map(function(p){var name=esc(p.item_name||p.name||p.id||'Purchase');var date=esc(p.purchased_at||p.date||'');var status=esc(p.status||'');return'<div class="nas-card" style="margin-bottom:.5rem"><strong>'+name+'</strong><div style="font-size:.8rem;color:var(--nas-muted)">'+(date||'License entitlement')+(status?' · '+status:'')+'</div></div>'}).join('');
    }
    if(filesEl){
      if(!files.length){
        filesEl.innerHTML='<div class="nas-loading-box">No downloads available</div>';
      }else{
        filesEl.innerHTML='<table class="nas-dl-table"><thead><tr><th>File</th><th>Type</th><th>Size</th><th>Modified</th><th>Download</th></tr></thead><tbody>'+files.map(function(f){var sz=fmtBytes(f.size||0);return'<tr><td>'+esc(f.name||'—')+'</td><td>'+esc((f.type||'').toUpperCase())+'</td><td>'+sz+'</td><td>'+esc(f.modified||'—')+'</td><td>'+(f.url?'<a href="'+esc(f.url)+'" download class="nas-dl-link">↓</a>':'—')+'</td></tr>'}).join('')+'</tbody></table>';
      }
    }
    if(loadEl)loadEl.style.display='none';
    if(contentEl)contentEl.style.display='block';
  }catch(e){
    if(loadEl)loadEl.innerHTML='<div style="color:var(--nas-muted)">Failed to load</div>';
  }
}

/* ── ADMIN PANELS ─────────────────────────────────────────────────────── */

async function loadAdminOverview(root){
  const el=root.querySelector('#nas-admin-overview');
  if(!el)return;
  try{
    const [health,agents]=await Promise.all([
      fetch(API+'/health',{credentials:'same-origin'}).then(r=>r.json()).catch(()=>({})),
      fetch(API+'/admin/agents',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}}).then(r=>r.json()).catch(()=>({}))
    ]);
    const agentList=agents.agents||agents.data||[];
    const active=Array.isArray(agentList)?agentList.filter(a=>a.status==='running'||a.running).length:0;
    el.innerHTML=`
      <div class="nas-admin-stat-grid">
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">🔌</div><div class="nas-admin-stat-val">${health.status==='healthy'?'✅ Healthy':'⚠ '+health.status}</div><div class="nas-admin-stat-label">Backend</div></div>
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">🤖</div><div class="nas-admin-stat-val">${active}/${Array.isArray(agentList)?agentList.length:0}</div><div class="nas-admin-stat-label">Agents aktiv</div></div>
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">🧠</div><div class="nas-admin-stat-val">${health.services?.ollama?.models_available||'?'}</div><div class="nas-admin-stat-label">Ollama models</div></div>
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">🔴</div><div class="nas-admin-stat-val">${health.services?.redis?.status==='healthy'?'✅':'⚠'}</div><div class="nas-admin-stat-label">Redis</div></div>
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">🔍</div><div class="nas-admin-stat-val">${health.services?.searxng?.status==='healthy'?'✅':'⚠'}</div><div class="nas-admin-stat-label">SearxNG</div></div>
        <div class="nas-admin-stat"><div class="nas-admin-stat-icon">⚡</div><div class="nas-admin-stat-val">${health.response_time_ms||0}ms</div><div class="nas-admin-stat-label">Response</div></div>
      </div>
    `;
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Failed to load der Admin-Übersicht</div>';
  }
}

async function loadSystem(root){
  const el=root.querySelector('#nas-system-content');
  if(!el)return;
  el.innerHTML='<div class="nas-loading-box">⏳ Loading system status…</div>';
  try{
    const r=await fetch(API+'/admin/status',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const svcs=d.services||{};
    let html='<div class="nas-sys-grid">';
    for(const[k,v]of Object.entries(svcs)){
      const ok=v.status==='healthy'||v.status==='ok'||v.running;
      html+=`<div class="nas-sys-card ${ok?'ok':'warn'}">
        <div class="nas-sys-name">${k}</div>
        <div class="nas-sys-status">${ok?'✅':'⚠'} ${v.status||'unknown'}</div>
        ${v.models_available?`<div class="nas-sys-detail">${v.models_available} models</div>`:''}
        ${v.message?`<div class="nas-sys-detail">${esc(v.message)}</div>`:''}
      </div>`;
    }
    html+='</div>';
    if(d.model_count)html+=`<div class="nas-info-bar">📊 Total models: <strong>${d.model_count}</strong></div>`;
    el.innerHTML=html;
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Error: '+e.message+'</div>';
  }
}

async function loadAgents(root){
  const el=root.querySelector('#nas-agents-content');
  if(!el)return;
  el.innerHTML='<div class="nas-loading-box">⏳ Loading agents…</div>';
  try{
    const r=await fetch(API+'/admin/agents',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const agents=d.agents||d.data||[];
    if(!agents.length){el.innerHTML='<div class="nas-loading-box">No agents found</div>';return}
    el.innerHTML='<div class="nas-agent-grid">'+agents.map(function(a){
      const running=a.status==='running'||a.running;
      const aid=a.id||a.name||'agent';
      return `<div class="nas-agent-card ${running?'running':'stopped'}">
        <div class="nas-agent-header">
          <span class="nas-agent-dot"></span>
          <strong class="nas-agent-name">${esc(a.name||aid)}</strong>
          <span class="nas-agent-status">${running?'🟢':'🔴'} ${esc(a.status||'stopped')}</span>
        </div>
        ${a.model?`<div class="nas-agent-model">🧠 ${esc(a.model)}</div>`:''}
        ${a.uptime?`<div class="nas-agent-detail">⏱ ${esc(String(a.uptime))}</div>`:''}
        <div class="nas-agent-actions">
          ${running
            ?`<button class="nas-btn-sm nas-btn-warn" data-agent="${esc(aid)}" data-action="stop">⏹ Stop</button>
               <button class="nas-btn-sm nas-btn-outline" data-agent="${esc(aid)}" data-action="restart">🔄 Restart</button>`
            :`<button class="nas-btn-sm nas-btn-ok" data-agent="${esc(aid)}" data-action="start">▶ Start</button>`}
        </div>
      </div>`;
    }).join('')+'</div>';
    // Agent action buttons
    el.querySelectorAll('[data-agent][data-action]').forEach(function(btn){
      btn.addEventListener('click',async function(){
        const aid=btn.dataset.agent;
        const action=btn.dataset.action;
        btn.disabled=true; btn.textContent='⏳';
        try{
          const r=await fetch(API+'/admin/agents/'+encodeURIComponent(aid)+'/'+action,{method:'POST',credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
          const d=await r.json();
          // Reload agents panel
          const key='agents_'+(root.id||'r');
          delete _loaded[key];
          loadAgents(root);
        }catch(e){btn.disabled=false;btn.textContent='Error'}
      });
    });
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Error: '+e.message+'</div>';
  }
}

async function loadMcpTools(root){
  const el=root.querySelector('#nas-mcp-content');
  if(!el)return;
  el.innerHTML='<div class="nas-loading-box">⏳ Loading MCP tools…</div>';
  try{
    const r=await fetch(API+'/admin/mcp/tools',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const tools=d.tools||d.data||[];
    const filterInput=root.querySelector('#nas-mcp-filter');
    function renderTools(filter){
      const filtered=filter?tools.filter(t=>(t.name||'').toLowerCase().includes(filter.toLowerCase())||(t.description||'').toLowerCase().includes(filter.toLowerCase())):tools;
      el.innerHTML='<div class="nas-mcp-count">'+filtered.length+' / '+tools.length+' Tools</div><div class="nas-mcp-grid">'+filtered.map(function(t){
        return `<div class="nas-mcp-tool" data-tool="${esc(t.name||'')}">
          <div class="nas-mcp-tool-name">🔧 ${esc(t.name||'—')}</div>
          <div class="nas-mcp-tool-desc">${esc((t.description||'').substring(0,80))}${t.description&&t.description.length>80?'…':''}</div>
          <button class="nas-btn-sm nas-btn-outline nas-mcp-run" data-tool="${esc(t.name||'')}">▶ Run</button>
        </div>`;
      }).join('')+'</div>';
      // MCP run buttons
      el.querySelectorAll('.nas-mcp-run').forEach(function(btn){
        btn.addEventListener('click',function(){
          const toolName=btn.dataset.tool;
          const argsEl=root.querySelector('#nas-mcp-args');
          const toolEl=root.querySelector('#nas-mcp-call-tool');
          if(toolEl)toolEl.value=toolName;
          if(argsEl)argsEl.value='{}';
          // Scroll to call panel
          const callPanel=root.querySelector('#nas-mcp-call-section');
          if(callPanel)callPanel.scrollIntoView({behavior:'smooth'});
        });
      });
    }
    renderTools('');
    if(filterInput){filterInput.addEventListener('input',function(){renderTools(filterInput.value)})}
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Error: '+e.message+'</div>';
  }
  // MCP call form
  const callBtn=root.querySelector('#nas-mcp-call-btn');
  if(callBtn&&!callBtn.dataset.bound){
    callBtn.dataset.bound='1';
    callBtn.addEventListener('click',async function(){
      const toolEl=root.querySelector('#nas-mcp-call-tool');
      const argsEl=root.querySelector('#nas-mcp-args');
      const resultEl=root.querySelector('#nas-mcp-result');
      const tool=toolEl?.value?.trim()||'';
      if(!tool){if(resultEl)resultEl.textContent='Tool-Name fehlt';return}
      let args={};
      try{args=JSON.parse(argsEl?.value||'{}')}catch{if(resultEl)resultEl.textContent='Ungültiges JSON';return}
      if(resultEl)resultEl.textContent='⏳ Ausführen…';
      callBtn.disabled=true;
      try{
        const r=await fetch(API+'/admin/mcp/call',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-WP-Nonce':CFG.nonce||''},body:JSON.stringify({tool,args})});
        const d=await r.json();
        if(resultEl)resultEl.textContent=JSON.stringify(d,null,2);
      }catch(e){
        if(resultEl)resultEl.textContent='Error: '+e.message;
      }finally{callBtn.disabled=false}
    });
  }
}

async function loadVault(root){
  const el=root.querySelector('#nas-vault-content');
  if(!el)return;
  el.innerHTML='<div class="nas-loading-box">⏳ Loading vault…</div>';
  try{
    const r=await fetch(API+'/admin/vault/keys',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const keys=d.keys||d.data||[];
    el.innerHTML='<div class="nas-vault-keys"><h3>🔑 Stored Keys ('+keys.length+')</h3><div class="nas-vault-list">'+
      (keys.length?keys.map(function(k){return`<div class="nas-vault-key-row"><span class="nas-vault-key-name">${esc(typeof k==='string'?k:k.name||k.key||String(k))}</span><span class="nas-vault-key-mask">●●●●●●●●</span></div>`}).join(''):'<div class="nas-muted">No keys found</div>')+
    '</div></div>';
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Error: '+e.message+'</div>';
  }
  // Set key form
  const setBtn=root.querySelector('#nas-vault-set-btn');
  if(setBtn&&!setBtn.dataset.bound){
    setBtn.dataset.bound='1';
    setBtn.addEventListener('click',async function(){
      const keyEl=root.querySelector('#nas-vault-key-name');
      const valEl=root.querySelector('#nas-vault-key-value');
      const msgEl=root.querySelector('#nas-vault-msg');
      const k=keyEl?.value?.trim()||'';
      const v=valEl?.value?.trim()||'';
      if(!k||!v){if(msgEl){msgEl.textContent='Key und Wert erforderlich';msgEl.className='nas-msg error';msgEl.style.display='block'}return}
      try{
        const r=await fetch(API+'/admin/vault/set',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-WP-Nonce':CFG.nonce||''},body:JSON.stringify({key:k,value:v})});
        const d=await r.json();
        if(msgEl){msgEl.textContent=d.ok!==false?'✅ Key gespeichert':'Error: '+(d.error||'unbekannt');msgEl.className='nas-msg '+(d.ok!==false?'ok':'error');msgEl.style.display='block'}
        if(d.ok!==false){if(keyEl)keyEl.value='';if(valEl)valEl.value='';const key='vault_'+(root.id||'r');delete _loaded[key];loadVault(root)}
      }catch(e){
        if(msgEl){msgEl.textContent='Error: '+e.message;msgEl.className='nas-msg error';msgEl.style.display='block'}
      }
    });
  }
}

async function loadLogs(root){
  const el=root.querySelector('#nas-logs-content');
  if(!el)return;
  el.innerHTML='<div class="nas-loading-box">⏳ Loading logs…</div>';
  try{
    const cat=root.querySelector('#nas-logs-cat')?.value||'all';
    const r=await fetch(API+'/admin/logs?category='+cat+'&limit=50',{credentials:'same-origin',headers:{'X-WP-Nonce':CFG.nonce||''}});
    const d=await r.json();
    const logs=d.logs||d.data||[];
    if(!logs.length){el.innerHTML='<div class="nas-loading-box">No logs</div>';return}
    el.innerHTML='<div class="nas-log-box">'+logs.map(function(l){
      const msg=l.message||l.msg||String(l);
      const lvl=(l.level||'info').toLowerCase();
      return`<div class="nas-log-line nas-log-${lvl}">${esc(msg)}</div>`;
    }).join('')+'</div>';
    // Scroll to bottom
    const logBox=el.querySelector('.nas-log-box');
    if(logBox)logBox.scrollTop=logBox.scrollHeight;
  }catch(e){
    el.innerHTML='<div style="color:var(--nas-muted)">Error: '+e.message+'</div>';
  }
  // Refresh + filter
  const refreshBtn=root.querySelector('#nas-logs-refresh');
  if(refreshBtn&&!refreshBtn.dataset.bound){
    refreshBtn.dataset.bound='1';
    refreshBtn.addEventListener('click',function(){const key='logs_'+(root.id||'r');delete _loaded[key];loadLogs(root)});
  }
  const catSel=root.querySelector('#nas-logs-cat');
  if(catSel&&!catSel.dataset.bound){
    catSel.dataset.bound='1';
    catSel.addEventListener('change',function(){const key='logs_'+(root.id||'r');delete _loaded[key];loadLogs(root)});
  }
}

function fmtBytes(b){
  if(b>=1073741824)return(b/1073741824).toFixed(1)+' GB';
  if(b>=1048576)return(b/1048576).toFixed(1)+' MB';
  if(b>=1024)return(b/1024).toFixed(1)+' KB';
  return b+' B';
}
function esc(s){const d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML}

})();
