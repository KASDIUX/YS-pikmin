'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="leafroute-token"]').content;
let state = null, selected = [25.033964,121.564468], points = [], drawing = false;
let library = {favorites:[],routes:[]}, saveKind = 'favorites', editing = false, polling = false;
let routeMeta = {kind:'manual'}, routeRevision = 0, planEpoch = 0, planning = false;
let toastTimer, tileFailures = 0;
const map = L.map('map', {zoomControl:false}).setView(selected,16);
L.control.zoom({position:'bottomright'}).addTo(map);
const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
  updateWhenIdle:true, keepBuffer:1
}).addTo(map);
tiles.on('tileerror',()=>{ if(++tileFailures>=3) $('tile-warning').hidden=false; });
tiles.on('tileload',()=>{ tileFailures=0; $('tile-warning').hidden=true; });
const current = L.marker(selected,{icon:L.divIcon({className:'current-marker',iconSize:[20,20],iconAnchor:[10,10]}),interactive:false,zIndexOffset:600}).addTo(map);
const selection = L.marker(selected,{icon:L.divIcon({className:'selection-marker',iconSize:[14,14],iconAnchor:[7,7]}),interactive:false,zIndexOffset:700}).addTo(map);
const line = L.polyline([],{color:'#3c6a4f',weight:4,opacity:.85,lineJoin:'round'}).addTo(map);
const routeDots = L.layerGroup().addTo(map);
const connector = L.polyline([],{color:'#749368',weight:2,dashArray:'5 7',opacity:.7}).addTo(map);

function toast(message,error=false){
  $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?6500:3000);
}
async function api(action,data){
  const response=await fetch('/api/'+action, data===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-LeafRoute-Token':token},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error || '操作失敗。');
  return result;
}
async function act(action,data={}){
  try{return await api(action,data);}
  finally{await refresh();}
}
function bind(id,fn){
  $(id).addEventListener('click',async event=>{
    const button=event.currentTarget; if(button.dataset.busy) return;
    button.dataset.busy='1';button.disabled=true;
    try{await fn(event);}catch(e){toast(e.message,true);}finally{delete button.dataset.busy;button.disabled=false;if(state) render(state);}
  });
}
function coordinate(value){
  const parts=value.trim().split(/[,，\s]+/).map(Number);
  if(parts.length!==2 || !parts.every(Number.isFinite) || Math.abs(parts[0])>90 || Math.abs(parts[1])>180) throw new Error('請輸入有效的「緯度, 經度」，例如 25.033964, 121.564468。');
  return parts;
}
function select(position,pan=false){
  selected=[...position];selection.setLatLng(position);$('coordinates').value=position.map(x=>Number(x).toFixed(6)).join(', ');
  if(pan) map.panTo(position);
}
function setDrawing(value){
  drawing=value;
  for(const [id,on] of [['draw-mode',value],['select-mode',!value]]) {$(id).classList.toggle('active',on);$(id).setAttribute('aria-pressed',String(on));}
  $('map-hint').textContent=value?'依序點擊地圖新增路徑點 · 手動畫線':'點一下地圖，選擇下一個位置';
  $('map').style.cursor=value?'crosshair':'';
}
function drawRoute(){
  line.setLatLngs(points);routeDots.clearLayers();
  if(routeMeta.kind==='road')$('road-profile').value=routeMeta.profile;
  // Rendering every GPX vertex creates thousands of DOM nodes; show endpoints for large tracks.
  const visible=points.length<=80?points.map((p,i)=>[p,i]):[[points[0],0],[points[points.length-1],points.length-1]];
  for(const [p,i] of visible){if(!p)continue;const dot=L.circleMarker(p,{radius:4,color:'#fff',weight:2,fillColor:'#3c6a4f',fillOpacity:1}).addTo(routeDots);if(i===0||i===points.length-1)dot.bindTooltip(i===0?'起點':'終點',{direction:'top',className:'route-label'});}
  updateConnector();
}
function updateConnector(){
  const p=state?.position;
  connector.setLatLngs(routeMeta.kind!=='road'&&points.length&&p&&state.status==='idle'?[p,points[0]]:[]);
}
async function setRoute(values,fit=false,meta=null,expectedRevision=undefined){
  if(editing) return;
  editing=true;planEpoch++;
  try{const payload={points:values,meta};if(expectedRevision!==undefined)payload.expected_revision=expectedRevision;
    const result=await act('route',payload);points=result.points;routeMeta=result.meta;routeRevision=result.state.route_revision;drawRoute();render(result.state);
    if(fit&&points.length)map.fitBounds(line.getBounds(),{padding:[45,45],maxZoom:17});}
  finally{editing=false;}
}
map.on('click',async event=>{
  if(editing) return;
  const p=[event.latlng.lat,((event.latlng.lng+180)%360+360)%360-180];
  select(p);
  if(drawing){
    if(state?.status==='running'){toast('請先暫停，再編輯路線。',true);return;}
    // A single draft vertex stays local until there is a valid route.
    const draft=routeMeta.kind==='road'?routeMeta.waypoints:points;
    if(draft.length===0){points=[p];planEpoch++;drawRoute();$('point-count').textContent='1 個點';return;}
    try{await setRoute([...draft,p]);}catch(e){toast(e.message,true);}
  }
});
function meters(value){return value>=1000?(value/1000).toFixed(2)+' km':Math.round(value)+' m';}
function render(s){
  state=s;current.setLatLng(s.position);updateConnector();
  const isDevice=s.mode==='device',running=s.status==='running';
  $('mode-badge').classList.toggle('device',isDevice);$('mode-badge').replaceChildren(Object.assign(document.createElement('i'),{}),document.createTextNode(isDevice?(s.connected?'iPhone 模式':'iPhone 已斷線'):'預覽模式'));
  $('notice-text').textContent=s.message;$('notice').classList.toggle('error',s.error);
  $('current-coordinates').textContent=s.position.map(x=>x.toFixed(6)).join(', ');
  $('position-label').textContent=isDevice?(s.simulation_active?'最後送出座標':'工作台座標'):'預覽座標';
  $('map-mode-note').textContent=isDevice?(s.connected?'已連接定位服務':'等待重新連接'):'僅在電腦上預覽';
  if(document.activeElement!==$('speed'))$('speed').value=s.speed;
  $('loop').checked=s.loop;
  document.querySelectorAll('[data-speed]').forEach(b=>b.classList.toggle('active',Number(b.dataset.speed)===s.speed));
  $('point-count').textContent=(points.length===1?1:s.point_count)+' 個點';
  $('route-distance').replaceChildren(document.createTextNode(s.route_distance?(s.route_distance/1000).toFixed(2):'—'),Object.assign(document.createElement('small'),{textContent:' km'}));
  const minutes=s.route_distance/(s.speed/3.6)/60;
  $('eta').replaceChildren(document.createTextNode(minutes?(minutes<1?'<1':Math.ceil(minutes)):'—'),Object.assign(document.createElement('small'),{textContent:' 分鐘'}));
  $('start').textContent=s.status==='paused'?'▶ 繼續行走':isDevice?'▶ 開始傳送路線':'▶ 開始預覽';
  $('restore').textContent=isDevice?'■ 停止並還原手機定位':'■ 停止預覽';
  for(const id of ['start','pause','go','undo','clear-route','import','export','demo','save-route','loop','plan-road','use-start']){
    if($(id).dataset.busy)continue;
    let disabled=false;
    if(id==='start')disabled=planning||running||!s.point_count||(isDevice&&!s.connected);
    else if(id==='pause')disabled=!running;
    else if(id==='export'||id==='save-route')disabled=!s.point_count;
    else if(id==='undo'||id==='clear-route')disabled=running||!points.length;
    else if(id==='plan-road')disabled=planning||running||points.length<2;
    else if(id==='use-start')disabled=running||!points.length;
    else disabled=running;
    $(id).disabled=disabled;
  }
  document.querySelectorAll('[data-bearing]').forEach(b=>b.disabled=running||(isDevice&&!s.connected));
  $('device-open').textContent=isDevice?(s.connected?'iPhone 已連接 ↗':'重新連接 ↗'):'連接 iPhone ↗';
  if(!planning){
    $('plan-road').textContent='⌁ 沿道路規劃';
    $('road-result').textContent=routeMeta.kind==='road'?({foot:'步行',bike:'騎車',car:'開車'}[routeMeta.profile]+'道路 · '+routeMeta.waypoints.length+' 個途經點'+(routeMeta.round_trip?' · 含道路回程':'')+'。開始前請先套用路線起點。'):routeMeta.kind==='gpx'?'已載入 GPX 軌跡。':'目前使用手動畫線，點與點之間直接連接。';
  }
  $('journey-status').textContent=({idle:'準備出發',running:'漫步進行中',paused:'暫停一下',completed:'已抵達終點'})[s.status]||'準備出發';
  $('journey-detail').textContent=s.status==='running'?(isDevice?'正在傳送座標；斷線時停止':'預覽中 · 不影響手機定位'):s.point_count?(routeMeta.kind==='road'?'道路路線 · 請先套用起點，避免直線穿越':s.loop?'循環包含回程；會從目前座標接上路線':'開始時會從目前座標接上路線'):'新增至少兩個路徑點即可開始';
  const percent=s.distance?Math.min(100,s.progress/s.distance*100):0;
  $('progress').value=percent;$('progress-label').textContent=Math.round(percent)+'%';
  $('traveled').textContent=meters(s.traveled);$('laps').textContent=s.laps;
}
async function refresh(){
  if(polling)return;
  polling=true;
  try{const s=await api('state');
    if(!editing&&s.route_revision!==routeRevision){const r=await api('route');points=r.points;routeMeta=r.meta;routeRevision=s.route_revision;planEpoch++;drawRoute();}
    render(s);}
  catch(e){$('notice-text').textContent='無法連接本機程式。請確認 LeafRoute 的啟動視窗仍在執行；手機狀態目前無法確認。';$('notice').classList.add('error');}
  finally{polling=false;}
}
function tab(name){
  for(const key of ['route','library']){const on=key===name;$('tab-'+key).classList.toggle('active',on);$('tab-'+key).setAttribute('aria-selected',String(on));$('panel-'+key).hidden=!on;}
}
function renderLibrary(){
  $('library-count').textContent=library.favorites.length+library.routes.length;
  for(const [kind,target] of [['favorites','favorites-list'],['routes','routes-list']]){
    $(target).replaceChildren();
    if(!library[kind].length){const p=document.createElement('div');p.className='empty';p.textContent=kind==='favorites'?'還沒有收藏。選一個地圖位置，再按「＋ 收藏」。':'把喜歡的路線存下來，下次就能接著走。';$(target).append(p);}
    for(const record of library[kind]){
      const row=document.createElement('div');row.className='library-item';
      const load=document.createElement('button');load.className='load-item';const title=document.createElement('strong');title.textContent=record.name;const info=document.createElement('small');info.textContent=kind==='favorites'?record.position.map(x=>Number(x).toFixed(5)).join(', '):meters(record.distance)+' · '+record.points.length+' 個點';load.append(title,info);
      load.onclick=async()=>{try{tab('route');setDrawing(false);if(kind==='favorites'){select(record.position,true);toast('已選擇收藏位置，按「套用」才會更新位置。');}else{await setRoute(record.points,true,record.meta);toast('已載入路線。');}}catch(e){toast(e.message,true);}};
      const remove=document.createElement('button');remove.className='delete-item';remove.textContent='刪除';remove.setAttribute('aria-label','刪除 '+record.name);
      remove.onclick=async()=>{if(!confirm('刪除「'+record.name+'」？'))return;try{library=await act('delete',{kind,id:record.id});renderLibrary();}catch(e){toast(e.message,true);}};
      row.append(load,remove);$(target).append(row);
    }
  }
}
async function settings(){await act('settings',{speed:Number($('speed').value),loop:$('loop').checked});}
bind('select-mode',()=>setDrawing(false));bind('draw-mode',()=>{tab('route');setDrawing(true);});
bind('center',()=>map.setView(state.position,Math.max(map.getZoom(),16)));
bind('fit',()=>{if(points.length)map.fitBounds(line.getBounds(),{padding:[45,45],maxZoom:17});});
bind('go',async()=>{const p=coordinate($('coordinates').value);await act('position',{position:p});select(p,true);});
$('coordinates').addEventListener('keydown',e=>{if(e.key==='Enter')$('go').click();});
bind('undo',async()=>{const draft=routeMeta.kind==='road'?routeMeta.waypoints:points;if(draft.length<=2){const keep=draft.slice(0,-1);await setRoute([]);points=keep;drawRoute();if(state)render(state);}else await setRoute(draft.slice(0,-1));});
bind('clear-route',async()=>{await setRoute([]);setDrawing(false);});
bind('start',async()=>{planEpoch++;await settings();await act('start');setDrawing(false);});bind('pause',()=>act('pause'));bind('restore',()=>{planEpoch++;return act('restore');});
bind('use-start',()=>{if(points.length){select(points[0],true);toast('已選取路線起點。按「套用」才會更新工作台或手機位置。');}});
bind('plan-road',async()=>{
  const waypoints=routeMeta.kind==='road'?routeMeta.waypoints:points;
  if(waypoints.length<2||waypoints.length>25)throw new Error('道路規劃需要 2～25 個途經點；請重新畫少量路徑點。');
  const epoch=planEpoch,profile=$('road-profile').value,roundTrip=$('loop').checked;
  planning=true;$('plan-road').textContent='規劃中…';$('road-result').textContent='正在查詢道路，原路線暫時保留…';render(state);
  try{
    const result=await api('plan',{points:waypoints,profile,round_trip:roundTrip});
    if(epoch!==planEpoch||profile!==$('road-profile').value||roundTrip!==$('loop').checked){toast('規劃期間路線或設定已變更，結果未套用。');return;}
    await setRoute(result.points,true,result.meta,result.revision);setDrawing(false);
    toast('道路路線已完成'+(result.snap_distance>=1?'，路徑點最多貼齊道路 '+Math.round(result.snap_distance)+' 公尺':'')+'。請選取並套用起點。');
  }finally{planning=false;render(state);}
});
$('speed').addEventListener('change',()=>settings().catch(e=>toast(e.message,true)));
$('loop').addEventListener('change',()=>settings().catch(e=>toast(e.message,true)));
document.querySelectorAll('[data-speed]').forEach(b=>b.addEventListener('click',async()=>{try{$('speed').value=b.dataset.speed;await settings();}catch(e){toast(e.message,true);}}));
document.querySelectorAll('[data-bearing]').forEach(b=>b.addEventListener('click',async()=>{if(b.dataset.busy)return;b.dataset.busy='1';try{await act('nudge',{bearing:Number(b.dataset.bearing)});}catch(e){toast(e.message,true);}finally{delete b.dataset.busy;}}));
bind('demo',async()=>{
  if(state.mode!=='preview')throw new Error('示範路線只在預覽模式使用。請先還原並中斷手機連線。');
  const route=[[25.033964,121.564468],[25.03505,121.564468],[25.03505,121.5663],[25.03275,121.5663],[25.03275,121.564468],[25.033964,121.564468]];
  await act('position',{position:route[0]});select(route[0]);await setRoute(route,true);tab('route');setDrawing(false);toast('示範路線已準備好，按「開始預覽」試走。');
});
bind('import',()=>$('gpx-file').click());
$('gpx-file').addEventListener('change',async()=>{const file=$('gpx-file').files[0];if(!file)return;planEpoch++;try{if(file.size>2_000_000)throw new Error('GPX 檔案上限為 2 MB。');const result=await act('import',{gpx:await file.text()});points=result.points;routeMeta=result.meta;routeRevision=result.state.route_revision;drawRoute();map.fitBounds(line.getBounds(),{padding:[45,45],maxZoom:17});setDrawing(false);toast('GPX 已匯入。');}catch(e){toast(e.message,true);}finally{$('gpx-file').value='';}});
bind('export',async()=>{const response=await fetch('/api/export');if(!response.ok){const error=await response.json();throw new Error(error.error);}const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='LeafRoute-'+new Date().toISOString().slice(0,10)+'.gpx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
function openName(kind){saveKind=kind;$('name-title').textContent=kind==='favorites'?'收藏選定位置':'儲存目前路線';$('save-name').value='';$('name-dialog').showModal();$('save-name').focus();}
bind('save-favorite',()=>{select(coordinate($('coordinates').value));openName('favorites');});bind('save-route',()=>openName('routes'));
bind('name-cancel',()=>$('name-dialog').close());
$('name-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{library=await act('save',{kind:saveKind,name:$('save-name').value,position:selected});renderLibrary();$('name-dialog').close();toast('已儲存在本機。');}catch(e){toast(e.message,true);}finally{button.disabled=false;}});
bind('tab-route',()=>tab('route'));bind('tab-library',()=>tab('library'));
bind('device-open',()=>$('device-dialog').showModal());
bind('scan',async()=>{
  $('device-status').textContent='正在讀取裝置，請稍候…';
  const result=await act('scan');$('devices').replaceChildren();
  if(!result.devices.length)$('devices').add(new Option('未找到裝置',''));
  for(const d of result.devices)$('devices').add(new Option(d.name+(d.ios?' · iOS '+d.ios:'')+' · '+d.transport+' · '+d.id.slice(-6),d.id));
  $('device-status').textContent=result.warning||'找到 '+result.devices.length+' 部裝置。請確認選擇正確的手機。';
});
bind('connect',async()=>{if(!$('devices').value)throw new Error('請先掃描並選擇裝置。');$('device-status').textContent='正在建立定位服務連線…';try{await act('connect',{id:$('devices').value});$('device-status').textContent=state.message;$('device-dialog').close();}catch(e){$('device-status').textContent=e.message;throw e;}});
bind('disconnect',async()=>{try{await act('disconnect');$('device-status').textContent='已中斷連線，回到預覽模式。';$('device-dialog').close();}catch(e){$('device-status').textContent=e.message+' 請在手機確認實際位置。';throw e;}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
Promise.all([api('state'),api('library'),api('route')]).then(([s,l,r])=>{points=r.points;routeMeta=r.meta;routeRevision=s.route_revision;library=l;render(s);select(s.position);drawRoute();renderLibrary();}).catch(e=>toast('啟動失敗：'+e.message,true));
setInterval(refresh,750);
