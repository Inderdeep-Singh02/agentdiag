/*
  agentdiag's one renderer (D40, phase-7 decisions 20 and 23). The Report file embeds one
  RunView as window.AGENTDIAG_VIEW beside this script and opens on #report/<run id>, the Run
  screen without the shell; `agentdiag serve` loads the same script with no view set, and
  the data layer below reads the /api/... routes instead. Nothing else differs between the
  two hosts. Every string-building function is pure (no DOM), so the flame graph can be
  rendered and checked outside a browser; the DOM is touched only by the shell, routing,
  the tooltip, cite() and afterRender().
*/

/* ---------- helpers ---------- */
const h=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const short=id=>id?String(id).slice(0,8):'—';
const fmtMs=ms=>ms==null?'—':ms<1000?`${ms} ms`:`${(ms/1000).toFixed(1)} s`;
const fmtUsd=v=>v==null?'—':'$'+v.toFixed(v<0.01?4:2);
const when=iso=>iso?iso.replace('T',' ').replace(/:\d\dZ$/,'Z'):'—';
const pct=v=>v==null?'—':Math.round(v*100)+'%';
const VERDICTS=['pass','fail','incomplete','unverifiable','invalid'];
const VCLS={pass:'st-good',fail:'st-critical',incomplete:'st-warning',unverifiable:'st-neutral',invalid:'st-serious'};
const pill=(cls,text)=>`<span class="pill ${h(cls)}"><i></i>${h(text??cls)}</span>`;
const SCREENS=[['dashboard','Dashboard','1'],['target','Target','2'],['sync','Sync','3'],['tests','Tests','4'],['run','Run report','5'],['compare','Compare','6'],['flow','Flow view','7']];
const FID={instrumented:'●',reconstructed:'◐',observed:'○'};
function verdictBar(counts){const v=VERDICTS.map(k=>counts[k]||0);const tot=v.reduce((a,b)=>a+b,0)||1;return `<div class="bar" title="${VERDICTS.map((k,i)=>k+' '+v[i]).join(' · ')}">${v.map((n,i)=>n?`<i class="${VCLS[VERDICTS[i]]}" style="width:${n/tot*100}%"></i>`:'').join('')}</div><div class="small muted mono" style="text-align:right">${v.join(' · ')}</div>`;}
function showTT(e,text){const t=document.getElementById('tt');t.textContent=text;t.style.display='block';t.style.left=Math.min(window.innerWidth-360,e.clientX+12)+'px';t.style.top=(e.clientY+14)+'px';}
function hideTT(){document.getElementById('tt').style.display='none';}

/* ---------- data: the embedded RunView, or the served routes ---------- */
const VIEW=(typeof window!=='undefined'&&window.AGENTDIAG_VIEW)||null;
const EMBEDDED=VIEW!==null;
const enc=encodeURIComponent;
/* A failed read is an Error naming the path, its status and the server's sentence. */
const TOKEN=(typeof window!=='undefined'&&window.AGENTDIAG_TOKEN)||'';
/* bodyOf: a response's JSON, or null when it has none: an error page, a stream cut short. */
const bodyOf=r=>Promise.resolve().then(()=>r.json()).catch(()=>null);
function failed(path,r){return bodyOf(r).then(b=>{throw new Error(`${path}: ${r.status}${b&&b.error?' · '+b.error:''}`);});}
function getJson(path){return fetch(path,{headers:{accept:'application/json'}}).then(r=>r.ok?r.json():failed(path,r));}
/* Every write is a JSON POST carrying X-Agentdiag-Request and the page token the server embedded in this
   page (X-Agentdiag-Token), which it requires of its own page; the answer is {ok, status, body} so a
   refusal's sentence can be shown where it happened. */
function postJson(path,body){return fetch(path,{method:'POST',headers:{'content-type':'application/json','x-agentdiag-request':'1','x-agentdiag-token':TOKEN,accept:'application/json'},body:JSON.stringify(body||{})}).then(r=>bodyOf(r).then(b=>({ok:r.ok,status:r.status,body:b||{}})));}
const qs=pairs=>{const s=pairs.filter(([,v])=>v!=null&&v!=='').map(([k,v])=>`${k}=${enc(v)}`).join('&');return s?'?'+s:'';};
const API={
  dashboard:()=>EMBEDDED?Promise.reject(new Error('a Report carries no Dashboard')):getJson('/api/dashboard'),
  registry:()=>EMBEDDED?Promise.resolve([]):getJson('/api/registry'),
  runs:target=>EMBEDDED?Promise.resolve([{run_id:VIEW.header.run_id,target:VIEW.header.target,source:VIEW.header.source}]):getJson('/api/runs'+(target?'?target='+enc(target):'')),
  run:id=>EMBEDDED?Promise.resolve(VIEW):getJson('/api/runs/'+enc(id)),
  change:id=>EMBEDDED?(VIEW.change_record&&VIEW.change_record.id===id?Promise.resolve(VIEW.change_record):Promise.reject(new Error('this Report carries no Change record '+id))):getJson('/api/changes/'+enc(id)),
  flow:(slug,env,id)=>EMBEDDED?Promise.reject(new Error('a Report carries no Flow definition')):getJson('/api/flows/'+[slug,env,id].map(enc).join('/')),
  target:slug=>getJson('/api/targets/'+enc(slug)),
  sync:(slug,env)=>getJson('/api/targets/'+enc(slug)+'/sync'+qs([['env',env]])),
  suites:slug=>getJson('/api/targets/'+enc(slug)+'/suites'),
  changes:target=>getJson('/api/changes'+qs([['target',target]])),
  compare:(a,b,expect)=>getJson('/api/compare'+qs([['baseline',a],['run',b],...expect.map(e=>['expect',e])])),
  launch:body=>postJson('/api/runs',body),
  cancel:id=>postJson('/api/runs/'+enc(id)+'/cancel',{}),
  pull:(slug,body)=>postJson('/api/targets/'+enc(slug)+'/pull',body),
  preview:(slug,body)=>postJson('/api/targets/'+enc(slug)+'/push/preview',body),
  push:(slug,body)=>postJson('/api/targets/'+enc(slug)+'/push',body),
};

/* ---------- shell and routing ---------- */
let currentTarget=null;
function shellHtml(){return `<div class="tt" id="tt"></div>
<div class="shell">
  <aside class="side">
    <div class="brand"><div class="mark">ad</div><div><b>agentdiag</b><small>Runs, Sync and Change records</small></div></div>
    <div class="pick"><label class="small muted">Target<br><select id="target-pick" onchange="setTarget(this.value)"></select></label></div>
    <nav class="nav" id="nav"></nav>
    <div class="foot"><div>Theme <span class="tog" id="theme-tog"><button data-t="auto">auto</button><button data-t="light">light</button><button data-t="dark">dark</button></span></div></div>
  </aside>
  <main class="main" id="main"></main>
</div>
<div class="vbar" id="vbar"><button onclick="cycleVariant(-1)">←</button><span id="vbar-label"></span><button onclick="cycleVariant(1)">→</button></div>
<div class="modal-bg" id="modal"><div class="modal" role="dialog" aria-modal="true"><div class="body" id="modal-body"></div><div class="foot" id="modal-foot"></div></div></div>`;}
let registryReady=Promise.resolve([]);
function buildNav(){document.getElementById('nav').innerHTML=SCREENS.map(s=>`<a href="#${s[0]}" data-s="${s[0]}"><span class="k">${s[2]}</span>${s[1]}</a>`).join('');registryReady=API.registry().then(entries=>{const p=document.getElementById('target-pick');p.innerHTML=entries.map(e=>`<option value="${h(e.slug)}">${h(e.slug)}</option>`).join('');currentTarget=currentTarget||(entries[0]||{}).slug||null;if(currentTarget)p.value=currentTarget;return entries;}).catch(()=>[]);}
/* The Target picker: a screen that names its Target in the hash moves to the chosen one. */
function setTarget(slug){currentTarget=slug;document.getElementById('target-pick').value=slug;const {screen}=parseHash();if(['target','sync','tests'].includes(screen))location.hash=screen+'/'+enc(slug);else if(screen==='compare')location.hash='compare';else route();}
/* slugOf: the Target a screen shows, once the Registry has answered: the hash's, else the picker's. */
async function slugOf(arg){const entries=await registryReady;if(arg){currentTarget=arg;const p=document.getElementById('target-pick');if(p)p.value=arg;}return currentTarget||(entries[0]||{}).slug||null;}
function noTarget(name){return `<div class="hd"><div><h1>${h(name)}</h1><div class="sub">The Workspace holds no Target yet: <code>agentdiag init</code> adds one.</div></div></div>`;}
function startHash(){return EMBEDDED?'#report/'+VIEW.header.run_id:'#dashboard';}
function parseHash(){const raw=(location.hash||startHash()).slice(1);const [path,q]=raw.split('?');const parts=path.split('/').map(p=>{try{return decodeURIComponent(p);}catch(e){return p;}});const query={};(q||'').split('&').filter(Boolean).forEach(kv=>{const [k,v]=kv.split('=');query[k]=decodeURIComponent(v||'');});return {screen:parts[0],args:parts.slice(1),query};}
async function route(){let {screen,args,query}=parseHash();shownRun=null;shownTrial=null;const report=EMBEDDED||screen==='report';if(EMBEDDED&&screen!=='report')args=[VIEW.header.run_id];if(report)screen='run';if(!SCREENS.find(x=>x[0]===screen))screen='dashboard';document.body.classList.toggle('report',report);document.querySelectorAll('.nav a').forEach(a=>a.classList.toggle('on',a.dataset.s===screen));const main=document.getElementById('main');try{main.innerHTML=await RENDER[screen](args,query,report);}catch(e){main.innerHTML=`<div class="card"><div class="body"><h3>render error</h3><pre>${h(e.stack||e)}</pre></div></div>`;}afterRender(screen);window.scrollTo({top:0});if(query.cite)cite(query.cite.split(',').filter(Boolean));}
function onKey(e){if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;const m=EMBEDDED?null:SCREENS.find(x=>x[2]===e.key);if(m)location.hash=m[0];if(e.key==='ArrowLeft')cycleVariant(-1);if(e.key==='ArrowRight')cycleVariant(1);}
function setTheme(t){try{localStorage.setItem('ad-theme',t);}catch(e){}if(t==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',t);document.querySelectorAll('#theme-tog button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));}
function bindTheme(){document.querySelectorAll('#theme-tog button').forEach(b=>b.onclick=()=>setTheme(b.dataset.t));let t='auto';try{t=localStorage.getItem('ad-theme')||'auto';}catch(e){}setTheme(t);}

const RENDER={};

/* ---------- screens the served UI brings (tickets 16 and 29): dashboard, target, sync, tests, compare ----------
   Every value from the Workspace reaches the page through h(); no handler is built from data: a button names its
   action in data-act and its subject in data-* attributes, read by the one delegated onAction. */
const DIRECTIONS_MOVED=['local_ahead','deployed_ahead','diverged'];
const isProtected=(entry,env)=>((entry||{}).protected||[]).includes(env);
function prov(sources){return `<div class="small muted" style="margin-top:14px">reads: ${h(sources)}</div>`;}
function showTab(btn){const tabs=btn.closest('.tabs');tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===btn));document.querySelectorAll('.tabpane').forEach(p=>p.style.display=p.dataset.p===btn.dataset.p?'block':'none');}
const pointer=v=>v==null?'—':typeof v==='string'?v:v.path||JSON.stringify(v);

/* ---------- 1 · dashboard (ticket 29) ----------
   One row per Target from /api/dashboard (DashboardView): read from the Registry and the Index, never a source of
   truth. Words, not zeros: no Run is "no Run yet", no Fingerprint "not synced yet", an Index that did not answer
   "Index not read"; the trend's last rate always has its incomplete, unverifiable and invalid counts beside it. */
/* spark: the prototype's sparkline over TrendPoints (run_id, pass_rate, counts), oldest first. A point with no pass rate is
   a gap: the line breaks there and the point is drawn hollow on the axis. Every point's tooltip carries its three abnormal
   counts beside its rate (ADR-0005 §8). */
function spark(points){if(!points.length)return '<span class="muted small">no Run</span>';const w=170,hh=44,p=4;const n=points.length;const xs=points.map((_,i)=>n===1?w/2:p+i*(w-2*p)/(n-1));const ys=points.map(t=>hh-p-(t.pass_rate??0)*(hh-2*p));
  const runs=[];let cur=[];points.forEach((t,i)=>{if(t.pass_rate==null){if(cur.length)runs.push(cur);cur=[];}else cur.push(i);});if(cur.length)runs.push(cur);
  const last=n-1;const lastRate=points[last].pass_rate;
  return `<svg class="spark" viewBox="0 0 ${w} ${hh}"><line x1="${p}" y1="${hh-p}" x2="${w-p}" y2="${hh-p}" stroke="var(--grid)"/>${runs.map(r=>`<polyline points="${r.map(i=>xs[i].toFixed(1)+','+ys[i].toFixed(1)).join(' ')}" fill="none" stroke="var(--s1)" stroke-width="2"/>`).join('')}${points.map((t,i)=>`<circle cx="${xs[i]}" cy="${ys[i]}" r="3.5" fill="${t.pass_rate==null?'var(--surface)':'var(--s1)'}" stroke="${t.pass_rate==null?'var(--ink-3)':'var(--surface)'}" stroke-width="1.5"><title>${h(t.run_id)} · ${h(pointText(t))}</title></circle>`).join('')}${lastRate==null?'':`<text x="${xs[last]-6}" y="${Math.max(10,ys[last]-8)}" text-anchor="end" font-size="10" fill="var(--ink-2)">${pct(lastRate)}</text>`}</svg>`;}
const beside=c=>`incomplete ${(c||{}).incomplete||0} · unverifiable ${(c||{}).unverifiable||0} · invalid ${(c||{}).invalid||0}`;
const pointText=t=>`${t.pass_rate==null?'no pass rate':pct(t.pass_rate)} · ${beside(t.counts)}`;
function dashSync(e){const s=e.sync;const env=s&&s.environment||(e.environments||[])[0]||'';
  const status=s?s.status:'not_checked';
  const text=status==='not_checked'?'not synced yet':status+(s.environment?' · '+s.environment:'')+(s.open_breaks?` · ${s.open_breaks} open Sync break${s.open_breaks===1?'':'s'}`:'');
  const link=env?`#sync/${enc(e.slug)}/${enc(env)}`:`#sync/${enc(e.slug)}`;
  return `<a href="${h(link)}">${pill(status,text)}</a>${s&&s.last_push?`<div class="small muted">pushed ${h(when(s.last_push))}</div>`:''}`;}
function dashRow(r,read){const e=r.entry;const last=r.last_run;const slug=enc(e.slug);
  const kind=last?(last.source==='imported'?'imported':last.traces_from?'rescored from '+short(last.traces_from):'run'):'';
  const lastCell=!read?'<span class="muted">Index not read</span>':last?`<a class="mono" href="#run/${h(enc(last.run_id))}">${h(last.run_id)}</a><div class="small muted">${h(when(last.created_at))} · ${h(kind)}</div>`:'<span class="muted">no Run yet</span>';
  const judged=last&&VERDICTS.some(k=>(last.counts||{})[k]);
  const verdicts=!read||!last?'<span class="muted">—</span>':judged?verdictBar(last.counts):'<span class="muted">not judged</span>';
  const trend=r.trend||[];
  const trendCell=!read?'<span class="muted small">Index not read</span>':trend.length?`${spark(trend)}<div class="small muted mono" data-section="trend-counts">${h(pointText(trend[trend.length-1]))}</div>`:last?'<span class="muted small">no Run agentdiag drove · imported and rescored Runs are not trended</span>':'<span class="muted small">no Run yet</span>';
  const ids=r.open_change_ids||[];
  const changes=!read?'<span class="muted">Index not read</span>':ids.length?ids.map(id=>`<a class="mono small" href="#flow/${h(enc(id))}">${h(id)}</a>`).join('<br>'):'<span class="muted">none open</span>';
  const envs=(e.environments||[]).map(x=>`<a class="chip env${(e.protected||[]).includes(x)?' prot':''}" href="#sync/${h(slug)}/${h(enc(x))}" title="${(e.protected||[]).includes(x)?'protected: push needs the name typed':'Sync '+h(x)}">${h(x)}</a>`).join(' ')||'<span class="muted">none</span>';
  const problems=(e.problems||[]).length?`<div class="small" style="color:var(--critical)">${e.problems.map(h).join('<br>')}</div>`:'';
  return `<tr data-slug="${h(e.slug)}">
      <td><a href="#target/${h(slug)}"><b>${h(e.name||e.slug)}</b></a><div class="small mono muted">${h(e.slug)} · ${h(e.family||'—')} · ${h(e.channel||'—')}</div>${problems}</td>
      <td>${envs}</td>
      <td>${dashSync(e)}</td>
      <td>${lastCell}</td>
      <td class="num">${verdicts}</td>
      <td>${trendCell}</td>
      <td>${changes}</td>
      <td><a class="btn sm" href="#target/${h(slug)}">open</a> <a class="btn sm" href="#tests/${h(slug)}">Tests</a></td></tr>`;}
RENDER.dashboard=async function(){const v=await API.dashboard();const rows=v.rows||[];const read=v.index_read!==false;
  const sync=r=>r.entry.sync;const broken=rows.filter(r=>(sync(r)||{}).status==='broken');const unsynced=rows.filter(r=>!sync(r)||sync(r).status==='not_checked');
  const families=[...new Set(rows.map(r=>r.entry.family).filter(Boolean))];const open=rows.reduce((a,r)=>a+(r.open_changes||0),0);
  const problems=(v.problems||[]).length?`<div class="well small" style="margin-bottom:12px;border-color:var(--critical)" data-section="dashboard-problems">${v.problems.map(h).join('<br>')}</div>`:'';
  if(!rows.length)return noTarget('Dashboard');
  return `<div class="hd"><div><h1>Targets</h1><div class="sub">${rows.length} Target${rows.length===1?'':'s'} in this Workspace. Sync, last Run and Score trend, read from the Registry and the Index.</div></div>
    <div style="display:flex;gap:8px"><a class="btn pri" href="#tests">Run tests…</a></div></div>
    ${problems}
    <div class="grid g4" style="margin-bottom:16px">
      <div class="card stat"><div class="l">Targets</div><div class="v">${rows.length}</div><div class="d">${families.length?h(families.join(' · ')):'no Family named'}</div></div>
      <div class="card stat"><div class="l">Sync broken</div><div class="v" style="color:${broken.length?'var(--critical)':'inherit'}">${broken.length}</div><div class="d">${h(broken.map(r=>r.entry.slug).join(' · ')||'none')}</div></div>
      <div class="card stat"><div class="l">Not synced yet</div><div class="v">${unsynced.length}</div><div class="d">${h(unsynced.map(r=>r.entry.slug).join(' · ')||'every Target has a Fingerprint')}</div></div>
      <div class="card stat"><div class="l">Open Change records</div><div class="v">${read?open:'—'}</div><div class="d">${read?'open, proposed or pushed':'Index not read'}</div></div>
    </div>
    <div class="card"><div class="head"><h3>Targets</h3><span class="legend"><span><i class="st-good"></i>held</span><span><i class="st-critical"></i>broken</span><span><i class="st-neutral"></i>not synced yet</span></span></div>
    <div class="scroll-x"><table data-section="dashboard"><thead><tr><th>Target</th><th>Environments</th><th>Sync</th><th>Last Run</th><th class="num">Verdicts</th><th style="width:180px">Trend (pass rate, last ${h(v.trend)} driven Runs)</th><th>Open Change records</th><th></th></tr></thead><tbody>${rows.map(r=>dashRow(r,read)).join('')}</tbody></table></div></div>
    ${prov('dashboard() · the Registry · the Index (runs, changes)')}<div class="small muted">A pass rate is never shown without the incomplete, unverifiable and invalid counts beside it; imported and rescored Runs are listed, never trended.</div>`;};

/* ---------- 2 · target ---------- */
RENDER.target=async function(args){
  const slug=await slugOf(args[0]);if(!slug)return noTarget('Target');
  const [v,suites,runs,sync]=await Promise.all([API.target(slug),API.suites(slug).catch(()=>null),API.runs(slug).catch(()=>[]),API.sync(slug).catch(e=>({error:String(e.message||e)}))]);
  return targetHtml(v,suites,runs,sync);
};
function targetHtml(v,suites,runs,sync){const e=v.entry;const m=v.manifest||{};const fp=v.fingerprint;const r=sync.result||{};const secs=r.sections||[];const env=sync.environment||'';
  const tabs=['Prompts','Tools','Data sources','Environments','Calibration Notes','Suites','Change records'];
  const promptRows=secs.filter(s=>s.kind==='prompt').map(s=>`<tr><td>${h(s.id)}</td><td>${h(short(s.local))}</td><td>${h(short(s.deployed))}</td><td>${h(short(s.recorded))}</td><td class="${s.direction==='identical'?'eq':'ne'}">${h(s.direction)}${s.change?' · '+h(s.change):''}${s.note?' · '+h(s.note):''}</td><td class="muted">${h(s.deployed_by||'—')}</td></tr>`).join('')||`<tr><td colspan="6" class="muted">no Sync result: ${h(sync.error||r.reason||'run agentdiag sync')}</td></tr>`;
  const toolRows=Object.entries(m.tools||{}).map(([n,tool])=>{const s=secs.find(x=>x.id==='tool.'+n)||{};return `<tr><td class="mono">${h(n)}</td><td><span class="kind">${h(tool.kind)}</span></td><td>${h(tool.side_effects||'none')}</td><td class="mono">${h(pointer(tool.schema))}</td><td class="${s.direction==='identical'?'eq':'ne'}">${h(s.direction||'—')}</td></tr>`;}).join('')||'<tr><td colspan="5" class="muted">none declared</td></tr>';
  const dataRows=Object.entries(m.data_sources||{}).map(([n,d])=>`<tr><td class="mono">${h(n)}</td><td>${h(typeof d==='string'?d:d.identity)}</td><td>${h(typeof d==='string'?'':d.kind||'')}</td></tr>`).join('')||'<tr><td colspan="3" class="muted">none declared</td></tr>';
  const adapter=m.adapter||{};const envs=Object.entries(adapter.environments||{}).filter(([k])=>k!=='default');const cenvs=(m.connector||{}).environments||{};
  const envNames=[...envs.map(([k])=>k),...Object.keys(cenvs).filter(k=>!envs.find(([n])=>n===k))];
  const envRows=envNames.map(name=>{const block=(adapter.environments||{})[name]||{};const cenv=cenvs[name];return `<tr><td><b>${h(name)}</b> ${name===adapter.environments?.default?'<span class="chip">default</span>':''} ${isProtected(e,name)?'<span class="chip prot">protected</span>':''}</td><td>${h(block.side_effects||adapter.side_effects||'none')}${block.turn_timeout?' · timeout '+h(block.turn_timeout)+' s':''}</td><td class="mono small">${cenv?h(cenv.deployed||JSON.stringify(cenv)):'<span class="muted">no Connector environment</span>'}</td><td>${name===env?pill(r.status||'not_checked',r.status||'not checked'):'<span class="muted">not checked here</span>'}</td><td><a class="btn sm" href="#sync/${h(enc(e.slug))}/${h(enc(name))}">Sync</a></td></tr>`;}).join('');
  const notes=v.calibration_notes;const last=runs[0];
  const suiteRows=(suites?suites.suites:[]).map(s=>{const kinds=[...new Set(s.scenarios.flatMap(x=>x.kinds))].sort();return `<tr><td class="mono">${h(s.reference)}</td><td>${h(s.status)}${s.problem?' · <span style="color:var(--critical)">'+h(s.problem)+'</span>':''}</td><td class="num">${s.scenarios.length}</td><td>${kinds.map(k=>`<span class="kind">${h(k)}</span>`).join(' ')}</td></tr>`;}).join('')||'<tr><td colspan="4" class="muted">no Suite</td></tr>';
  const chRows=(v.change_records||[]).map(c=>`<tr><td class="mono"><a href="#flow/${h(enc(c.id))}">${h(c.id)}</a></td><td>${h(when(c.opened_at))}</td><td>${pill(c.status)}</td><td>${h(c.title)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">none</td></tr>';
  const lastRun=last?`<a class="mono" href="#run/${h(enc(last.run_id))}">${h(last.run_id)}</a> · ${pct(last.pass_rate)} (${VERDICTS.map(k=>(last.counts||{})[k]||0).join(' · ')})`:'<span class="muted">no Run yet</span>';
  return `<div class="hd"><div><div class="muted small mono">${h(e.slug)} · ${h(v.directory)}</div><h1>${h((m.target||{}).name||e.slug)}</h1><div class="sub">${h((m.target||{}).description||'')}</div></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${pill(r.status||'not_checked','Sync '+(r.status||'not checked'))}<a class="btn" href="#sync/${h(enc(e.slug))}">Open Sync</a><a class="btn pri" href="#tests/${h(enc(e.slug))}">Run Suite</a></div></div>
    ${(e.problems||[]).length?`<div class="well small" style="margin-bottom:12px;border-color:var(--critical)">${e.problems.map(h).join('<br>')}</div>`:''}
    <div class="tabs" id="tabs">${tabs.map((x,i)=>`<button class="${i===0?'on':''}" data-p="${i}" onclick="showTab(this)">${x}</button>`).join('')}</div>
    <div class="tabpane" data-p="0"><div class="two"><div class="card"><div class="head"><h3>Prompt sections · local / deployed / recorded</h3><span class="small muted">sha256 per section, environment ${h(env||'—')}</span></div><div class="body"><table class="drift"><thead><tr><th>section</th><th>local</th><th>deployed</th><th>recorded</th><th>direction</th><th>covered</th></tr></thead><tbody>${promptRows}</tbody></table></div></div>
      <div class="grid" style="align-content:start"><div class="card"><div class="head"><h3>Manifest</h3><span class="small mono muted">manifest.yaml</span></div><div class="body"><dl class="kv"><dt>schema_version</dt><dd>${h(m.schema_version)}</dd><dt>family · channel</dt><dd>${h(e.family||'—')} · ${h(e.channel||'—')}</dd><dt>adapter</dt><dd>${h(adapter.kind)} · side_effects <b>${h(adapter.side_effects)}</b> · ${envs.length} environment(s)</dd><dt>connector</dt><dd>${m.connector?h(m.connector.kind)+' · evidence '+h(Object.keys(m.connector.evidence||{}).join(', ')||'none'):'none'}</dd><dt>prompts</dt><dd>${Object.entries(m.prompts||{}).map(([k,p])=>h(k)+': '+h(pointer(p))).join('<br>')||'—'}</dd><dt>tools</dt><dd>${Object.keys(m.tools||{}).length}</dd><dt>Fingerprint</dt><dd>${fp?`<span class="mono">${h(short((e.sync||{}).fingerprint))}</span> built ${h(when(fp.built_at))} · ${Object.keys(fp.sections||{}).length} sections${fp.resynced_from?' · re-synced from '+h(short(fp.resynced_from)):''}${fp.pushed_from?' · pushed from '+h(short(fp.pushed_from)):''}`:'<span class="muted">none · run agentdiag sync</span>'}</dd><dt>open Sync breaks</dt><dd>${(v.sync_breaks||[]).length}</dd><dt>last Run</dt><dd>${lastRun}</dd></dl></div></div>
      ${(m.eval_parameters||m.forbidden_phrases||m.suppressions)?`<div class="card"><div class="head"><h3>Eval parameters</h3><span class="small muted">mechanical, never prose for the Judge</span></div><div class="body"><pre class="small mono" style="margin:0;white-space:pre-wrap">${h(JSON.stringify({eval_parameters:m.eval_parameters,forbidden_phrases:m.forbidden_phrases,suppressions:m.suppressions},null,1))}</pre></div></div>`:''}
      </div></div></div>
    <div class="tabpane" data-p="1" style="display:none"><div class="card"><div class="scroll-x"><table><thead><tr><th>tool</th><th>kind</th><th>side effects</th><th>schema pointer</th><th>Sync</th></tr></thead><tbody>${toolRows}</tbody></table></div></div></div>
    <div class="tabpane" data-p="2" style="display:none"><div class="card"><div class="scroll-x"><table><thead><tr><th>name</th><th>identity</th><th>kind</th></tr></thead><tbody>${dataRows}</tbody></table></div></div></div>
    <div class="tabpane" data-p="3" style="display:none"><div class="card"><div class="scroll-x"><table><thead><tr><th>environment</th><th>side effects</th><th>Connector</th><th>Sync</th><th></th></tr></thead><tbody>${envRows}</tbody></table></div><div class="body small muted">A protected environment's push needs its name typed by a person, on a terminal or in the Sync screen's confirm step (ADR-0011 §6d).</div></div></div>
    <div class="tabpane" data-p="4" style="display:none"><div class="card"><div class="head"><h3>Calibration Notes</h3><span class="small mono muted">${notes?h(notes.path)+' · '+h(notes.words)+' words · '+h(short(notes.fingerprint)):'none'}</span></div><div class="body small muted">${notes?'Every Judge prompt of this Target carries the file verbatim; it is part of the Judge\'s Fingerprint.':'This Target has no Calibration Notes.'}</div></div></div>
    <div class="tabpane" data-p="5" style="display:none"><div class="card"><div class="scroll-x"><table><thead><tr><th>Suite</th><th>status</th><th class="num">Scenarios</th><th>kinds</th></tr></thead><tbody>${suiteRows}</tbody></table></div></div></div>
    <div class="tabpane" data-p="6" style="display:none"><div class="card"><div class="head"><h3>Change records</h3><a class="small" href="#flow">all of them</a></div><div class="scroll-x"><table><thead><tr><th>id</th><th>opened</th><th>status</th><th>title</th></tr></thead><tbody>${chRows}</tbody></table></div></div></div>
    ${prov('target_view() · sync --check · the Suite files · list')}`;}

/* ---------- 3 · sync ---------- */
let syncShown=null,pushShown=null,resyncPolicy='resync';
RENDER.sync=async function(args){const slug=await slugOf(args[0]);if(!slug)return noTarget('Sync');const s=await API.sync(slug,args[1]);syncShown=s;return syncHtml(s);};
function diffOf(s,id){return ((s.preview||{}).sections||[]).find(d=>d.id===id)||null;}
function syncHtml(s){const r=s.result||{};const secs=r.sections||[];const p=s.preview;const changed=secs.filter(x=>DIRECTIONS_MOVED.includes(x.direction));
  const rows=secs.map(x=>{const d=diffOf(s,x.id);const ahead=x.direction==='local_ahead';const behind=x.direction==='deployed_ahead'||x.direction==='diverged';
    return `<tr><td>${DIRECTIONS_MOVED.includes(x.direction)?`<input type="checkbox" data-sec="${h(x.id)}" ${ahead?'checked':'disabled'}>`:''}</td><td class="mono">${h(x.id)}</td><td>${h(x.kind)}</td><td class="${x.direction==='identical'?'eq':x.direction==='not_covered'?'muted':'ne'} drift">${h(x.direction)}${x.change?' · '+h(x.change):''}</td><td class="muted small">${h(x.deployed_by||'—')}${x.note?' · '+h(x.note):''}</td><td>${d?`<button class="btn sm" data-act="diff" data-sec="${h(x.id)}">diff</button> `:''}${ahead&&d?`<button class="btn sm" data-act="push" data-sec="${h(x.id)}">push</button>`:''}${behind?`<button class="btn sm" data-act="pull" data-sec="${h(x.id)}">pull</button>`:''}${x.direction==='diverged'?' <span class="small muted">pull, merge, push again · no force flag</span>':''}</td></tr>`;}).join('');
  const first=(p&&p.sections[0])||null;
  const pushRows=(s.pushes||[]).slice().reverse().map(({path,push:x})=>`<tr><td>${h(when(x.pushed_at))}</td><td>${h(x.environment)}</td><td class="mono small">${h(x.sections.join(', '))}</td><td>${h(x.by)}</td><td>${h(x.effective_side_effects)} · ${h(x.confirmed_by)}</td><td class="mono">${h(short(x.fingerprint_before))} → ${h(short(x.fingerprint_after))}</td><td class="mono small">${h(x.restore_point)}</td><td class="mono small">${x.change_record?`<a href="#flow/${h(enc(x.change_record))}">${h(x.change_record)}</a>`:'none'}</td></tr>`).join('')||`<tr><td colspan="8" class="muted">no Push record under targets/${h(s.target)}/pushes/ yet · every push writes one (ADR-0011 §8)</td></tr>`;
  const breaks=(s.sync_breaks||[]).map(b=>`<div class="mono small">${h(b.path)}</div>`).join('');
  const envPick=`<select onchange="location.hash='sync/'+encodeURIComponent(currentTarget)+'/'+encodeURIComponent(this.value)">${s.environments.map(x=>`<option value="${h(x)}" ${x===s.environment?'selected':''}>${h(x)}</option>`).join('')}</select>`;
  const policy=policyHtml([['resync','re-sync (default, ADR-0008)'],['no_resync','<code>--no-resync</code> Run labelled broken'],['strict','<code>--strict</code> refuse']],' &nbsp; ');
  return `<div class="hd"><div><h1>Sync · ${h(s.target)} · ${h(s.environment)}${s.protected?' <span class="chip prot">protected</span>':''}</h1><div class="sub">Fingerprint <span class="mono">${h(short(r.fingerprint))}</span> built ${h(when(r.built_at))} · ${pill(r.status||'not_checked',(r.status||'not checked')+(changed.length?' · '+changed.length+' section(s)':'')+(r.reason?' · '+r.reason:''))} · read through ${h(r.covered_by||'—')}</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><label class="small muted">environment ${envPick}</label><button class="btn" data-act="refresh">sync --check</button><button class="btn" data-act="pull">Pull deployed → local…</button><button class="btn pri" data-act="push-ticked">Push local → deployed…</button></div></div>
    ${s.error?`<div class="well small" style="margin-bottom:12px;border-color:var(--critical)">${h(s.error)}</div>`:''}
    ${breaks?`<div class="well small" style="margin-bottom:12px;border-color:var(--warning)"><b>Open Sync breaks · ${(s.sync_breaks||[]).length}</b>${breaks}<div class="muted">\`agentdiag sync --env ${h(s.environment)}\` re-records the Fingerprint and closes them.</div></div>`:''}
    <div class="two"><div class="card"><div class="head"><h3>Sections · three hashes each</h3><span class="small muted">a break names what moved; it never invalidates a prior Run</span></div><div class="body"><table><thead><tr><th></th><th>section</th><th>kind</th><th>direction</th><th>covered</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="6" class="muted">no Sync result: '+h(s.error||r.reason||'')+'</td></tr>'}</tbody></table>
      <div class="well small" style="margin-top:12px"><b>Policy for the next Run</b> — ${policy}</div></div></div>
      <div class="card"><div class="head"><h3 id="diff-title">Push preview${first?' · '+h(first.id):''}</h3><span class="small muted">deployed (−) → local (+) · exactly the bytes a push writes</span></div><div class="body"><div class="diff" id="diff-box">${first?diffHtml(first.diff):'<div class="h">'+h(p?'no section differs between the local files and the Connector\'s read':'no Connector: this Target is compared through its Adapter\'s probe and cannot be pushed')+'</div>'}</div>${p&&p.refusals.length?`<div class="small" style="margin-top:10px;color:var(--critical)">${p.refusals.map(x=>'refused: '+h(x)).join('<br>')}</div>`:''}<div class="small muted" style="margin-top:10px">Computed against a Connector read taken now (never a pull); the push carries the deployed Fingerprint it saw, and a deployed set that moved since is refused (ADR-0011 §6a). Afterwards the Fingerprint is rebuilt with <span class="mono">pushed_from</span> and a Push record is written.</div></div></div></div>
    <div class="card" style="margin-top:16px"><div class="head"><h3>Push records · ${h(s.target)}</h3><span class="small muted">every push, with who, how it was confirmed and both Fingerprints</span></div><div class="scroll-x"><table><thead><tr><th>when</th><th>env</th><th>sections</th><th>by</th><th>class · confirmed</th><th>Fingerprint</th><th>Restore point</th><th>Change record</th></tr></thead><tbody>${pushRows}</tbody></table></div></div>
    ${prov('sync --check · push preview · Sync breaks · pushes/*.json')}`;}
/* policyHtml: the broken-Sync policy radios, one choice shared by the Sync and Tests screens. */
function policyHtml(labels,sep){return labels.map(([k,label])=>`<label><input type="radio" name="pol" data-act="policy" data-policy="${k}" ${resyncPolicy===k?'checked':''}> ${label}</label>`).join(sep);}
function diffHtml(text){return String(text||'').replace(/\n$/,'').split('\n').map(l=>{const c=l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@')?'h':l.startsWith('+')?'a':l.startsWith('-')?'r':'ctx';return `<div class="${c}">${h(l)}</div>`;}).join('');}
function showDiff(id){const d=syncShown&&diffOf(syncShown,id);if(!d)return;document.getElementById('diff-title').textContent='Push preview · '+id;document.getElementById('diff-box').innerHTML=diffHtml(d.diff);}
function modal(body,foot){document.getElementById('modal-body').innerHTML=body;document.getElementById('modal-foot').innerHTML=foot;document.getElementById('modal').classList.add('on');}
function closeModal(){document.getElementById('modal').classList.remove('on');}
const closeFoot=(label='Close')=>`<button class="btn" data-act="close">${label}</button>`;
function refusedHtml(title,r){return `<h2>${h(title)}</h2><p style="color:var(--critical)">${h(r.body.error||('refused, status '+r.status))}</p>${r.status===409?'<p class="small muted">Preview again: the deployed set, or the preview, is not what was confirmed.</p>':''}`;}
async function openPush(sections){const s=syncShown;if(!s)return;if(!sections.length){modal('<h2>Push local → deployed</h2><p>Tick at least one local_ahead section.</p>',closeFoot());return;}
  modal('<h2>Push local → deployed</h2><p class="small muted">Reading the deployed set through the Connector…</p>',closeFoot('Cancel'));
  const r=await API.preview(s.target,{env:s.environment,sections});
  if(!r.ok){modal(refusedHtml('Push preview refused',r),closeFoot());return;}
  pushShown={id:r.body.preview_id,preview:r.body.preview,target:s.target};modal(pushModalHtml(s,r.body),`${closeFoot('Cancel')}<button class="btn pri" id="m-ok" data-act="confirm-push" disabled>Confirm push</button>`);}
function pushModalHtml(s,answer){const p=answer.preview;const prot=p.protected;const pushable=(s.change_records||[]).filter(c=>['proposed','pushed'].includes(c.status));
  const choose=`<select id="m-change">${prot?'':'<option value="none">none</option>'}${pushable.map(c=>`<option value="${h(c.id)}">${h(c.id)} (${h(c.status)}) · ${h(c.title)}</option>`).join('')}</select>${prot?` <span class="small muted">required for a protected environment${pushable.length?'':': no record is proposed or pushed; <code>change open</code> and <code>change propose</code> make one'}</span>`:''}`;
  const confirm=p.refusals.length?'':prot?`<div class="well small" style="margin-top:12px">Type the environment's name to confirm: <input type="text" id="m-input" autocomplete="off" oninput="confirmReady()" style="width:160px"> <span class="muted">no box stands in for this; a coding agent hands the push to a person</span></div>`:`<div class="well small" style="margin-top:12px"><label><input type="checkbox" id="m-ack" onclick="confirmReady()"> push: write to the ${h(p.effective_side_effects)} environment ${h(p.environment)} now (the UI's <code>--push</code>)</label></div>`;
  return `<h2>Push local → deployed</h2><p class="small muted" style="margin:6px 0 12px">Target <b>${h(s.target)}</b> · environment <b>${h(p.environment)}</b>${prot?' · <b style="color:var(--critical)">protected</b>':''} · effective class <b>${h(p.effective_side_effects)}</b></p>
    ${p.refusals.length?`<div class="well small" style="border-color:var(--critical)">${p.refusals.map(x=>'refused: '+h(x)).join('<br>')}</div>`:''}
    <dl class="kv small"><dt>Sections</dt><dd class="mono">${p.sections.map(d=>h(d.id)+' · '+h(d.file)).join('<br>')||'—'}</dd><dt>Expects</dt><dd>deployed Fingerprint <span class="mono">${h(short(p.expected_fingerprint))}</span>, read ${h(when(p.read_at))} (moved since → refused); a preview is good for ${h(answer.max_age_s)} s</dd><dt>Restore point</dt><dd><span class="mono">${h(p.restore_point||'—')}</span> · the deployed set as read before the write, kept</dd><dt>Change record</dt><dd>${choose}</dd><dt>Afterwards</dt><dd>Fingerprint rebuilt with <span class="mono">pushed_from</span> · Push record written · the Change record's push event points at it</dd></dl>
    <div class="diff" style="margin-top:12px;max-height:280px">${p.sections.map(d=>diffHtml(d.diff)).join('')}</div>${confirm}`;}
function confirmReady(){const ok=document.getElementById('m-ok');if(!ok||!pushShown)return;const p=pushShown.preview;if(p.protected){const i=document.getElementById('m-input');ok.disabled=!i||i.value.trim()!==p.environment;}else{const a=document.getElementById('m-ack');ok.disabled=!a||!a.checked;}}
async function doPush(){const shown=pushShown;if(!shown||!shown.id)return;const p=shown.preview;const change=(document.getElementById('m-change')||{}).value||'none';
  const confirm=p.protected?{typed_name:(document.getElementById('m-input')||{}).value||''}:{push:!!(document.getElementById('m-ack')||{}).checked};
  modal('<h2>Pushing…</h2><p class="small muted">Restore point, compare-and-swap write, Fingerprint, Push record.</p>','');
  const r=await API.push(shown.target,{preview_id:shown.id,change,confirm});
  if(!r.ok&&!r.body.push_record){modal(refusedHtml('Push refused',r),closeFoot());return;}
  const b=r.body;pushShown=null;
  modal(`<h2>${r.ok?'Pushed':'Pushed, with problems'}</h2>${r.ok?'':`<p style="color:var(--critical)">${h(b.error)}</p>`}<dl class="kv small"><dt>Sections</dt><dd class="mono">${(b.sections||[]).map(h).join('<br>')}</dd><dt>Confirmed by</dt><dd>${h(b.confirmed_by)}</dd><dt>Fingerprint</dt><dd class="mono">${h(short(b.fingerprint_before))} → ${h(short(b.fingerprint_after))} (pushed_from ${h(short(b.fingerprint_before))})</dd><dt>Deployed</dt><dd class="mono">${h(short(b.deployed_fingerprint_before))} → ${h(short(b.deployed_fingerprint_after))}</dd><dt>Restore point</dt><dd class="mono">${h(b.restore_point)}</dd><dt>Push record</dt><dd class="mono">${h(b.push_record)}</dd><dt>Change record</dt><dd class="mono">${h(b.change_record||'none')}</dd></dl>`,`<button class="btn pri" data-act="close-refresh">Close</button>`);}
async function openPull(section){const s=syncShown;if(!s)return;const secs=((s.result||{}).sections||[]).filter(x=>(x.direction==='deployed_ahead'||x.direction==='diverged')&&(!section||x.id===section));
  const diffs=secs.map(x=>diffOf(s,x.id)).filter(Boolean);
  modal(`<h2>Pull deployed → local</h2><p class="small muted">Target <b>${h(s.target)}</b> · environment <b>${h(s.environment)}</b></p><dl class="kv small"><dt>Writes</dt><dd class="mono">${secs.length?secs.map(x=>h(x.id)).join('<br>'):'nothing: no section reads deployed_ahead or diverged'}</dd><dt>Into</dt><dd>the files the Manifest points at</dd><dt>Never</dt><dd>commits · writes fingerprint.json · writes outside the Workspace root · overwrites a file with uncommitted changes unless asked below</dd></dl>${diffs.length?`<div class="small muted" style="margin-top:10px">The deployed side is the − lines; a pull writes it into the local file.</div><div class="diff" style="max-height:260px">${diffs.map(d=>diffHtml(d.diff)).join('')}</div>`:''}<div class="well small" style="margin-top:12px"><label><input type="checkbox" id="m-overwrite"> <code>--overwrite-local</code>: write a file even when it holds uncommitted changes</label></div>`,
    `${closeFoot('Cancel')}<button class="btn pri" data-act="confirm-pull" ${section?`data-sec="${h(section)}"`:''} ${secs.length?'':'disabled'}>Confirm pull</button>`);}
async function doPull(section){const s=syncShown;const body={env:s.environment,sections:section?[section]:[],overwrite_local:!!(document.getElementById('m-overwrite')||{}).checked};
  const r=await API.pull(s.target,body);if(!r.ok){modal(refusedHtml('Pull refused',r),closeFoot());return;}const res=r.body.result;
  modal(`<h2>Pulled</h2><table><thead><tr><th>section</th><th>file</th></tr></thead><tbody>${res.written.map(w=>`<tr><td class="mono">${h(w.section)}</td><td class="mono">${h(w.file)}</td></tr>`).join('')||'<tr><td colspan="2" class="muted">nothing written</td></tr>'}</tbody></table>${res.skipped.length?`<div class="small muted" style="margin-top:8px">${res.skipped.map(x=>'skipped '+h(x.section)+': '+h(x.reason)).join('<br>')}</div>`:''}${res.diff?`<div class="diff" style="margin-top:10px;max-height:260px">${diffHtml(res.diff)}</div>`:''}<div class="small muted" style="margin-top:8px">Nothing was committed; the next <code>sync</code> or Run settles the Fingerprint.</div>`,`<button class="btn pri" data-act="close-refresh">Close</button>`);}

/* ---------- 4 · tests: the picker, --dry-run, launch, follow, cancel ---------- */
/* selectionExpression: the expression run.json records for a selection (Selection.expression, D31): each kind's
   values sorted and de-duplicated, the kinds given in the order scenario, tag, suite, `all` when none is. */
const SELECTION_KINDS=['scenario','tag','suite'];
const selectionGiven=sel=>SELECTION_KINDS.map(k=>[k,[...new Set(sel[k]||[])].sort()]).filter(([,v])=>v.length);
function selectionExpression(sel){const given=selectionGiven(sel);return given.length?given.map(([k,v])=>`${k}=${v.join(',')}`).join(' '):'all';}
function selectionFlags(sel){return selectionGiven(sel).flatMap(([k,v])=>v.map(x=>`--${k} ${x}`)).join(' ')||'(no selection flag: all)';}
let picker={slug:null,suites:null,scenario:new Set(),tag:new Set(),suite:new Set(),search:'',env:null};
let running=null,runLog=['› tick Suites, tags or Scenarios, then Launch.'],runDone=null,progressShown=0;
const pickedSel=()=>Object.fromEntries(SELECTION_KINDS.map(k=>[k,[...picker[k]]]));
RENDER.tests=async function(args){const slug=await slugOf(args[0]);if(!slug)return noTarget('Tests');
  const [s,v]=await Promise.all([API.suites(slug),API.target(slug).catch(()=>null)]);
  if(picker.slug!==slug)picker={slug,suites:s,scenario:new Set(),tag:new Set(),suite:new Set(),search:'',env:null};else picker.suites=s;
  return testsHtml(s,v);};
function testsHtml(s,v){const entry=(v||{}).entry||{};const m=(v||{}).manifest||{};const sync=entry.sync||{};
  const tags=[...new Set(s.suites.flatMap(x=>x.scenarios.flatMap(c=>[...c.tags,...c.kinds])))].sort();
  const envs=runEnvironments(s).map(e=>`<option value="${h(e)}" ${e===pickedEnv(s)?'selected':''}>${h(e)}${e===s.default_environment?' (default)':''}</option>`).join('');
  const policy=policyHtml([['resync','re-sync'],['no_resync','--no-resync'],['strict','--strict']],' ');
  return `<div class="hd"><div><h1>Tests · ${h(s.target)}</h1><div class="sub">Tick by Suite, tag or id: one kind's values OR together, different kinds AND (D31). The expression below is what <code>run.json</code> records.</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><label class="small muted">env <select id="t-env" onchange="pickEnv(this.value)">${envs}</select></label><label class="small muted">trials <input type="text" id="t-trials" value="1" style="width:52px"></label><span class="small muted">broken Sync: ${policy}</span></div></div>
    <div class="two"><div class="card"><div class="head"><h3>Scenarios</h3><span class="small muted" id="sel-count"></span></div><div class="body">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px"><input type="search" id="t-search" placeholder="id or title…" style="flex:1;min-width:160px" oninput="picker.search=this.value;redrawPicker()">${tags.map(g=>`<span class="chip${picker.tag.has(g)?' on':''}" style="cursor:pointer" data-act="tag" data-tag="${h(g)}">#${h(g)}</span>`).join('')}</div>
      <div class="tree" id="tree">${treeHtml()}</div><div class="well small" style="margin-top:12px" id="sel-box">${selBoxHtml()}</div></div></div>
    <div class="grid" style="align-content:start"><div class="card"><div class="head"><h3>Launch</h3>${pill(sync.status||'not_checked',sync.status==='broken'?'Sync broken → '+(resyncPolicy==='resync'?'will re-sync':resyncPolicy):'Sync '+(sync.status||'not checked'))}</div><div class="body"><dl class="kv small" style="margin-bottom:12px"><dt>Adapter</dt><dd>${h((m.adapter||{}).kind)} · <span id="t-adapter-env">${h(pickedEnv(s))}</span> · side_effects <b>${h((m.adapter||{}).side_effects)}</b></dd><dt>Connector</dt><dd>${h(entry.connector||'none')}</dd><dt>Judge</dt><dd>the default Judge through the Backend a Run resolves (a live Run spends model calls)</dd></dl>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-act="dry-run">--dry-run</button><button class="btn pri" id="launch" data-act="launch" ${running?'disabled':''}>Launch Run</button><button class="btn" id="cancel" data-act="cancel" ${running?'':'disabled'}>Cancel</button></div></div></div>
      <div class="card"><div class="head"><h3>Live progress</h3><span class="small mono muted" id="run-id">${running?h(running.run_id):'no Run'}</span></div><div class="body"><div class="prog"><i id="prog" style="width:${Math.round(progressShown*100)}%"></i></div><div class="small muted" style="margin:6px 0 10px">The same Events <code>show --follow</code> reads, streamed as each Trial writes them.</div><div class="log" id="log">${h(runLog.join('\n'))}</div><div style="margin-top:10px;display:flex;gap:8px">${runDone?`<a class="btn sm" id="open-run" href="#run/${h(enc(runDone))}">Open Run report</a>`:''}</div></div></div></div></div>
    ${prov('the Suite files (load_suite) · run --dry-run · run · show --follow --json')}`;}
function treeHtml(){const s=picker.suites;if(!s)return '';const q=picker.search.toLowerCase();
  return s.suites.map(x=>{const items=x.scenarios.filter(c=>!q||c.id.toLowerCase().includes(q)||(c.title||'').toLowerCase().includes(q));
    return `<details open><summary><input type="checkbox" data-act="suite" data-suite="${h(x.name)}" ${picker.suite.has(x.name)?'checked':''} ${x.status==='runnable'?'':'disabled'}><b>${h(x.name)}</b><span class="kind">${h(x.status)}</span><span class="muted small">${items.length} of ${x.scenarios.length}${x.problem?' · '+h(x.problem):''}</span></summary>${items.map(c=>`<label class="row"><input type="checkbox" data-act="scenario" data-id="${h(c.id)}" ${picker.scenario.has(c.id)?'checked':''}><span class="id" title="${h(c.title)}">${h(c.id)}</span>${c.kinds.map(k=>`<span class="kind">${h(k)}</span>`).join('')}<span class="small muted">${c.tags.map(g=>'#'+h(g)).join(' ')}${c.not_run?' · not_run: '+h(c.not_run):''}</span></label>`).join('')}</details>`;}).join('');}
function selBoxHtml(){const sel=pickedSel();return `<b>Selection</b> <span class="mono" id="sel-expr">${h(selectionExpression(sel))}</span><div class="small muted mono" style="margin-top:4px" id="sel-flags">${h(selectionFlags(sel))}</div>`;}
function redrawPicker(){const t=document.getElementById('tree');if(t)t.innerHTML=treeHtml();const b=document.getElementById('sel-box');if(b)b.innerHTML=selBoxHtml();}
function log(text,reset){if(reset)runLog=[];runLog.push(text);const l=document.getElementById('log');if(l){l.textContent=runLog.join('\n');l.scrollTop=l.scrollHeight;}}
function progress(f){progressShown=f;const p=document.getElementById('prog');if(p)p.style.width=Math.round(f*100)+'%';}
/* runEnvironments: the Adapter's environments, the ones a Run can open (run --env); a Connector-only one is read and pushed to, never run. */
function runEnvironments(s){return s.adapter_environments||[];}
function pickedEnv(s){const envs=runEnvironments(s);return envs.includes(picker.env)?picker.env:s.default_environment;}
function pickEnv(value){picker.env=value;const a=document.getElementById('t-adapter-env');if(a)a.textContent=value;}
/* env is sent only when it is not the default, so a default launch is the command it always was. */
function launchBody(dry){const n=parseInt((document.getElementById('t-trials')||{}).value||'1',10);const env=picker.suites?pickedEnv(picker.suites):null;const other=env&&env!==picker.suites.default_environment;return Object.assign({target:picker.slug,trials:n>0?n:1,resync:resyncPolicy,dry_run:!!dry},other?{env}:{},pickedSel());}
function commandLine(body){return `› agentdiag run --target ${body.target} ${selectionFlags(body)}${body.env?' --env '+body.env:''}${body.trials>1?' --trials '+body.trials:''}${body.resync==='no_resync'?' --no-resync':body.resync==='strict'?' --strict':''}${body.dry_run?' --dry-run':''}`;}
async function dryRun(){const body=launchBody(true);log(commandLine(body),true);const r=await API.launch(body);if(!r.ok){log('✕ '+(r.body.error||'refused, status '+r.status));return;}const d=r.body.dry_run;
  d.selected.forEach(x=>log(`  ${x.id}  kinds ${x.kinds.join(',')||'—'}  suite ${x.suite}`));log(`  not run ${d.not_run.length}${d.not_run.length?' ('+[...new Set(d.not_run.map(x=>x.reason))].join(', ')+')':''}`);log(`  selection ${d.expression}`);if(d.environment)log(`  environment ${d.environment}`);if(d.evals_parsed!=null)log(`  evals ${d.evals_parsed} declaration(s), every parameter parsed`);log(`  sync ${(d.sync||{}).status||'not checked'} · nothing written`);}
async function launch(){if(running)return;const body=launchBody(false);runDone=null;progress(0);log(commandLine(body),true);
  running={run_id:'launching…',source:null};setRunning();const r=await API.launch(body);
  if(!r.ok){running=null;setRunning();log('✕ '+(r.body.error||'refused, status '+r.status));return;}
  running={run_id:r.body.run_id,source:null};log(`  Run ${r.body.run_id} · selection ${r.body.selection}`);setRunning();followRun(r.body.run_id);}
function setRunning(){const l=document.getElementById('launch'),c=document.getElementById('cancel'),i=document.getElementById('run-id');if(l)l.disabled=!!running;if(c)c.disabled=!running;if(i)i.textContent=running?running.run_id:(runDone||'no Run');}
/* eventLine: one Event of a followed Trial as the log shows it. */
function eventLine(ev){const t=ev.type;const text=v=>{const s=typeof v==='string'?v:JSON.stringify(v);return s.length>120?s.slice(0,120)+'…':s;};
  if(t==='span/start')return `${ev.span_id} ${ev.name||''} …`;if(t==='span/end')return `${ev.span_id} ${ev.status||'ok'}`;if(t==='message')return `${ev.role||ev.actor}: ${text(ev.content??'')}`;
  if(t==='trace/end')return `trace/end ${ev.termination||''}${ev.error?' · '+text(ev.error):''}`;if(t==='error')return `error ${text(ev.message||ev.error_type||'')}`;if(t==='trace/start'||t==='blob')return null;return `${t}${ev.span_id?' '+ev.span_id:''}`;}
function followRun(id){if(typeof EventSource==='undefined'){log('this browser cannot follow a Run; open it from the Run list when it ends');return;}
  const es=new EventSource('/api/runs/'+enc(id)+'/follow?token='+enc(TOKEN));running.source=es;
  const data=e=>JSON.parse(e.data);
  es.addEventListener('run',e=>{const d=data(e);log(`  ${d.trials.length} Trial(s) · selection ${d.selection}`);});
  es.addEventListener('trial',e=>{const d=data(e);progress((d.index-1)/d.of);log(`\n[${d.index}/${d.of}] ${d.scenario} / ${d.trial}`);});
  es.addEventListener('event',e=>{const line=eventLine(data(e).event);if(line)log('  '+line);});
  es.addEventListener('scores',e=>{const d=data(e);if(!d.scores){log('  not run');return;}d.scores.forEach(x=>log(`  ${x.eval}${x.eval_id?'['+x.eval_id+']':''} ${x.verdict}`));});
  es.addEventListener('finished',e=>{const d=data(e);es.close();progress(1);runDone=d.scorecard?d.run_id:null;running=null;log(`\n${d.scorecard?'✓':'✕'} Run ${d.run_id} ${d.scorecard?'finished':'wrote no Scorecard'}${d.code!=null?' · exit '+d.code:''}${d.message?'\n'+d.message:''}`);setRunning();if(parseHash().screen==='tests')route();});
  es.onerror=()=>{if(es.readyState===2)return;es.close();running=null;setRunning();log('\nthe follow stream closed; the Run goes on: open it from the Run list');};}
async function cancelRun(){if(!running)return;const r=await API.cancel(running.run_id);log(r.ok?`\n✕ cancel asked: ${r.body.detail}`:'✕ '+(r.body.error||'cancel refused'));}

/* ---------- 6 · compare ---------- */
/* The pickers hold only the shown Target's Runs: compare refuses two Targets, so offering another's would only lead
   to that refusal. The shown Target is the one the hash's Run belongs to, else the picker's; a hash naming Runs of two
   Targets is still compared, and the refusal says why. */
async function compareTarget(args){const named=args[1]||args[0];if(!named)return slugOf();
  const all=await API.runs().catch(()=>[]);const run=all.find(r=>r.run_id===named);return slugOf(run?run.target:undefined);}
RENDER.compare=async function(args,query){const slug=await compareTarget(args);const listing=(await API.runs(slug).catch(()=>[])).filter(r=>r.target===slug).sort((a,b)=>a.run_id<b.run_id?1:-1);
  const a=args[0]||(listing[1]||{}).run_id,b=args[1]||(listing[0]||{}).run_id;const expect=(query.expect||'').split(',').map(x=>x.trim()).filter(Boolean);
  let c=null,err=null;if(a&&b){try{c=await API.compare(a,b,expect);}catch(e){err=String(e.message||e);}}
  return compareHtml(listing,a,b,expect,c,err);};
function goCompare(extra){const a=document.getElementById('c-base').value,b=document.getElementById('c-run').value;const e=[...(document.getElementById('c-expect').value||'').split(','),...(extra?[extra]:[])].map(x=>x.trim()).filter(Boolean);location.hash='compare/'+enc(a)+'/'+enc(b)+(e.length?'?expect='+enc([...new Set(e)].join(',')):'');}
function compareHtml(listing,a,b,expect,c,err){const ids=listing.map(r=>r.run_id);const byId=Object.fromEntries(listing.map(r=>[r.run_id,r]));
  const opts=id=>ids.map(x=>{const r=byId[x]||{};return `<option value="${h(x)}" ${x===id?'selected':''}>${h(x)}${r.source?' · '+h(r.source):''}${r.fingerprint?' · '+h(short(r.fingerprint)):''}</option>`;}).join('');
  const unchanged=c?c.deltas.filter(d=>d.label==='unchanged'):[];
  const body=!c?`<div class="card"><div class="body ${err?'':'muted'}">${err?h(err):'Pick two Runs of this Target: the Baseline and the Run compared with it.'}</div></div>`:`<div class="two"><div class="card" data-section="differences"><div class="head"><h3>Configuration diff</h3><span class="small muted">declared vs undeclared${c.shared_traces?' · shared Traces':''}</span></div><div class="body">${c.differences.length?`<table><thead><tr><th>field</th><th>Baseline</th><th>Run</th><th></th></tr></thead><tbody>${c.differences.map(d=>`<tr><td class="mono">${h(d.path)}</td><td class="mono small">${h(JSON.stringify(d.baseline))}</td><td class="mono small">${h(JSON.stringify(d.run))}</td><td>${d.declared?'<span class="chip">declared</span>':`<span class="chip" style="border-color:var(--warning)">undeclared</span> <button class="btn sm" data-act="expect" data-path="${h(d.path)}">--expect</button>`}</td></tr>`).join('')}</tbody></table>`:'<div class="muted">no configuration difference</div>'}<div class="well small" style="margin-top:12px">${h(c.summary)}${c.judge_statements.map(x=>'<br>'+h(x)).join('')}${(c.cites_read.baseline||c.cites_read.run)?`<br>cites read: ${h(c.cites_read.baseline)} / ${h(c.cites_read.run)}`:''}</div></div></div>
    <div class="card" data-section="deltas"><div class="head"><h3>Scores per Scenario · intersection ${c.scenarios.intersection.length}</h3><span class="small muted">${Object.entries(c.counts).map(([k,v])=>h(k)+' '+h(v)).join(' · ')}</span></div><div class="scroll-x"><table><thead><tr><th>Scenario · Eval</th><th>Baseline</th><th>Run</th><th>Δ</th></tr></thead><tbody>${c.deltas.filter(d=>d.label!=='unchanged').concat(unchanged.slice(0,6)).map(d=>`<tr><td class="mono small">${h(d.scenario)}<div class="muted">${h(d.eval)}${d.eval_id?'['+h(d.eval_id)+']':''}</div></td><td>${d.baseline?pct(d.baseline.mean):'—'}</td><td>${d.run?pct(d.run.mean):'—'}</td><td class="${d.label==='regression'?'ne':d.label==='improvement'?'':'muted'}">${h(d.label)}${d.delta!=null&&d.delta!==0?' '+(d.delta>0?'▲':'▼')+Math.abs(d.delta).toFixed(2):''}${d.because.length?' <span class="small muted">('+d.because.map(h).join(', ')+')</span>':''}</td></tr>`).join('')}${unchanged.length>6?`<tr><td colspan="4" class="muted">${unchanged.length-6} more unchanged</td></tr>`:''}${c.scenarios.only_in_baseline.length||c.scenarios.only_in_run.length?`<tr><td colspan="4" class="muted">only in Baseline: ${c.scenarios.only_in_baseline.length} · only in Run: ${c.scenarios.only_in_run.length} · no aggregate over mismatched sets</td></tr>`:''}</tbody></table></div></div></div>`;
  return `<div class="hd"><div><h1>Compare</h1><div class="sub">Baseline vs Run. The configuration diff comes first; "regression" is said only when nothing undeclared varied (ADR-0005 §6).</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><label class="small muted">Baseline <select id="c-base" onchange="goCompare()">${opts(a)}</select></label><label class="small muted">Run <select id="c-run" onchange="goCompare()">${opts(b)}</select></label><label class="small muted">--expect <input type="text" id="c-expect" value="${h(expect.join(','))}" placeholder="judge,fingerprint" style="width:180px"></label><button class="btn" data-act="compare">Compare</button></div></div>
    ${body}${prov('compare(baseline, run, expect) → Comparison')}`;}

/* ---------- the one delegated handler of every served screen's buttons ---------- */
function onAction(e){const el=e.target.closest&&e.target.closest('[data-act]');if(!el)return;const d=el.dataset;const act=d.act;
  if(act==='diff')showDiff(d.sec);else if(act==='push')openPush([d.sec]);else if(act==='push-ticked')openPush([...document.querySelectorAll('input[data-sec]:checked')].map(c=>c.dataset.sec));
  else if(act==='pull')openPull(d.sec||null);else if(act==='confirm-pull')doPull(d.sec||null);else if(act==='confirm-push')doPush();
  else if(act==='close')closeModal();else if(act==='close-refresh'){closeModal();route();}else if(act==='refresh')route();
  else if(act==='policy')resyncPolicy=d.policy;
  else if(act==='suite'){picker.suite.has(d.suite)?picker.suite.delete(d.suite):picker.suite.add(d.suite);redrawPicker();}
  else if(act==='scenario'){picker.scenario.has(d.id)?picker.scenario.delete(d.id):picker.scenario.add(d.id);redrawPicker();}
  else if(act==='tag'){picker.tag.has(d.tag)?picker.tag.delete(d.tag):picker.tag.add(d.tag);el.classList.toggle('on',picker.tag.has(d.tag));redrawPicker();}
  else if(act==='dry-run')dryRun();else if(act==='launch')launch();else if(act==='cancel')cancelRun();
  else if(act==='compare')goCompare();else if(act==='expect')goCompare(d.path);}

/* ---------- 5 · run report, with the flame graph ---------- */
let shownRun=null,shownTrial=null,runRecords=[];
/* openedFrom: the Change records already opened from this Trial's Diagnosis (served only). */
const openedFrom=(run,s)=>runRecords.filter(c=>c.trigger&&c.trigger.run===run&&c.trigger.scenario===s.scenario_id&&c.trigger.trial===s.trial);
const VARIANTS=[['BC','Icicle per Turn, then the ladder'],['A','Actor lanes · wall time'],['B','Icicle per Turn · depth'],['C','Ladder · Event order with bars']];
let variant='BC',citedSpans=new Set();
function cycleVariant(d){if(parseHash().screen!=='run'&&parseHash().screen!=='report')return;const i=VARIANTS.findIndex(v=>v[0]===variant);variant=VARIANTS[(i+d+VARIANTS.length)%VARIANTS.length][0];const {screen,args}=parseHash();location.hash=screen+'/'+args.join('/')+'?variant='+variant;}
RENDER.run=async function(args,query,report){
  const listing=await API.runs(currentTarget);const id=args[0]||(listing[0]||{}).run_id;if(!id)return '<div class="card"><div class="body">no Run</div></div>';
  const r=await API.run(id);currentTarget=r.header.target||currentTarget;shownRun=r.header.run_id;
  runRecords=EMBEDDED||!r.header.target?[]:await API.changes(r.header.target).catch(()=>[]);
  return runHtml(r,args,query,report,listing);
};
function runHtml(r,args,query,report,listing){shownTrial=null;
  if(query.variant&&VARIANTS.find(v=>v[0]===query.variant))variant=query.variant;
  const hd=r.header;const sc=r.scorecard;const sync=r.sync||{};const tIdx=Math.max(0,r.trials.findIndex(x=>x.scenario_id===args[1]&&String(x.trial)===(args[2]||'1')));const trial=r.trials[tIdx];if(trial)shownTrial=[trial.scenario_id,String(trial.trial)];
  const decidable=(sc.counts.pass||0)+(sc.counts.fail||0);const abnormal=(sc.counts.incomplete||0)+(sc.counts.unverifiable||0)+(sc.counts.invalid||0);
  const cost=r.trials.reduce((a,x)=>{Object.entries(x.costs||{}).forEach(([k,v])=>a[k]=(a[k]||0)+(v.cost_usd||0));return a;},{});const dur=r.trials.reduce((a,x)=>a+(x.duration_ms||0),0);
  const evals={};sc.scenarios.forEach(line=>line.scores.forEach(s=>{const k=s.eval+(s.eval_id?'['+s.eval_id+']':'');evals[k]=evals[k]||{pass:0,fail:0,incomplete:0,unverifiable:0,invalid:0,n:0};evals[k][s.verdict]++;evals[k].n++;}));
  const evalRows=Object.entries(evals).map(([k,v])=>`<tr><td class="mono">${h(k)}</td><td style="width:34%">${verdictBar(v)}</td>${VERDICTS.map(x=>`<td class="num">${v[x]||0}</td>`).join('')}</tr>`).join('');
  const trialsOf=id=>sc.scenarios.filter(l=>l.id===id).sort((p,q)=>p.trial-q.trial);const worstOf=lines=>VERDICTS.slice().reverse().find(v=>lines.some(l=>l.scores.some(s=>s.verdict===v)))||'pass';const trialHref=(id,n)=>`#${report?'report':'run'}/${h(hd.run_id)}/${h(id)}/${n}?variant=${variant}`;const scorePills=line=>line.scores.map(s=>`<span class="pill ${h(s.verdict)}" title="${h(s.reason||'')}"><i></i>${h(s.eval)}${s.eval_id?'['+h(s.eval_id)+']':''}</span>`).join(' ');
  const scRows=sc.scenario_aggregates.map(a=>{const lines=trialsOf(a.id);const line=lines[0]||{trial:1,scores:[]};const worst=worstOf(lines);return `<tr class="${a.id===trial?.scenario_id?'sel':''}"><td><a class="mono" href="${trialHref(a.id,line.trial)}">${h(a.id)}</a><div class="small muted">${h(a.suite||'')}</div></td><td>${pill(worst,worst)}</td><td class="num">${a.passed} / ${a.decidable} of ${a.trials}</td><td class="num">${Object.entries(a.pass_k).map(([k,v])=>`pass^${k} ${v==null?'—':v.toFixed(2)}`).join(' · ')}</td><td class="small">${scorePills(line)}</td></tr>`;}).join('');
  const trialRows=sc.scenarios.map(l=>`<tr class="${l.id===trial?.scenario_id&&l.trial===trial?.trial?'sel':''}"><td><a class="mono" href="${trialHref(l.id,l.trial)}">${h(l.id)} / ${l.trial}</a></td><td>${pill(worstOf([l]),worstOf([l]))}</td><td class="small">${scorePills(l)}</td></tr>`).join('');
  const trialPicker=hd.trials>1?`<div class="card" style="margin-bottom:16px" data-section="trials"><div class="head"><h3>Trials · ${sc.scenarios.length}</h3><span class="small muted">click one to open it below</span></div><div class="scroll-x"><table><thead><tr><th>Scenario / Trial</th><th>verdict</th><th>Scores</th></tr></thead><tbody>${trialRows}</tbody></table></div></div>`:'';
  const notRun=r.not_run.length?`<div class="card" style="margin-bottom:16px" data-section="not-run"><div class="head"><h3>What did not happen · ${r.not_run.length} Scenario(s) not run</h3></div><div class="scroll-x"><table><thead><tr><th>Scenario</th><th>Suite</th><th>reason</th><th>detail</th></tr></thead><tbody>${r.not_run.map(n=>`<tr><td class="mono">${h(n.scenario)}</td><td>${h(n.suite||'')}</td><td>${h(n.reason)}</td><td class="muted">${h(n.detail||'')}</td></tr>`).join('')}</tbody></table></div></div>`:'';
  const syncBlock=(sync.resynced_from||sync.status==='broken'||sync.connector_failed)?`<div class="card" style="margin-bottom:16px;border-color:var(--warning)" data-section="sync"><div class="head"><h3>Sync ${h(sync.status)}${sync.resynced_from?' · re-synced from '+h(short(sync.resynced_from)):''}</h3><span class="small muted">said before anything else (ADR-0008)</span></div><div class="body"><table class="drift"><thead><tr><th>section</th><th>direction</th><th>change</th><th>local</th><th>deployed</th><th>recorded</th></tr></thead><tbody>${(sync.sections||[]).map(s=>`<tr><td>${h(s.id)}</td><td class="ne">${h(s.direction)}</td><td>${h(s.change||'')}</td><td>${h(short(s.local))}</td><td>${h(short(s.deployed))}</td><td>${h(short(s.recorded))}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">no section listed</td></tr>'}</tbody></table>${sync.connector_failed?`<div class="small" style="margin-top:8px;color:var(--critical)">Connector read failed: ${h(sync.connector_failed)}</div>`:''}</div></div>`:'';
  const importedLine=hd.importer?importerHtml(hd.importer):'';
  const picker=listing.length>1?`<select onchange="location.hash='${report?'report':'run'}/'+this.value+'?variant=${variant}'">${listing.slice().sort((a,b)=>a.run_id<b.run_id?1:-1).map(x=>`<option value="${h(x.run_id)}" ${x.run_id===hd.run_id?'selected':''}>${h(x.run_id)} · ${h(x.target)} · ${h(x.source)}</option>`).join('')}</select>`:'';
  const links=EMBEDDED?'':report?`<a class="btn" href="#run/${h(hd.run_id)}">open in the UI</a>`:`<a class="btn" href="#report/${h(hd.run_id)}">as the Report file</a><a class="btn" href="#compare/${h(hd.run_id)}">compare…</a>`;
  return `<div class="hd"><div><div class="muted small mono">${h(hd.run_id)} · ${h(hd.target)} · ${h(hd.environment||'')} · source ${h(hd.source)}${hd.traces_from?' · Traces from '+h(hd.traces_from):''}</div><h1>${report?'Report':'Run report'}</h1>
      <div class="sub">${h(when(hd.created_at))} · Adapter ${h(hd.adapter_kind)} (${h(hd.side_effects)}) · Target Backend ${h(hd.target_backend||'—')} · Judge ${h(hd.judge_model||'none')} via ${h(hd.judge_backend||'—')} · trials ${hd.trials} · selection <span class="mono">${h(hd.selection)}</span> · ${pill(sync.status||'not_checked','Sync '+(sync.status||'not checked')+(sync.reason?' · '+sync.reason:''))} · Fingerprint <span class="mono">${h(short(hd.fingerprint))}</span>${Object.values(hd.git_dirty||{}).some(Boolean)?' · <b style="color:var(--warning)">git dirty</b>':''}</div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">${picker}${links}</div></div>
    ${syncBlock}${importedLine}
    <div class="grid g4" style="margin-bottom:16px" data-section="scorecard">
      <div class="card stat"><div class="l">Scenarios run</div><div class="v">${sc.scenario_aggregates.length}</div><div class="d">${sc.scenarios.length} Trial(s) · not run ${r.not_run.length} · excluded ${sc.excluded_trials}</div></div>
      <div class="card stat"><div class="l">Pass rate</div><div class="v">${pct(sc.pass_rate)}</div><div class="d">${sc.counts.pass} pass · ${sc.counts.fail} fail · of ${decidable} decidable Scores · ${Object.entries(sc.pass_k).map(([k,v])=>'pass^'+k+' '+(v==null?'—':v.toFixed(2))).join(' · ')}</div></div>
      <div class="card stat"><div class="l">What did not happen</div><div class="v">${abnormal}</div><div class="d">${sc.counts.incomplete} incomplete · ${sc.counts.unverifiable} unverifiable · ${sc.counts.invalid} invalid${sc.shared_model?' · <b>shared model</b>':''}${sc.cites_read?' · cites read '+sc.cites_read:''}</div></div>
      <div class="card stat"><div class="l">Cost · Target / Judge</div><div class="v">${fmtUsd(Object.values(cost).reduce((a,b)=>a+b,0))}</div><div class="d">${Object.entries(cost).map(([k,v])=>h(k)+' '+fmtUsd(v)).join(' · ')||'unpriced'} · ${fmtMs(dur)} of Trace time</div></div></div>
    <div class="card" style="margin-bottom:16px"><div class="head"><h3>Scorecard · per Eval</h3><span class="legend">${VERDICTS.map(v=>`<span><i class="${VCLS[v]}"></i>${v}</span>`).join('')}</span></div><div class="scroll-x"><table><thead><tr><th>Eval</th><th>Verdicts</th>${VERDICTS.map(v=>`<th class="num">${v.slice(0,5)}</th>`).join('')}</tr></thead><tbody>${evalRows}</tbody></table></div></div>
    ${r.change_record?changeCardHtml(r.change_record):''}
    <div class="card" style="margin-bottom:16px"><div class="head"><h3>Scenarios · ${sc.scenario_aggregates.length}</h3><span class="small muted">click one to open its Trial below</span></div><div class="scroll-x"><table><thead><tr><th>Scenario</th><th>Trial verdict</th><th class="num">passed / decidable of Trials</th><th class="num">pass^k</th><th>Scores</th></tr></thead><tbody>${scRows}</tbody></table></div></div>
    ${trialPicker}${notRun}
    ${trial?trialHtml(r,trial,report):''}`;
}
function importerHtml(imp){const q=imp.query||{};const query=[...['conversation_id','since','until','limit'].filter(k=>q[k]!=null).map(k=>k+' '+q[k]),...Object.entries(q.extra||{}).map(([k,v])=>k+' '+(typeof v==='string'?v:JSON.stringify(v)))].join(' · ')||'—';const counts={};(imp.lacked||[]).forEach(l=>{counts[l.field]=(counts[l.field]||0)+1;});const lacked=Object.entries(counts).map(([k,n])=>`${k} (${n})`).join(', ')||'nothing';
  return `<div class="well small" style="margin-bottom:16px" data-section="imported"><b>Imported</b><dl class="kv" style="margin-top:6px"><dt>evidence</dt><dd>${h(imp.kind)} · ${h(imp.fidelity)}</dd><dt>query</dt><dd class="mono">${h(query)}</dd><dt>read at</dt><dd>${h(when(imp.evidence_read_at))}</dd><dt>lacked</dt><dd>${h(lacked)}</dd></dl></div>`;}
/* spanRows: the Trial's Spans in the flat shape the flame graph draws: the story's Turn tree walked in
   order, each Span tagged with the Turn it sits in (null outside every Turn). */
function spanRows(s){const spans=[];const walk=(v,turn)=>{if(!v||v.span_id==null)return;spans.push(Object.assign({},v,{turn}));(v.children||[]).forEach(c=>walk(c,turn));};
  s.turns.forEach(t=>{const n=t.index>0?t.index:null;(t.before||[]).forEach(v=>walk(v,null));walk(t.span,n);(t.spans||[]).forEach(v=>walk(v,n));(t.after||[]).forEach(v=>walk(v,null));});
  const messages=s.messages||[];const ends=[...spans.map(z=>z.end_ms??z.start_ms),...messages.map(m=>m.at_ms)];
  return {duration_ms:ends.length?Math.max(...ends):0,spans,messages};}
function trialHtml(r,s,report){const sr=s.turns.length?spanRows(s):null;const scores=s.scores||[];
  const turns=s.turns.map(t=>`<div><div class="small muted">Turn ${t.index}${t.simulated?' · Simulated User':''}${t.operator?' · operator':''} · ${fmtMs(t.duration_ms)}</div>${t.user!=null?`<div class="turnmsg user">${h(t.user)}</div>`:''}${t.assistant!=null?`<div class="turnmsg assistant">${h(t.assistant)}</div>`:''}</div>`).join('');
  const scoreRows=scores.map((sc,i)=>`<div class="evrow" id="score-${i}" data-cites="${h(JSON.stringify(sc.evidence))}" style="cursor:pointer"><span class="icon" style="color:${sc.verdict==='pass'?'var(--good)':sc.verdict==='fail'?'var(--critical)':'var(--neutral)'}">${sc.verdict==='pass'?'✓':sc.verdict==='fail'?'✕':sc.verdict==='incomplete'?'◔':sc.verdict==='invalid'?'⚠':'?'}</span><span class="v mono">${h(sc.eval)}${sc.eval_id?'['+h(sc.eval_id)+']':''}</span><span>${h(sc.rationale||sc.reason||'')}${sc.reason&&sc.rationale?' <span class="muted">('+h(sc.reason)+')</span>':''}${sc.fault_source?' <span class="muted">· fault '+h(sc.fault_source)+'/'+h(sc.fault_direction||'')+'</span>':''}</span><span class="cites">${sc.evidence.map(e=>`<span class="cite">${h(e)}</span>`).join(' ')}${sc.cites_read.length?` <span class="small muted">read as ${sc.cites_read.map(h).join(', ')}</span>`:''}</span></div>`).join('')||'<div class="muted small">no Scores'+(s.scores===null?' yet (scores.json not written)':'')+'</div>';
  const diag=s.diagnosis?`<p style="margin:0 0 10px;line-height:1.6" data-section="diagnosis">${h(s.diagnosis.text).replace(/\b([a-z_]+-\d+)\b/g,(m0)=>`<span class="cite" data-cites="${h(JSON.stringify([m0]))}">${m0}</span>`)}</p><div class="small muted">cites ${s.diagnosis.cites.map(h).join(', ')||'—'}${s.diagnosis.cites_read.length?' · read as '+s.diagnosis.cites_read.map(h).join(', '):''}</div>`:'<span class="muted small">no Diagnosis: the Trial judged nothing</span>';
  const judge=s.judgement.length?`<table><thead><tr><th>Eval</th><th>model</th><th class="num">duration</th><th class="num">tokens</th><th class="num">cost</th><th>status</th></tr></thead><tbody>${s.judgement.map(j=>`<tr><td class="mono">${h(j.eval)}</td><td class="mono small">${h(j.resolved_model||j.requested_model)}</td><td class="num">${fmtMs(j.duration_ms)}</td><td class="num">${j.tokens.total||'—'}</td><td class="num">${fmtUsd(j.cost_usd)}${j.cost_check?' <span class="muted small">'+h(j.cost_check)+'</span>':''}${j.attempts?' · '+j.attempts+' attempts':''}</td><td>${h(j.status)}${j.error?' · '+h(j.error):''}${j.note?' · '+h(j.note):''}</td></tr>`).join('')}</tbody></table>`:'<span class="muted small">no Judge call</span>';
  const scenarioLine=`${s.provenance?'provenance '+h(s.provenance)+' · ':''}${s.notes?h(s.notes)+' · ':''}${s.fixtures.length?'fixtures '+s.fixtures.map(h).join(', '):''}`;
  return `<div class="card" style="margin-bottom:16px" data-section="trial"><div class="head"><div><h3>Trial · <span class="mono">${h(s.scenario_id)} / ${s.trial}</span></h3><div class="small muted">${h(s.termination||'running')}${s.termination_detail?' · '+h(s.termination_detail):''} · ${fmtMs(s.duration_ms)} · tokens ${s.tokens.total||0} · ${Object.entries(s.costs).map(([k,v])=>h(k)+' '+fmtUsd(v.cost_usd)+(v.unpriced?' (+'+v.unpriced+' unpriced)':'')).join(' · ')}${s.imported?' · '+h(s.imported):''}${s.rescored_from?' · Scores read the Trace of '+h(s.rescored_from):''}${s.continues?' · continues '+h(s.continues):''} ${scenarioLine?'· '+scenarioLine:''}</div></div>
      <span class="legend"><span><i style="background:var(--s1)"></i>llm_call · response</span><span><i style="background:var(--s2)"></i>tool_call</span><span><i style="background:var(--s3)"></i>retrieval</span><span><i style="background:var(--s4)"></i>judge</span><span><i class="hatch" style="border:1px dashed var(--ink-3)"></i>simulated_user</span><span class="mono small">● instrumented ◐ reconstructed ○ observed</span></span></div>
    <div class="body" data-section="flame">${sr?flame(sr,s):'<span class="muted">no Trace (a rescore whose source is gone)</span>'}<div class="small muted" style="margin-top:8px">Flame graph <b>${h(variant)}</b>: ${h(VARIANTS.find(v=>v[0]===variant)[1])}. ${r.header.target_backend==='replay'?'<b>Replay Run: the Target answered from a recording, so every Span is a few ms and time is flat.</b>':r.header.source==='imported'?'<b>Imported Run: the times are the evidence\'s, at the Fidelity it supports.</b>':''} Hover a Span for its attributes; click a Score's cite to highlight the Spans it cites.</div></div></div>
    <div class="two" style="margin-bottom:16px"><div class="card"><div class="head"><h3>Scores · this Trial</h3><span class="small muted">every judged Score cites Span ids</span></div><div class="body">${scoreRows}</div></div>
      <div class="card"><div class="head"><h3>Diagnosis</h3><span class="small mono muted">${s.judge_backend?.kind?'Judge via '+h(s.judge_backend.kind):''}</span></div><div class="body">${diag}${!EMBEDDED&&s.diagnosis?`<div class="well small" style="margin-top:10px" data-section="open-record">Open a Change record from this Diagnosis: <code>agentdiag change open --target ${h(r.header.target)} --from ${h(r.header.run_id)}/${h(s.scenario_id)}/${h(s.trial)} --layer &lt;layer&gt; --title "&lt;title&gt;"</code></div>`:''}${EMBEDDED?'':openedFrom(r.header.run_id,s).map(c=>`<div class="small" style="margin-top:6px" data-section="opened-record">Change record opened from this Trial: <a class="mono" href="#flow/${h(enc(c.id))}">${h(c.id)}</a> ${pill(c.status)} ${h(c.title)}</div>`).join('')}<div style="margin-top:12px"><h3 style="font-size:14px;margin-bottom:6px">Judge calls</h3>${judge}</div></div></div></div>
    <div class="card"><div class="head"><h3>Turns · ${s.turns.length}</h3><span class="small muted">the story <code>show</code> prints, from the same TrialStory</span></div><div class="body">${turns||'<span class="muted">none</span>'}</div></div>`;}
function kindOf(sp){return sp.kind||sp.span_id.replace(/-\d+$/,'')||'other';}
function spanClass(sp){const k=kindOf(sp);const c=k==='response'?'llm_call response':['turn','llm_call','tool_call','retrieval','simulate','judge','review','action'].includes(k)?k:sp.actor==='simulated_user'?'simulated_user':sp.actor==='judge'?'judge':'other';return `${c} ${sp.fidelity}${sp.status==='error'?' err':''}${citedSpans.size?(citedSpans.has(sp.span_id)?' cited':' dim'):''}`;}
function tip(sp){const facts=[sp.stop_reason?'stop '+sp.stop_reason:'',sp.model_latency_ms!=null?'model '+fmtMs(sp.model_latency_ms):'',sp.startup_ms!=null?'start-up '+fmtMs(sp.startup_ms):'',(sp.attributes||{})['agentdiag.time_to_first_frame_ms']!=null?'first frame '+fmtMs((sp.attributes||{})['agentdiag.time_to_first_frame_ms']):'',(sp.not_observed||[]).length?'not observed: '+sp.not_observed.join(', '):''].filter(Boolean).join(' · ');const a=Object.entries(sp.attributes||{}).filter(([k])=>!/^llm\.cost\.(prompt|completion)|prompt_details/.test(k)).slice(0,10).map(([k,v])=>`${k}: ${String(v).slice(0,60)}`).join('\n');return `${sp.span_id} · ${sp.name}\n${kindOf(sp)} · ${sp.actor} · ${sp.fidelity} · ${sp.status}\n${fmtMs(sp.end_ms==null?null:sp.end_ms-sp.start_ms)} · tokens ${(sp.tokens||{}).total||0} · ${fmtUsd(sp.cost_usd)}${facts?'\n'+facts:''}${a?'\n'+a:''}`;}
function flame(sr,s){if(!sr.spans.length)return '<span class="muted">no Spans</span>';if(variant==='A')return flameA(sr,s);if(variant==='B')return flameB(sr);if(variant==='C')return flameC(sr,s);return flameB(sr)+flameC(sr,s);}
function flameA(sr,s){const W=1000,L=120,T=Math.max(sr.duration_ms,1);const x=t=>L+t/T*(W-L-10);const actors=['agentdiag','target','simulated_user','adapter','operator'];const laneRows={};
  actors.forEach(a=>{const sp=sr.spans.filter(z=>z.actor===a).sort((p,q)=>p.start_ms-q.start_ms||p.depth-q.depth);if(!sp.length)return;const ends=[];sp.forEach(z=>{let r=0;while(r<ends.length&&ends[r]>z.start_ms)r++;ends[r]=z.end_ms??T;z._row=r;});laneRows[a]=ends.length;});
  let y=22,out='';const H=22;for(let t=0;t<=T;t+=T/8){out+=`<line class="grid" x1="${x(t)}" y1="14" x2="${x(t)}" y2="{{H}}"/><text x="${x(t)}" y="12" class="lane" text-anchor="middle">${fmtMs(Math.round(t))}</text>`;}
  actors.forEach(a=>{if(!laneRows[a])return;out+=`<text x="4" y="${y+14}" class="lane">${a}</text>`;sr.spans.filter(z=>z.actor===a).forEach(z=>{const x0=x(z.start_ms),w=Math.max(3,x((z.end_ms??T))-x0);const yy=y+z._row*H;out+=`<rect class="${spanClass(z)}" data-id="${h(z.span_id)}" x="${x0}" y="${yy}" width="${w}" height="${H-4}" rx="3" data-tip="${h(tip(z))}"/>${w>50?`<text x="${x0+4}" y="${yy+13}" class="lbl${kindOf(z)==='turn'||z.actor==='simulated_user'?' dark':''}">${h((z.span_id+' '+(z.name||'')).slice(0,Math.floor(w/6.5)))}</text>`:''}`;});y+=laneRows[a]*H+8;});
  if(s.judgement.length){out+=`<text x="4" y="${y+14}" class="lane">judge · after</text>`;let jx=x(0);s.judgement.forEach(j=>{const w=Math.max(40,(j.duration_ms||0)/T*(W-L-10));out+=`<rect class="judge instrumented" data-id="${h(j.span_id)}" x="${jx}" y="${y}" width="${Math.min(w,W-jx-10)}" height="${H-4}" rx="3" data-tip="${h(j.span_id+' · '+j.eval+'\n'+fmtMs(j.duration_ms)+' · '+fmtUsd(j.cost_usd)+' · after the Trial, own file')}"/><text x="${jx+4}" y="${y+13}" class="lbl">${h(j.eval.slice(0,Math.floor(w/6.5)))}</text>`;jx+=Math.min(w,W-jx-10)+2;});y+=H+8;}
  return `<svg class="flame" viewBox="0 0 ${W} ${y}">${out.replace(/{{H}}/g,y)}</svg>`;}
function flameB(sr){const W=1000;const turns=[...new Set(sr.spans.map(z=>z.turn).filter(z=>z!=null))].sort((a,b)=>a-b);const groups=turns.length?turns.map(t=>sr.spans.filter(z=>z.turn===t)):[sr.spans];const outside=sr.spans.filter(z=>z.turn==null&&turns.length);if(outside.length)groups.push(outside);
  let out='',y=6;const H=22;groups.forEach((g,gi)=>{const t0=Math.min(...g.map(z=>z.start_ms)),t1=Math.max(...g.map(z=>z.end_ms??z.start_ms+1));const T=Math.max(t1-t0,1);const x=t=>110+(t-t0)/T*(W-120);const depths=Math.max(...g.map(z=>z.depth))+1;
    out+=`<text x="4" y="${y+14}" class="lane">${turns.length&&gi<turns.length?'Turn '+turns[gi]:'outside Turns'}</text><text x="4" y="${y+28}" class="lane">${fmtMs(T)}</text>`;
    g.forEach(z=>{const x0=x(z.start_ms),w=Math.max(3,x(z.end_ms??t1)-x0);const yy=y+z.depth*H;out+=`<rect class="${spanClass(z)}" data-id="${h(z.span_id)}" x="${x0}" y="${yy}" width="${w}" height="${H-3}" rx="2" data-tip="${h(tip(z))}"/>${w>40?`<text x="${x0+4}" y="${yy+13}" class="lbl${kindOf(z)==='turn'||z.actor==='simulated_user'?' dark':''}">${h((z.span_id+' '+(z.name||'')).slice(0,Math.floor(w/6.5)))}</text>`:''}`;});y+=depths*H+12;});
  return `<svg class="flame" viewBox="0 0 ${W} ${y}">${out}</svg><div class="small muted">Each Turn is scaled to its own width (the label says its wall time), so nesting reads even when one Turn dwarfs the rest or every Span is a millisecond.</div>`;}
function flameC(sr,s){const T=Math.max(sr.duration_ms,1);const items=[...sr.spans.map(z=>({t:z.start_ms,z})),...sr.messages.map(m=>({t:m.at_ms,m}))].sort((a,b)=>a.t-b.t||((a.z?a.z.depth:99)-(b.z?b.z.depth:99)));
  const rows=items.map(it=>{if(it.m)return `<tr class="msg"><td></td><td colspan="4">${h(it.m.role||it.m.actor)}: ${h(it.m.text.slice(0,140))}${it.m.text.length>140?'…':''}</td><td></td></tr>`;const z=it.z;const cited=citedSpans.has(z.span_id);const l=(z.start_ms/T*100).toFixed(2),w=Math.max(0.6,((z.end_ms??T)-z.start_ms)/T*100).toFixed(2);const col=kindOf(z)==='llm_call'||kindOf(z)==='response'?'var(--s1)':kindOf(z)==='retrieval'?'var(--s3)':/tool|action/.test(kindOf(z))?'var(--s2)':z.actor==='judge'?'var(--s4)':'var(--line-strong)';return `<tr class="${citedSpans.size?(cited?'cited':'dim'):''}" data-id="${h(z.span_id)}" onmousemove="showTT(event,this.dataset.tip)" onmouseleave="hideTT()" data-tip="${h(tip(z))}"><td class="mono">${h(z.span_id)}</td><td style="padding-left:${10+z.depth*18}px">${'└ '.repeat(z.depth?1:0)}${h(z.name||kindOf(z))} <span class="muted">${FID[z.fidelity]||''}</span></td><td>${h(z.actor)}</td><td class="num">${fmtMs(z.end_ms==null?null:z.end_ms-z.start_ms)}</td><td class="num">${(z.tokens||{}).total||''}</td><td style="width:32%"><div class="bar"><i style="left:${l}%;width:${w}%;background:${col}${z.fidelity==='observed'?';opacity:.45':z.fidelity==='reconstructed'?';opacity:.7':''}"></i></div></td></tr>`;}).join('');
  return `<table class="ladder"><thead><tr><th>Span</th><th>what</th><th>actor</th><th class="num">duration</th><th class="num">tokens</th><th>0 → ${fmtMs(T)}</th></tr></thead><tbody>${rows}</tbody></table>`;}
function cite(ids,row){citedSpans=new Set(ids);document.querySelectorAll('.evrow').forEach(e=>e.classList.remove('sel'));if(row)row.classList.add('sel');document.querySelectorAll('.flame rect').forEach(r=>{r.classList.toggle('cited',citedSpans.has(r.dataset.id));r.classList.toggle('dim',citedSpans.size>0&&!citedSpans.has(r.dataset.id));});document.querySelectorAll('.ladder tr[data-id]').forEach(r=>{r.classList.toggle('cited',citedSpans.has(r.dataset.id));r.classList.toggle('dim',citedSpans.size>0&&!citedSpans.has(r.dataset.id));});const first=[...document.querySelectorAll('[data-id]')].find(e=>e.dataset.id===ids[0]);if(first)first.scrollIntoView({block:'center',behavior:'smooth'});}

/* ---------- 7 · the Flow view (ticket 28): a Change record's story, a Flow's definition ---------- */
const LANES=[['what_happened','what'],['problem','problem'],['fix','fix'],['expected','expect'],['observed','observed']];
/* hrefOf: a lane item's link as this host can open it. Served, every page route opens; a Report file
   opens only its own Run (as #report/...), so a route to anything else is shown, not linked. A path
   (a file, a Push record, a Restore point) is shown as text: a page cannot open it. */
function hrefOf(link){if(!link||link[0]!=='#')return null;if(!EMBEDDED)return link;const own='#run/'+VIEW.header.run_id;return link===own||link.startsWith(own+'/')?'#report/'+link.slice(5):null;}
/* A cite is data-cites, read by the one delegated onCite, with data-run, data-scenario and data-trial
   naming the Trial whose Spans it cites (Span ids repeat across Trials): on that Trial's screen it
   highlights them; anywhere else it opens that Trial with ?cite= (served only). A cite whose Trial
   cannot be linked is not drawn: the item's text names it. */
function itemHtml(it){const href=hrefOf(it.link);const text=h(it.text);const at=(it.link||'').startsWith('#run/')?it.link.split('?')[0].split('/').slice(1):[];
  const cites=at.length===3?(it.cites||[]).map(c=>`<span class="cite" data-cites="${h(JSON.stringify([c]))}" data-run="${h(at[0])}" data-scenario="${h(at[1])}" data-trial="${h(at[2])}"${href?` data-href="${h(href)}"`:''}>${h(c)}</span>`).join(' '):'';
  return `<li>${href?`<a href="${h(href)}">${text}</a>`:text}${it.link&&!href?` <span class="mono small muted">${h(it.link)}</span>`:''}${cites?' '+cites:''}</li>`;}
function storyHtml(c){return `<div class="story" data-section="story">${LANES.map(([k,cls],i)=>{const l=c[k];return `<div class="node ${cls}${l.pending?' pending':''}" data-lane="${k}"><div class="n">${i+1}${l.pending?' · pending':''}</div><h3>${h(l.title)}</h3><ul class="items">${l.items.map(itemHtml).join('')}</ul></div>`;}).join('')}</div>`;}
function changeCardHtml(c){const self=hrefOf('#flow/'+c.id);const id=`<span class="mono">${h(c.id)}</span>`;
  return `<div class="card" style="margin-bottom:16px" data-section="change-record"><div class="head"><h3>Change record · ${self?`<a href="${h(self)}">${id}</a>`:id} · ${h(c.title)}</h3><span>${pill(c.status)} <span class="small muted">${h(c.target)} · opened ${h(when(c.opened_at))} by ${h(c.opened_by)}${c.closed_at?' · closed '+when(c.closed_at):''}${c.imported_from?' · imported from '+h(c.imported_from):''}</span></span></div><div class="body">${storyHtml(c)}<div class="small muted" style="margin-top:10px">Five lanes, each item linked to what it cites. A lane marked pending says what would fill it. A rendering of ${c.file?`<span class="mono">${h(c.file)}</span>`:'the record file'}; nothing here is stored.</div></div></div>`;}
let lastChange=null;
function flowModes(mode,story,def){const b=(on,href,label)=>href?`<a class="${on?'on':''}" href="${h(href)}">${label}</a>`:`<span class="${on?'on':''}">${label}</span>`;return `<div class="tog" data-section="flow-mode">${b(mode==='story',story,'story')}${b(mode==='def',def,'Flow definition')}</div>`;}
function flowShell(mode,body,story,def){return `<div class="hd"><div><h1>Flow view</h1><div class="sub">A Change record as its story, or a Flow from its definition: two renderings from the one renderer.</div></div>${flowModes(mode,story,def)}</div>${body}`;}
RENDER.flow=async function(args){
  if(args[0]==='def'){const [slug,env,id]=args.slice(1);if(!id)return flowShell('def',flowHelp(),lastChange?'#flow/'+lastChange.id:null,null);const f=await API.flow(slug,env,id);return flowShell('def',flowDefHtml(f,slug,env),lastChange?'#flow/'+lastChange.id:null,'#flow/def/'+[slug,env,id].join('/'));}
  if(!args[0]){const slug=await slugOf();const list=EMBEDDED?null:await API.changes(slug).catch(()=>null);return flowShell('story',(list?changesListHtml(list,slug):'')+flowHelp(),null,null);}
  const c=await API.change(args[0]);lastChange=c;const def=(c.fix.items.map(i=>i.link).find(l=>l&&l.startsWith('#flow/def/')))||null;
  return flowShell('story',changeCardHtml(c),'#flow/'+c.id,def);
};
function changesListHtml(list,slug){const rows=list.map(c=>`<tr><td class="mono"><a href="#flow/${h(enc(c.id))}">${h(c.id)}</a></td><td>${pill(c.status)}</td><td>${h(when(c.opened_at))}</td><td>${h(c.layer||'—')}</td><td>${h(c.title)}</td><td>${(c.pushes||[]).map(p=>h(p.environment)).join(', ')||'—'}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">none yet: <code>agentdiag change open</code> opens one from a Diagnosis or a complaint</td></tr>';
  return `<div class="card" style="margin-bottom:16px" data-section="change-records"><div class="head"><h3>Change records · ${h(slug||'')}</h3><span class="small muted">each file under changes/, by id</span></div><div class="scroll-x"><table><thead><tr><th>id</th><th>status</th><th>opened</th><th>layer</th><th>title</th><th>pushed to</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;}
function flowHelp(){return `<div class="card"><div class="body muted">Name a Change record, <span class="mono">#flow/&lt;change id&gt;</span>, or a Flow the Connector reads, <span class="mono">#flow/def/&lt;target&gt;/&lt;environment&gt;/&lt;flow id&gt;</span>.</div></div>`;}
function flowDefHtml(f,slug,env){const rows=f.steps.map(s=>`<tr><td class="mono">${h(s.id)}</td><td>${h(s.name)}</td><td class="mono">${h(s.kind||'—')}</td><td class="mono">${h(s.calls||'—')}</td></tr>`).join('');
  return `<div class="card" data-section="flow-definition"><div class="head"><h3>Flow · <span class="mono">${h(f.id)}</span>${f.tool?` · called by tool <span class="mono">${h(f.tool)}</span>`:''}${f.version!=null?' · version '+h(f.version):''}${f.state!=null?' · '+h(f.state):''}</h3><span class="small muted">${h(slug)} · ${h(env)} · ${f.steps.length} step(s) · ${f.edges.length} edge(s)</span></div><div class="body">${flowSvg(f)}<div class="scroll-x" style="margin-top:12px"><table><thead><tr><th>step</th><th>name</th><th>kind</th><th>calls</th></tr></thead><tbody>${rows}</tbody></table></div><div class="small muted" style="margin-top:8px">Drawn from the definition the Connector's <code>read_deployed_set</code> returns under <code>flows</code>; each step's kind and call are the definition's own words.</div></div></div>`;}
/* flowSvg: each step at the column its longest path from a root gives it (bounded, so a cycle ends),
   one box per step, one line per edge. */
function flowSvg(f){const ids=f.steps.map(s=>s.id);const level={};ids.forEach(i=>level[i]=0);const edges=f.edges.filter(([a,b])=>a in level&&b in level);
  for(let n=0;n<ids.length;n++){let changed=false;edges.forEach(([a,b])=>{if(level[b]<level[a]+1&&level[a]+1<ids.length){level[b]=level[a]+1;changed=true;}});if(!changed)break;}
  const cols={};f.steps.forEach(s=>(cols[level[s.id]]=cols[level[s.id]]||[]).push(s));const bw=150,bh=56;const pos={};Object.entries(cols).forEach(([l,steps])=>steps.forEach((s,i)=>pos[s.id]={x:20+l*170,y:30+i*80}));
  const W=Math.max(1000,...Object.values(pos).map(p=>p.x+bw+20));
  const boxes=f.steps.map(s=>{const p=pos[s.id];return `<g data-step="${h(s.id)}"><title>${h(s.id+' · '+s.name+(s.kind?' · '+s.kind:'')+(s.calls?' · calls '+s.calls:''))}</title><rect class="box" x="${p.x}" y="${p.y}" width="${bw}" height="${bh}" rx="8"/><text x="${p.x+10}" y="${p.y+22}">${h(String(s.name).slice(0,22))}</text><text x="${p.x+10}" y="${p.y+40}" class="t2">${h(String(s.calls||s.kind||'').slice(0,24))}</text></g>`;}).join('');
  const lines=edges.map(([a,b])=>{const p=pos[a],q=pos[b];return `<path class="edge" data-edge="${h(a)}>${h(b)}" d="M${p.x+bw},${p.y+bh/2} C${p.x+bw+20},${p.y+bh/2} ${q.x-20},${q.y+bh/2} ${q.x-2},${q.y+bh/2}"/>`;}).join('');
  const H=f.steps.length?Math.max(...Object.values(pos).map(p=>p.y))+bh+30:40;return `<svg class="flow" viewBox="0 0 ${W} ${H}"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--line-strong)"/></marker></defs>${lines}${boxes}</svg>`;}

/* ---------- after render ---------- */
function afterRender(screen){document.querySelectorAll('.flame rect[data-tip]').forEach(r=>{r.addEventListener('mousemove',e=>showTT(e,r.dataset.tip));r.addEventListener('mouseleave',hideTT);});
  document.getElementById('vbar').classList.toggle('on',screen==='run');document.getElementById('vbar-label').textContent=`flame graph ${variant} · ${VARIANTS.find(v=>v[0]===variant)[1]}`;
  citedSpans=new Set();}
function boot(){document.body.insertAdjacentHTML('afterbegin',shellHtml());buildNav();bindTheme();window.addEventListener('hashchange',route);document.addEventListener('keydown',onKey);document.addEventListener('click',onCite);document.addEventListener('click',onAction);route();}
function onCite(e){const el=e.target.closest&&e.target.closest('[data-cites]');if(!el)return;let ids=[];try{ids=JSON.parse(el.dataset.cites);}catch(_){ids=[];}
  const elsewhere=el.dataset.run&&(el.dataset.run!==shownRun||!shownTrial||el.dataset.scenario!==shownTrial[0]||el.dataset.trial!==shownTrial[1]);
  if(elsewhere){if(el.dataset.href)location.hash=el.dataset.href.split('?')[0].slice(1)+'?cite='+encodeURIComponent(ids.join(','));return;}
  cite(ids,el.classList.contains('evrow')?el:null);}
if(typeof window!=='undefined'&&window.document)boot();
