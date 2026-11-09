const $=id=>document.getElementById(id);
const names={idle:'Tayyor',queued:'Navbatda',running:'Kirilmoqda',success:'Tasdiqlandi',failed:'Xato',waiting:'Qo‘lda kirish',unverified:'Tasdiqlanmadi',cancelled:'To‘xtatildi',interrupted:'Uzilgan',rate_limited:'Limit',skipped:'O‘tkazildi'};
const ATTENTION=['failed','unverified','rate_limited','interrupted','waiting'];
const RETRYABLE=['failed','unverified','interrupted','cancelled'];
let groups=[{id:'default',name:'Asosiy'}], activeGroup=null, editing=null, editingGroup=null;
let selected=new Set(), lastPicked=null;
let closing=false, running=false, accountSig='', groupSig='', logSig='', lastAccounts=[];

async function api(path,method='GET',body){
  const r=await fetch('/api/'+path,{method,headers:{'Content-Type':'application/json','X-Manager':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  let data={};try{data=await r.json();}catch(e){}
  if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'So‘rov bajarilmadi');
  return data;
}
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined&&text!==null)e.textContent=text;if(cls)e.className=cls;return e;}
function say(text,bad){const m=$('message');m.textContent=text||'';m.className=text?(bad?'bad':'good'):'';}
async function action(fn){try{await fn();say('');await refresh();}catch(e){say(e.message,true);}}
const groupName=id=>(groups.find(g=>g.id===id)||{name:'—'}).name;

/* ---------- ro‘yxat ---------- */
function renderGroups(accounts){
  const box=$('group-tabs');box.replaceChildren();
  for(const g of [{id:null,name:'Barchasi'},...groups]){
    const n=accounts.filter(a=>g.id===null||(a.group_id||'default')===g.id).length;
    const b=el('button',g.name+' · '+n,'tab');b.type='button';b.setAttribute('aria-pressed',String(activeGroup===g.id));
    b.onclick=()=>{activeGroup=g.id;groupSig='';renderGroups(lastAccounts);filterAccounts();syncButtons();};
    box.append(b);
  }
}
function renderAccounts(accounts,isRunning){
  const box=$('accounts');box.replaceChildren();
  if(!accounts.length){box.append(el('div','Hozircha akkaunt yo‘q. «＋ Akkaunt» yoki «Ko‘plab qo‘shish» tugmasini bosing.','empty'));syncSelection();return;}
  accounts.forEach((a,i)=>{
    const row=el('article',null,'account');row.dataset.login=(a.login+' '+a.label).toLowerCase();row.dataset.group=a.group_id||'default';
    const pick=el('input',null,'pick');pick.type='checkbox';pick.checked=selected.has(a.id);pick.disabled=isRunning;pick.title='Tanlash';pick.setAttribute('aria-label',a.label+' ni tanlash');row.dataset.id=a.id;
    row.classList.toggle('picked',pick.checked);
    pick.onclick=ev=>{
      const rows=[...document.querySelectorAll('.account:not([hidden])')];
      if(ev.shiftKey&&lastPicked){const ids=rows.map(r=>r.dataset.id),x=ids.indexOf(lastPicked),y=ids.indexOf(a.id);if(x>=0&&y>=0)for(const id of ids.slice(Math.min(x,y),Math.max(x,y)+1))pick.checked?selected.add(id):selected.delete(id);}
      else pick.checked?selected.add(a.id):selected.delete(a.id);
      lastPicked=a.id;syncSelection();
    };
    row.append(pick,el('span',String(i+1),'num'));
    const who=el('div',null,'who');who.append(el('strong',a.label),el('span',a.login));row.append(who);
    row.append(el('span',groupName(a.group_id||'default'),'gname'));
    const st=el('div',null,'state');st.append(el('span',names[a.status]||a.status,'badge s-'+a.status));if(a.detail)st.append(el('small',a.detail));row.append(st);
    const act=el('div',null,'row-actions');
    const go=el('button','▶','icon');go.title='Faqat shu akkauntga kirish';go.disabled=isRunning;go.onclick=()=>action(()=>api('run','POST',{headed:$('headed').checked,account_id:a.id}));
    const ed=el('button','✎','icon');ed.title='Tahrirlash';ed.disabled=isRunning;ed.onclick=()=>openEditor(a);
    const del=el('button','🗑','icon danger');del.title='O‘chirish';del.disabled=isRunning;del.onclick=async()=>{if(confirm(a.label+' akkauntini o‘chirish?'))await action(()=>api('accounts/'+a.id,'DELETE'));};
    act.append(go,ed,del);row.append(act);box.append(row);
  });
  filterAccounts();
}
/* ---------- tanlash ---------- */
const visibleIds=()=>[...document.querySelectorAll('.account:not([hidden])')].map(r=>r.dataset.id);
const inScope=()=>lastAccounts.filter(a=>activeGroup===null||(a.group_id||'default')===activeGroup).map(a=>a.id);
function syncSelection(){
  const known=new Set(lastAccounts.map(a=>a.id));for(const id of [...selected])if(!known.has(id))selected.delete(id);
  document.querySelectorAll('.account').forEach(r=>{const on=selected.has(r.dataset.id);r.classList.toggle('picked',on);const c=r.querySelector('.pick');if(c)c.checked=on;});
  const vis=visibleIds(),n=vis.filter(id=>selected.has(id)).length;
  const all=$('select-all');all.checked=vis.length>0&&n===vis.length;all.indeterminate=n>0&&n<vis.length;all.disabled=running||!vis.length;
  const total=selected.size;
  $('selected-count').textContent=total?'Tanlangan: '+total+' ta':'';
  const rest=inScope().filter(id=>!selected.has(id)).length;
  $('sel-run').disabled=running||!total;$('sel-delete').disabled=running||!total;$('sel-invert').disabled=running||!vis.length;
  $('sel-run-rest').disabled=running||!total||!rest;
  $('selection').hidden=!lastAccounts.length;
}
$('select-all').onchange=e=>{for(const id of visibleIds())e.target.checked?selected.add(id):selected.delete(id);syncSelection();};
$('sel-invert').onclick=()=>{for(const id of visibleIds())selected.has(id)?selected.delete(id):selected.add(id);syncSelection();};
$('sel-run').onclick=()=>{const ids=[...selected];if(ids.length&&confirm(ids.length+' ta tanlangan akkauntga kirilsinmi?'))action(()=>api('run','POST',{headed:$('headed').checked,account_ids:ids}));};
$('sel-run-rest').onclick=()=>{const ids=inScope().filter(id=>!selected.has(id));if(ids.length&&confirm('Tanlanmagan '+ids.length+' ta akkauntga kirilsinmi? (tanlanganlar o‘tkazib yuboriladi)'))action(()=>api('run','POST',{headed:$('headed').checked,account_ids:ids}));};
$('sel-delete').onclick=()=>{const ids=[...selected];if(ids.length&&confirm(ids.length+' ta tanlangan akkaunt o‘chirilsinmi? Buni qaytarib bo‘lmaydi.'))action(async()=>{await api('accounts/delete','POST',{ids});selected.clear();});};
function filterAccounts(){
  const q=$('account-search').value.trim().toLowerCase();let visible=0;
  document.querySelectorAll('.account').forEach(r=>{r.hidden=!r.dataset.login.includes(q)||(activeGroup!==null&&r.dataset.group!==activeGroup);if(!r.hidden)visible++;});
  $('search-count').textContent=q?visible+' ta':'';
  const e=$('search-empty');const any=document.querySelectorAll('.account').length>0;
  e.hidden=!any||visible>0;e.textContent=q?'Bunday akkaunt topilmadi.':'Bu bo‘limda akkaunt yo‘q.';
  syncSelection();
}
function syncButtons(){$('rename-group').disabled=running||!activeGroup;}

async function refresh(){
  try{
    const s=await api('state');if(closing)return;
    desktopControls(s);
    groups=s.groups||groups;lastAccounts=s.accounts;running=s.running;
    $('connection').textContent=s.running?'Ish davom etmoqda…':'Tayyor';$('connection').className='pill'+(s.running?' busy':'');
    const gsig=JSON.stringify([groups,s.accounts.map(a=>a.group_id),activeGroup]);
    if(gsig!==groupSig){groupSig=gsig;renderGroups(s.accounts);}
    const asig=JSON.stringify([s.accounts,s.running,groups]);
    if(asig!==accountSig){accountSig=asig;renderAccounts(s.accounts,s.running);}
    $('count-total').textContent=s.accounts.length;
    $('count-success').textContent=s.accounts.filter(a=>a.status==='success').length;
    $('count-attention').textContent=s.accounts.filter(a=>ATTENTION.includes(a.status)).length;
    $('count-left').textContent=s.accounts.filter(a=>a.status==='queued'||a.status==='running').length;
    $('run').disabled=s.running||!s.accounts.length;$('run').textContent=s.running?'Tekshirilmoqda…':'▶ Barchasiga kirish';
    const again=s.accounts.filter(a=>RETRYABLE.includes(a.status)&&!(a.detail||'').startsWith('Login/parol rad etildi')).length;
    $('retry').disabled=s.running||!again;$('retry').textContent='↻ Xatolarni qayta urinish'+(again?' ('+again+')':'');
    $('stop').disabled=!s.running;
    for(const id of ['add','bulk','add-group'])$(id).disabled=s.running;
    syncButtons();
    const lsig=JSON.stringify(s.logs);
    if(lsig!==logSig){logSig=lsig;const box=$('logs');box.replaceChildren();const names2=Object.fromEntries(s.accounts.map(a=>[a.id,a.login]));
      for(const l of s.logs){const r=el('div',null,'log');r.append(el('time',new Date(l.time).toLocaleString('uz-UZ',{timeZone:'Asia/Samarkand'})),el('span',(names2[l.account_id]||'Akkaunt')+' · '+l.message));box.append(r);}
      if(!s.logs.length)box.textContent='Hali hodisa yo‘q.';}
    if(!refresh.init){refresh.init=true;$('enabled').checked=!!s.schedule.enabled;$('time').value=s.schedule.time;}
  }catch(e){$('connection').textContent='Aloqa yo‘q';}
}

/* ---------- bitta akkaunt ---------- */
function fillGroups(select,value){select.replaceChildren(...groups.map(g=>{const o=el('option',g.name);o.value=g.id;return o;}));select.value=value;}
async function openEditor(a){
  try{
    const v=a?await api('accounts/'+a.id+'/editor','POST',{}):{label:'',login:'',password:''};
    editing=a?a.id:null;$('account-form').reset();setPasswordVisible(false);
    fillGroups($('account-group'),(a&&a.group_id)||activeGroup||'default');
    $('label').value=v.label;$('login').value=v.login;$('password').value=v.password;
    $('form-title').textContent=a?'Akkauntni tahrirlash':'Akkaunt qo‘shish';$('form-error').textContent='';$('editor').showModal();
  }catch(e){say(e.message,true);}
}
function setPasswordVisible(v){$('password').type=v?'text':'password';$('toggle-password').setAttribute('aria-pressed',String(v));$('toggle-password').textContent=v?'🙈':'👁';}
$('toggle-password').onclick=()=>setPasswordVisible($('password').type==='password');
$('add').onclick=()=>openEditor();
$('cancel').onclick=()=>$('editor').close();
$('account-form').onsubmit=async e=>{e.preventDefault();try{await api('accounts'+(editing?'/'+editing:''),editing?'PUT':'POST',{group_id:$('account-group').value,label:$('label').value,login:$('login').value,password:$('password').value});$('editor').close();await refresh();}catch(err){$('form-error').textContent=err.message;}};

/* ---------- bo‘limlar ---------- */
function openGroupEditor(rename){editingGroup=rename?activeGroup:null;$('group-name').value=rename?groupName(activeGroup):'';$('group-title').textContent=rename?'Bo‘lim nomini o‘zgartirish':'Yangi bo‘lim';$('group-error').textContent='';$('group-editor').showModal();}
$('add-group').onclick=()=>openGroupEditor(false);$('rename-group').onclick=()=>openGroupEditor(true);$('group-cancel').onclick=()=>$('group-editor').close();
$('group-form').onsubmit=async e=>{e.preventDefault();try{const r=await api('groups'+(editingGroup?'/'+editingGroup:''),editingGroup?'PUT':'POST',{name:$('group-name').value.trim()});if(!editingGroup)activeGroup=r.id;groupSig='';$('group-editor').close();await refresh();}catch(err){$('group-error').textContent=err.message;}};

/* ---------- ko‘plab qo‘shish ---------- */
function parseBulk(text,common){
  const out=[],bad=[];
  text.split(/\r?\n/).forEach((raw,i)=>{
    const line=raw.trim();if(!line)return;
    const parts=line.split(/[\t;,]+\s*|\s+/).filter(Boolean);
    const login=parts[0];let password,label;
    if(common){password=common;label=parts.slice(1).join(' ');}else{password=parts[1];label=parts.slice(2).join(' ');}
    if(!login||!password){bad.push(i+1);return;}
    out.push({login,password,label:(label||login).slice(0,60)});
  });
  return {out,bad};
}
$('bulk').onclick=()=>{fillGroups($('bulk-group'),activeGroup||'default');$('bulk-result').textContent='';$('bulk-result').className='';$('bulk-submit').disabled=false;$('bulk-editor').showModal();};
$('bulk-cancel').onclick=()=>$('bulk-editor').close();
$('bulk-form').onsubmit=async e=>{
  e.preventDefault();const r=$('bulk-result');
  const {out,bad}=parseBulk($('bulk-text').value,$('bulk-password').value.trim());
  if(!out.length){r.textContent='Akkaunt topilmadi. Har qatorda kamida login va parol bo‘lsin.';r.className='bad';return;}
  $('bulk-submit').disabled=true;let added=0;const failed=[];
  for(const a of out){
    r.textContent='Qo‘shilmoqda… '+(added+failed.length+1)+' / '+out.length;r.className='';
    try{await api('accounts','POST',{group_id:$('bulk-group').value,label:a.label,login:a.login,password:a.password});added++;}catch(err){failed.push(a.login+' ('+err.message+')');}
  }
  r.textContent='Qo‘shildi: '+added+' ta.'+(failed.length?' Xato: '+failed.join(', '):'')+(bad.length?' O‘tkazilgan qatorlar: '+bad.join(', '):'');
  r.className=failed.length||bad.length?'bad':'good';
  if(added){$('bulk-text').value='';await refresh();}
  $('bulk-submit').disabled=false;
};

/* ---------- boshqaruv ---------- */
$('run').onclick=()=>action(()=>api('run','POST',{headed:$('headed').checked}));
$('retry').onclick=()=>action(()=>api('run','POST',{headed:$('headed').checked,retry:true}));
$('stop').onclick=()=>action(()=>api('stop','POST',{}));
$('schedule').onsubmit=e=>{e.preventDefault();action(()=>api('schedule','PUT',{enabled:$('enabled').checked,time:$('time').value}));};
$('account-search').addEventListener('input',filterAccounts);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
function desktopControls(state){
  if(!state.desktop||$('desktop-quit'))return;
  const b=el('button','Dasturni yopish','ghost');b.id='desktop-quit';b.type='button';
  b.onclick=async()=>{if(!confirm('Dasturni yopsangiz rejalashtirilgan ishlar ham to‘xtaydi. Yopilsinmi?'))return;
    try{await api('desktop/quit','POST',{});closing=true;b.disabled=true;b.textContent='Dastur yopildi';$('connection').textContent='Yopildi: oynani yopishingiz mumkin';$('run').disabled=true;}catch(err){say(err.message,true);}};
  document.querySelector('.bar-actions').prepend(b);
}
/* Ish ketayotganda 2 s, bo‘sh turganda 6 s: eski kompyuterlarda keraksiz yuklanish bo‘lmasin. */
(async function poll(){if(!document.hidden)await refresh();setTimeout(poll,running?2000:6000);})();
