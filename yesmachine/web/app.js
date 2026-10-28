'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
const icons = {
  desktop: '<rect x="3" y="3" width="26" height="18" rx="2"/><path d="M11 28h10M16 21v7"/>',
  network: '<rect x="11" y="3" width="10" height="8" rx="1"/><path d="M16 11v7M6 18h20M6 18v4M26 18v4"/><rect x="2" y="22" width="8" height="7" rx="1"/><rect x="22" y="22" width="8" height="7" rx="1"/>',
  laptop: '<rect x="6" y="4" width="20" height="18" rx="2"/><path d="m6 22-4 5h28l-4-5M13 25h6"/>',
  folder: '<path d="M3 9V6h10l3 4h13v17H3Z"/><path d="M3 10h13"/>',
  shield: '<path d="m16 2 12 5v8c0 7-6 12-12 15C10 27 4 22 4 15V7Z"/><path d="m10 15 4 4 8-8"/>',
  clock: '<circle cx="16" cy="16" r="12"/><path d="M16 8v9l6 3"/>',
};
const icon = name => `<svg aria-hidden="true" viewBox="0 0 32 32">${icons[name] || icons.desktop}</svg>`;
icons.phone='<rect x="8" y="2" width="16" height="28" rx="3"/><path d="M13 6h6M15 26h2"/>';
icons.settings='<path d="M5 8h22M5 16h22M5 24h22"/><circle cx="11" cy="8" r="3"/><circle cx="21" cy="16" r="3"/><circle cx="14" cy="24" r="3"/>';
const routeIcons = {overview:'desktop', computers:'laptop', network:'network', features:'shield', activity:'clock',phone:'phone',clients:'desktop',settings:'settings'};
document.querySelectorAll('nav a').forEach(link => link.insertAdjacentHTML('afterbegin', icon(routeIcons[link.dataset.route])));

let state = null, signature = '', currentRoute = '', query = '', page = 1, lastJob = '', refreshing = false, toastTimer;
let deviceDirty = false, confirmCallback = null, deviceInvoker = null, confirmInvoker = null;
let themeDraft=null, themeDirty=false, qrBlob=null, pendingQr=false;
const themeRoles=['background','surface','text','muted','primary','secondary','border','success','warning','danger','link','on-primary'];
function applyTheme(colors){if(!colors)return;for(const role of themeRoles){if(/^#[a-f0-9]{6}$/i.test(colors[role]||''))document.documentElement.style.setProperty(`--${role}`,colors[role]);}const background=colors.background||'#101419';const brightness=parseInt(background.slice(1,3),16)*.2126+parseInt(background.slice(3,5),16)*.7152+parseInt(background.slice(5,7),16)*.0722;document.documentElement.style.colorScheme=brightness>145?'light':'dark';}
try{applyTheme(JSON.parse(localStorage.getItem('yesmachine.theme')||'null'));}catch{}

function route() {
  const [name = 'overview', params = ''] = location.hash.slice(1).split('?');
  return {name: ['overview','computers','network','features','activity','phone','clients','settings'].includes(name) ? name : 'overview', params:new URLSearchParams(params)};
}

async function api(path, data) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(`/api/${path}`, {method:data === undefined ? 'GET' : 'POST', credentials:'same-origin', headers:{'X-YesMachine':'1', ...(data === undefined ? {} : {'Content-Type':'application/json'})}, body:data === undefined ? undefined : JSON.stringify(data), signal:controller.signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The action could not finish. Retry from this panel.');
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The local app took too long to reply. Check Activity before retrying.');
    throw error;
  } finally { clearTimeout(timer); }
}

function notify(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5500);
}

function feedback(message, error = false) {
  const target = $(error ? '#job-error' : '#job-status');
  target.textContent = message;
  target.hidden = !message;
}

async function refresh(force = false) {
  if (refreshing) return;
  refreshing = true;
  try {
    const next = await api('state');
    $('#offline').hidden = true;
    state = next;
    if(!themeDirty&&state.theme){applyTheme(state.theme);localStorage.setItem('yesmachine.theme',JSON.stringify(state.theme));}
    const jobSignature = JSON.stringify(state.job);
    if (jobSignature !== lastJob) {
      lastJob = jobSignature;
      feedback(state.job.running ? `Working: ${state.job.action.replaceAll('-', ' ')}…` : '');
      feedback(state.job.error, true);
      if (!state.job.running && state.job.message) notify(state.job.message);
      if(pendingQr&&!state.job.running&&['mobile-start','mobile-cycle'].includes(state.job.action)){pendingQr=false;if(!state.job.error&&state.mobile?.active)showQr();}
    }
    $('#global-status').className = `status${state.host ? ' live' : ''}`;
    $('#global-status span:last-child').textContent = state.host ? `Hosting · ${state.viewer_transports} encrypted transport${state.viewer_transports === 1 ? '' : 's'}` : 'Hosting is off';
    const newSignature = JSON.stringify(state);
    if (force || signature !== newSignature) { signature = newSignature; render(); }
  } catch (error) {
    $('#offline').hidden = false;
    if (!state) $('#main').innerHTML = '<div class="panel"><h1>Open the local app</h1><p>The control service has not replied yet. Use the YesMachine desktop launcher, then reload this window.</p><button data-action="retry">Retry</button></div>';
  } finally { refreshing = false; }
}

function heading(eyebrow, title, description, actions = '') {
  return `<div class="heading"><div>${eyebrow?`<p class="eyebrow">${eyebrow}</p>`:''}<h1>${title}</h1><p class="muted">${description}</p></div>${actions ? `<div class="actions">${actions}</div>` : ''}</div>`;
}
function buttons() { return '<button class="secondary" data-action="import">Import profile</button><button data-action="add">Add computer</button>'; }
function empty(search = false) {
  return `<div class="empty">${icon(search ? 'network' : 'desktop')}<h3>${search ? 'No computers match' : 'Your other computers go here'}</h3><p>${search ? 'Try a different name or address, or clear the search.' : 'Start YesMachine on your other computer and import its connection profile to pair the two.'}</p><button class="secondary small" data-action="${search ? 'clear' : 'import'}">${search ? 'Clear search' : 'Import connection profile'}</button></div>`;
}
function deviceRows(devices) {
  return devices.map(device=>{const native=device.protocol&&device.protocol!=='yesmachine';return `<article class="device"><div class="device-icon">${icon('desktop')}</div><div class="device-info"><strong>${esc(device.name)}</strong><span class="mono">${esc(device.host)}:${device.port}</span><span class="${device.paired?'paired':''}">${native?`${esc(device.protocol.toUpperCase())} · native client credentials`:device.paired?'Paired · connection not checked':'Pairing details needed'}</span></div><div class="actions"><button class="small" data-action="${device.paired?'desktop':'edit'}" data-id="${device.id}">${device.paired?'Connect':'Pair'}</button><button class="secondary small" data-action="files" data-id="${device.id}" ${device.paired&&!native?'':'disabled title="Use the native client’s session tools for this protocol"'}>Files</button><details><summary aria-label="More actions for ${esc(device.name)}">More</summary><div class="menu"><button data-action="edit" data-id="${device.id}">Edit computer</button>${native?`<button data-action="export-client-device" data-id="${device.id}">Export client profile</button>`:''}<button data-action="terminal" data-id="${device.id}" ${device.paired&&!native?'':'disabled'}>Open terminal</button><button data-action="tunnel" data-id="${device.id}" ${device.paired&&!native?'':'disabled'}>TCP forwarding</button><button data-action="wake" data-id="${device.id}" ${device.mac?'':'disabled title="Add a MAC address first"'}>Wake on LAN</button><button data-action="remove" data-id="${device.id}">Remove computer</button></div></details></div></article>`;}).join('');
}
function hostPanel() {
  return `<section class="panel"><div class="panel-head"><div><h2>This computer</h2><p class="hint">${state.host ? 'Ready for approved viewers' : 'Incoming connections are off'}</p></div><span class="badge ${state.host ? 'ready' : ''}">${state.host ? 'HOSTING' : 'OFF'}</span></div><div class="host-name">${icon('laptop')}<strong>${esc(state.hostname)}</strong></div><dl><div><dt>Local address</dt><dd class="mono">${esc(state.lan_ip)}:${state.port}</dd></div><div><dt>Desktop engine</dt><dd>RustDesk ${esc(state.engine.version)}</dd></div><div><dt>Operating system</dt><dd>${esc(state.engine.platform)} / ${esc(state.engine.session)}</dd></div><div><dt>Access</dt><dd>Access key + owner approval</dd></div></dl><button class="wide ${state.host ? 'secondary' : ''}" data-action="${state.host ? 'host-stop' : 'host-start'}" ${state.job.running ? 'disabled' : ''}>${state.host ? 'Stop hosting' : 'Start hosting'}</button><p class="hint">${state.host ? 'Closing this window keeps hosting on. Use Stop hosting or Quit to close it.' : 'Your screen is shared only after the native engine receives OS permissions and you accept a viewer.'}</p>${state.host ? '<button class="ghost small wide" data-action="export-lan">Export LAN connection profile ↓</button>' : ''}</section>`;
}
function overview() {
  return heading('REMOTE DESKTOP, ON YOUR TERMS', 'Your computers. One workspace.', 'Connect to another desk, or make this one available.', '<button data-action="add">Add a computer</button>') + `<div class="grid"><div><section class="panel feature"><div class="panel-head"><div><h2>A direct path to your desk</h2><p class="hint">No account. Your connection, your router.</p></div>${icon('shield')}</div><div class="path"><div class="path-node"><div class="path-icon">${icon('laptop')}</div><strong>This computer</strong><span>${state.host ? 'Hosting on' : 'Hosting off'}</span></div><div class="path-line"></div><div class="path-node"><div class="path-icon">${icon('network')}</div><strong>Your router</strong><span>${state.mapping ? 'UPnP mapped' : 'Not published'}</span></div><div class="path-line"></div><div class="path-node"><div class="path-icon">${icon('desktop')}</div><strong>Other devices</strong><span>${state.viewer_transports ? `${state.viewer_transports} transport(s)` : 'Ready when you are'}</span></div></div><div class="path-footer"><span class="hint">TLS encryption · verified certificate · private access key</span><a href="#network">Network settings →</a></div></section><section class="panel"><div class="panel-head"><div><h2>Your computers</h2><p class="hint">Saved addresses and paired devices</p></div><a href="#computers">View all →</a></div>${state.devices.length ? deviceRows(state.devices.slice(0,3)) : empty()}</section></div><div>${hostPanel()}<div class="panel"><h2>A complete native session</h2><p class="hint">Desktop control, files, clipboard, audio, display selection, chat and recording are handled by the RustDesk engine.</p><a href="#features">See support and limits →</a></div></div></div><p class="footnote">YesMachine is a new control center built on RustDesk. Native session windows retain the RustDesk name. NoMachine is unchanged.</p>`;
}
function computers() {
  const filtered = state.devices.filter(device => `${device.name} ${device.host}`.toLowerCase().includes(query.toLowerCase()));
  page = Math.max(1, Math.min(page, Math.ceil(filtered.length / 20)));
  return heading('ADDRESS BOOK', 'Computers', 'Private connection profiles stay on this device.', buttons()) + `<div class="panel"><div class="search"><label for="computer-search">Search computers</label><input id="computer-search" type="search" placeholder="Search by name or address" value="${esc(query)}" autocomplete="off"><button class="ghost small" data-action="clear" ${query ? '' : 'hidden'} aria-label="Clear computer search">Clear</button></div><div id="device-list">${filtered.length ? deviceRows(filtered.slice((page-1)*20,page*20)) : empty(Boolean(query))}</div><div class="pagination"><span>${filtered.length ? `${(page-1)*20+1}–${Math.min(page*20,filtered.length)} of ${filtered.length}` : '0 computers'}</span><div class="actions"><button class="secondary small" data-action="previous" ${page<=1?'disabled':''}>Previous</button><button class="secondary small" data-action="next" ${page*20>=filtered.length?'disabled':''}>Next</button></div></div></div>`;
}
function network() {
  return heading('CONNECTION PATH', 'Network', 'LAN access first. Publish an encrypted connection when you need it.') + `<div class="grid"><div><section class="panel feature"><h2>${state.mapping ? 'Published through your router' : 'Local network'}</h2><p class="network-address">${esc(state.mapping ? state.mapping.address : state.lan_ip)}:${state.port}</p><p class="hint">${state.mapping ? `Verified router mapping on ${esc(state.mapping.router)}. External reachability still needs a connection from outside your LAN.` : 'Connect with a private profile from another computer on the same network. No router change is required.'}</p><div class="actions"><button data-action="${state.mapping ? 'export-internet' : 'export-lan'}">Export ${state.mapping ? 'Internet' : 'LAN'} profile ↓</button><button class="secondary" data-action="copy-address">Copy address</button></div></section><section class="panel"><div class="panel-head"><div><h2>UPnP Internet access</h2><p class="hint">Only encrypted TCP port ${state.port} is mapped.</p></div><span class="badge ${state.mapping ? 'ready' : ''}">${state.mapping ? 'PUBLISHED' : 'OFF'}</span></div><p class="hint">Enable this when connecting from outside your home network. Your router must support UPnP and have a public Internet address. Carrier NAT and a second router can prevent incoming access.</p>${state.mapping ? `<dl><div><dt>Router</dt><dd>${esc(state.mapping.router)}</dd></div><div><dt>Lease</dt><dd>${state.mapping.lease ? `${state.mapping.lease / 60} minutes, renewed automatically` : 'Permanent: removed when YesMachine quits'}</dd></div></dl>` : ''}<div class="actions"><button data-action="${state.mapping ? 'upnp-disable' : 'upnp-enable'}" ${state.job.running ? 'disabled' : ''}>${state.mapping ? 'Remove router mapping' : 'Enable UPnP access'}</button><button class="secondary" data-action="router-check" ${state.job.running ? 'disabled' : ''}>Check router</button><button class="ghost" data-action="upnp-cleanup">Clean up an old mapping</button></div>${state.network_error ? `<p class="notice error">${esc(state.network_error)}</p>` : ''}</section><section class="panel"><h2>Pairing and identity</h2><p class="hint">The connection profile contains this computer’s certificate fingerprint and access key. Transfer it privately. The native host still asks you to approve the viewer.</p><dl><div><dt>Certificate SHA-256</dt><dd class="mono">${esc(state.fingerprint)}</dd></div></dl><div class="actions"><button class="secondary small" data-action="copy-fingerprint">Copy fingerprint</button><button class="ghost small" data-action="key-rotate">Replace access key</button></div><p class="hint">Replace the key to revoke old profiles and disconnect existing viewers.</p></section></div><div>${hostPanel()}<section class="panel"><h2>What is exposed?</h2><p class="hint">The control panel is available only on this computer. The native engine accepts only loopback clients. Other devices pass through YesMachine’s TLS tunnel and access-key check.</p><p class="hint">A router mapping is not a successful Internet connection test. Verify from a device on a different network.</p></section></div></div>`;
}
function features() {
  const rows = [
    ['Remote display and keyboard/mouse','Native engine','Screen and input permissions are required on the host OS.'],
    ['Audio and clipboard','Native engine','Use the native session toolbar to control permissions and audio.'],
    ['File transfer','Native engine','Open Files next to a paired computer; transfer in the native window.'],
    ['Multiple monitors and quality controls','Native engine','Select displays, fit, codec and image quality inside the session.'],
    ['Recording, chat and session tools','Native engine','Available in the native session menus; platform support varies.'],
    ['TCP forwarding and terminal','Native engine','Open from a paired computer’s More menu; the host must allow the feature.'],
    ['Certificate-pinned encrypted transport','YesMachine','TLS tunnel checks the host certificate before sending the access key.'],
    ['UPnP port mapping','YesMachine','Ownership checks, conflict refusal, renewal and removal on clean shutdown.'],
    ['Wake-on-LAN','YesMachine','Local broadcast; requires a MAC address and firmware/network support.'],
    ['Windows and macOS packages','Source + setup','Setup scripts are included. This build has been exercised only on Linux.'],
    ['Wayland hosting','OS-dependent','Desktop permission prompts are required. Full input can require the upstream installed service.'],
    ['USB, printer and smart-card forwarding','Not implemented','No transparent peripheral redirection. Transfer files and print locally as a manual workaround.'],
    ['Transparent disk mounting','Not implemented','Use the native file-transfer window instead.'],
    ['Linux virtual desktops and headless login','Not implemented','This version shares an existing logged-in desktop.'],
    ['Phone browser desktop','YesMachine','Cloudflare QR access, mouse, touch, zoom, keyboard, clipboard and revocable sessions.'],
    ['Standard RDP, NX and VNC clients','YesMachine','Native GNOME RDP, installed NoMachine server, and an RFB adapter for VNC viewers.'],
  ];
  return heading('REAL FEATURES, EXPLICIT LIMITS', 'Capabilities', 'The desktop engine does the remote work. YesMachine manages the connection.') + `<section class="panel"><div class="notice warning">This is a usable foundation for a replacement, not full NoMachine feature parity. Features below marked “Native engine” depend on RustDesk and your operating system.</div>${rows.map(([name,status,description])=>`<div class="feature-row"><div><strong>${name}</strong><p>${description}</p></div><span class="badge ${['YesMachine','Native engine'].includes(status)?'ready':''}">${status}</span></div>`).join('')}<div class="actions"><button data-action="native">Open native settings ↗</button></div></section>`;
}
function activity() {
  return heading('LOCAL HISTORY', 'Activity', 'Recent actions on this computer. Access keys and passwords are never logged.') + `<section class="panel">${state.activity.length ? `<ol class="activity">${state.activity.map(event=>`<li data-level="${esc(event.level)}"><time datetime="${esc(event.at)}">${esc(new Date(event.at).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'medium'}))}</time>${esc(event.message)}</li>`).join('')}</ol><p class="hint">Showing the latest ${Math.min(100,state.activity.length)} entries. The app keeps at most 500 locally.</p>` : '<div class="empty"><h3>No activity yet</h3><p>Actions you take in YesMachine will appear here.</p></div>'}</section>`;
}
function clients(){
  const compatibility=state.compatibility;
  if(!compatibility)return heading('','Other clients','Reopen the app to load native-client support.');
  const rdp=compatibility.rdp,nx=compatibility.nomachine,vnc=state.vnc||{};
  function profileButtons(protocol,status){const mapping=state.client_mappings?.[protocol];return `<div class="actions"><button class="secondary small" data-action="export-native" data-protocol="${protocol}">Export ${protocol==='rdp'?'.rdp':'.nxs'} profile</button>${status.listening?`<button class="ghost small" data-action="native-publish" data-protocol="${protocol}">${mapping?'Remove Internet mapping':'Publish via UPnP'}</button>`:''}${mapping?`<button class="secondary small" data-action="export-native-internet" data-protocol="${protocol}">Export Internet profile</button>`:''}</div>`;}
  return heading('','Other clients','Standard RDP, NoMachine Player, and VNC viewers.')+`<div class="compat-grid"><section class="panel"><div class="panel-head"><h2>Standard RDP</h2><span class="badge ${rdp.listening?'ready':''}">${rdp.listening?'READY':'OFF'}</span></div><p class="hint">Connect with Windows Remote Desktop, Windows App, Remmina, FreeRDP, and other modern RDP clients.</p><p class="client-address">${esc(state.lan_ip)}:${rdp.port}</p><p class="hint">${esc(rdp.provider)} · native TLS/NLA and separate RDP credentials. YesMachine’s access key and approval dialog do not apply.</p>${rdp.error?`<p class="notice">${esc(rdp.error)}</p>`:''}<div class="actions">${rdp.managed?'<button data-action="rdp-setup">Set RDP credentials</button><button class="secondary" data-action="rdp-stop">Stop RDP</button>':!rdp.enabled&&rdp.available&&state.engine.platform==='Linux'?'<button data-action="rdp-setup">Enable RDP</button>':''}</div>${profileButtons('rdp',rdp)}</section><section class="panel"><div class="panel-head"><h2>NoMachine Player</h2><span class="badge ${nx.listening?'ready':''}">${nx.listening?'READY':'SERVER REQUIRED'}</span></div><p class="hint">Uses the already installed NoMachine server and its NX protocol. YesMachine does not emulate or translate proprietary NX.</p><p class="client-address">${esc(state.lan_ip)}:${nx.port}</p><p class="hint">Sign in with the NoMachine server’s system credentials. NoMachine is not updated or reconfigured by these controls.</p>${profileButtons('nomachine',nx)}</section><section class="panel"><div class="panel-head"><h2>VNC / RFB</h2><span class="badge ${vnc.active?'ready':''}">${vnc.active?'READY':'OFF'}</span></div><p class="hint">Share this desktop with TigerVNC, TightVNC, Remmina and compatible RVNC versions. Viewer policies may restrict third-party servers.</p><p class="client-address">${esc(state.lan_ip)}:${vnc.port||5900}</p><p class="hint">${vnc.clients||0} connected viewers. VeNCrypt clients can use TLS. Classic VNC does not encrypt screen traffic; use it on a trusted LAN or VPN.</p><button data-action="${vnc.active?'vnc-stop':'vnc-setup'}" ${state.job.running?'disabled':''}>${vnc.active?'Stop VNC':'Enable VNC'}</button></section></div><section class="panel"><h2>Connect from YesMachine</h2><p class="hint">Add a computer and choose Standard RDP or NoMachine Player as its protocol. Connect opens the appropriate installed native client; exporting its profile lets you choose another app.</p><button data-action="add">Add computer</button></section>`;
}
function phone(){
  const mobile=state.mobile||{},managed=Boolean(state.capabilities?.phone_management);
  const date=value=>value?new Date(value*1000).toLocaleString():'Open ended';
  const links=mobile.links||[],sessions=mobile.sessions||[];
  const rows=links.map(link=>`<li class="share-row"><div><strong>Access link ${esc(link.id.slice(0,6))}</strong><p class="hint">${esc(date(link.expires))} · ${link.sessions} signed-in session${link.sessions===1?'':'s'}</p></div><div class="actions"><button class="small" data-action="show-qr" data-id="${link.id}">QR code</button><button class="secondary small" data-action="mobile-cycle" data-id="${link.id}">Cycle key</button><button class="ghost small" data-action="mobile-revoke" data-id="${link.id}">Revoke</button></div></li>`).join('');
  const browsers=sessions.map(session=>`<li class="share-row"><div><strong>${esc(session.device)} · ${esc(session.id.slice(0,6))}</strong><p class="hint">${session.connected?'Connected':'Signed in'} · link ${esc(session.share_id.slice(0,6))} · ${esc(date(session.expires))}</p><p class="hint">Last seen ${esc(new Date(session.last_seen*1000).toLocaleString())}</p></div><button class="secondary small" data-action="mobile-session-revoke" data-id="${session.id}">Revoke session</button></li>`).join('');
  return heading('','Phone access','Private browser desktop links, QR codes, and session controls.')+`<div class="grid"><section class="panel"><div class="panel-head"><h2>Share this desktop</h2><span class="badge ${mobile.active?'ready':''}">${mobile.active?'TUNNEL ACTIVE':'OFF'}</span></div>${mobile.active?`<p class="client-address">${esc(mobile.url)}</p><p class="hint">${mobile.clients} active browser connection${mobile.clients===1?'':'s'}. Each link below has its own revocable access key.</p>`:''}<form id="mobile-form" novalidate><div class="field"><label for="share-duration">Share expiry</label><select id="share-duration" name="duration">${managed?'<option value="0" selected>No expiry — until revoked</option>':''}<option value="900">15 minutes</option><option value="3600" ${managed?'':'selected'}>1 hour</option><option value="14400">4 hours</option></select></div><button type="submit" ${state.job.running?'disabled':''}>${mobile.active&&managed?'Create another QR share':'Generate QR tunnel'}</button></form>${!managed?'<p class="hint">Session management will appear after the service update is loaded.</p>':''}${mobile.error?`<p class="notice error">${esc(mobile.error)}</p>`:''}<p class="notice">Anyone with a QR code or its key can control the desktop. Revoke a link to remove its sessions, or cycle its key to issue a replacement.</p><p class="hint">No expiry means no app-imposed time limit. The app and temporary Cloudflare tunnel must remain running; this is not a permanent reserved domain.</p>${mobile.active?'<button class="secondary" data-action="mobile-stop">Revoke all shares and stop tunnel</button>':''}</section><section class="panel"><h2>Phone controls</h2><p class="hint">Session controls are tucked into the right-side tab. Drag the pointer, tap to click, hold and drag for a mouse drag, and use two fingers to pan or pinch to zoom.</p><p class="hint">Portrait starts in a readable 1:1 viewport. Turning to landscape fits the screen; you can change either mode from Display in the session menu.</p><p class="hint">The QR key is kept in the URL fragment, removed after sign-in, and exchanged for an HttpOnly cookie. The endpoint serves only the viewer, never local app settings.</p></section></div><section class="panel"><div class="panel-head"><div><h2>Access links</h2><p class="hint">Cycling a key disconnects that link’s sessions and makes old QR codes invalid.</p></div><span class="badge">${links.length} ACTIVE</span></div><ul class="share-list">${rows||'<li class="hint">No active links yet. Generate a QR share above.</li>'}</ul></section><section class="panel"><div class="panel-head"><div><h2>Browser sessions</h2><p class="hint">Revoke one device here. Revoke its access link as well to prevent it from signing in again.</p></div><span class="badge">${sessions.length} SIGNED IN</span></div><ul class="share-list">${browsers||'<li class="hint">Phones and browsers appear here after they scan a QR code.</li>'}</ul></section>`;
}
function settings(){
  const colors=themeDraft||state.theme||state.theme_defaults;
  if(!colors)return heading('','Settings','Reopen the app to load saved color preferences.');
  const labels={background:'App background',surface:'Panels and sidebar',text:'Main text',muted:'Secondary text',primary:'Primary buttons',secondary:'Selected surfaces',border:'Outlines and scrollbars',success:'Success text',warning:'Warning text',danger:'Error text',link:'Links and icons','on-primary':'Button text'};
  return heading('','Settings','Choose any colors. Changes preview immediately and stay local to this app.')+`<section class="panel"><h2>Appearance</h2><p class="hint">Dark backgrounds and white text are the default. The sidebar and the content beside it scroll independently.</p><form id="theme-form" novalidate><div class="theme-grid">${themeRoles.map(role=>`<div class="field"><label for="color-${role}">${labels[role]}</label><div class="color-field"><input type="color" aria-label="Pick ${labels[role].toLowerCase()}" data-color-picker="${role}" value="${esc(colors[role])}"><input id="color-${role}" name="${role}" data-color-text="${role}" value="${esc(colors[role])}" maxlength="7" spellcheck="false" aria-label="${labels[role]} hex color"></div></div>`).join('')}</div><p id="theme-error" class="field-error" role="alert"></p><div class="actions"><button type="submit" value="save">Save colors</button><button type="submit" value="reset" class="secondary">Reset to dark defaults</button><button type="submit" value="cancel" class="ghost">Cancel preview</button></div></form><p class="hint">All RGB colors are allowed. Choose contrasting text and background colors so controls remain readable.</p></section>`;
}
function render() {
  if (!state) return;
  const active = document.activeElement;
  const focusWasInMain = Boolean(active?.closest('main'));
  const focusId = active?.id;
  const selection = focusId === 'computer-search' ? [active.selectionStart,active.selectionEnd] : null;
  const focusAction = active?.dataset?.action, focusDevice = active?.dataset?.id;
  const next = route();
  currentRoute = next.name;
  query = next.params.get('q') || '';
  const name = ({overview:'Overview',computers:'Computers',network:'Network',features:'Capabilities',activity:'Activity',phone:'Phone access',clients:'Other clients',settings:'Settings'})[currentRoute];
  document.title = `YesMachine — ${name}`;
  $('#route-label').textContent = name;
  document.querySelectorAll('nav a').forEach(link => { if(link.dataset.route===currentRoute)link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
  $('#main').innerHTML = ({overview,computers,network,features,activity,phone,clients,settings})[currentRoute]();
  if (selection && $('#computer-search')) { $('#computer-search').focus(); $('#computer-search').setSelectionRange(...selection); }
  else if (focusAction && focusWasInMain) {
    const candidate = [...document.querySelectorAll('main [data-action]')].find(node=>node.dataset.action===focusAction && node.dataset.id===focusDevice);
    candidate?.focus();
  }
}
function confirmAction(title, description, label, callback, danger = false) {
  confirmInvoker = document.activeElement;
  $('#confirm-title').textContent = title;
  $('#confirm-description').textContent = description;
  $('#confirm-accept').textContent = label;
  $('#confirm-accept').className = danger ? 'danger' : '';
  $('#confirm-error').textContent = '';
  confirmCallback = callback;
  $('#confirm-dialog').showModal();
  $('#confirm-cancel').focus();
}
function closeConfirm() {
  $('#confirm-dialog').close();
  confirmCallback = null;
  confirmInvoker?.isConnected && confirmInvoker.focus();
}
function closeDevice() {
  $('#device-dialog').close();
  deviceDirty = false;
  deviceInvoker?.isConnected && deviceInvoker.focus();
}
function cancelDevice() {
  if (!deviceDirty) return closeDevice();
  confirmAction('Discard these changes?', 'The changes in this computer profile have not been saved.', 'Discard changes', ()=>closeDevice(), true);
}
function editDevice(id) {
  deviceInvoker = document.activeElement;
  const form = $('#device-form'); form.reset();
  form.querySelectorAll('.field-error').forEach(node=>node.textContent='');
  form.querySelectorAll('[aria-invalid]').forEach(node=>node.removeAttribute('aria-invalid'));
  const device = state.devices.find(item=>item.id===id);
  for (const [key,value] of Object.entries(device || {id:'',port:21180})) if(form.elements[key])form.elements[key].value=value;
  $('#device-title').textContent = device ? 'Edit computer' : 'Add a computer';
  $('#pair-details').open = Boolean(device && !device.paired);
  $('#device-token').type = 'password';
  $('#toggle-token').checked = false;
  deviceDirty = false;
  $('#device-dialog').showModal(); $('#device-name').focus();
  updateProtocol(false);
}

async function action(name, id, extra={}) {
  if(['mobile-start','mobile-cycle'].includes(name))pendingQr=true;
  try { await api('action',{action:name,id,...extra}); await refresh(true); }
  catch(error) { feedback(error.message,true); throw error; }
}
async function showQr(identity=null){try{const value=await api(`mobile/share${identity?'?id='+encodeURIComponent(identity):''}`);if(qrBlob)URL.revokeObjectURL(qrBlob);qrBlob=URL.createObjectURL(new Blob([value.qr],{type:'image/svg+xml'}));const image=new Image();image.src=qrBlob;image.className='qr-image';image.alt='Private QR code for this phone desktop share';$('#share-qr-container').replaceChildren(image);$('#private-link').value=value.url;$('#qr-expiry').textContent=value.expires?`Expires ${new Date(value.expires*1000).toLocaleString()}`:'No expiry — valid until this link is revoked, cycled, or the app stops.';if(!$('#qr-dialog').open)$('#qr-dialog').showModal();}catch(error){feedback(error.message,true);}}
function closeQr(){$('#qr-dialog').close();$('#private-link').value='';$('#share-qr-container').replaceChildren();if(qrBlob)URL.revokeObjectURL(qrBlob);qrBlob=null;}
async function exportNative(protocol,internet=false,id=null){const parameters=new URLSearchParams({protocol,internet:internet?'1':'0'});if(id)parameters.set('id',id);const value=await api(`client-profile?${parameters}`);const url=URL.createObjectURL(new Blob([value.content],{type:'application/octet-stream'}));const link=document.createElement('a');link.href=url;link.download=value.name.replace(/[^a-zA-Z0-9_-]/g,'_')+value.extension;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Native client profile exported. Passwords are not included.');}
function updateProtocol(changePort=true){const protocol=$('#device-protocol').value;const native=protocol!=='yesmachine';$('#native-username-field').hidden=!native;$('#fingerprint-field').hidden=$('#access-key-field').hidden=native;$('#pair-details summary').textContent=native?'Wake-on-LAN':'Pairing details and Wake-on-LAN';if(changePort&&[21180,3389,4000,5900].includes(Number($('#device-port').value)))$('#device-port').value=({yesmachine:21180,rdp:3389,nomachine:4000,vnc:5900})[protocol];}
async function exportProfile(internet) {
  const profile = await api(`profile${internet?'?internet=1':''}`);
  const blob = new Blob([JSON.stringify(profile,null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href=url; link.download=`${state.hostname}${internet?'-internet':'-lan'}.yesmachine.json`; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  notify('Private connection profile exported. Transfer it securely to your other device.');
}
async function copy(value) {
  try { await navigator.clipboard.writeText(value); notify('Copied to clipboard.'); }
  catch { feedback('Clipboard access was denied. Select and copy the displayed value.',true); }
}

async function handleAction(button) {
  if (!button || button.disabled) return;
  const name = button.dataset.action, id = button.dataset.id;
  try {
    if (name==='add'||name==='edit') editDevice(id);
    else if(name==='import') $('#import-profile').click();
    else if(name==='native') await action('engine-open');
    else if(name==='retry') await refresh(true);
    else if(name==='rdp-setup'){$('#rdp-form').reset();$('#rdp-title').textContent=state.compatibility.rdp.managed?'Set RDP credentials':'Enable standard RDP';$('#rdp-username').value=state.compatibility.rdp.username||'yesmachine';$('#rdp-port').value=state.compatibility.rdp.port||3389;$('#rdp-port').readOnly=state.compatibility.rdp.managed;$('#rdp-error').textContent='';$('#rdp-dialog').showModal();$('#rdp-username').focus();}
    else if(name==='vnc-setup'){$('#vnc-form').reset();$('#vnc-error').textContent='';$('#vnc-dialog').showModal();$('#vnc-password').focus();}
    else if(name==='rdp-stop')confirmAction('Stop standard RDP?','RDP and phone viewers will disconnect. YesMachine will restore the desktop-sharing settings it replaced.','Stop RDP',()=>action('rdp-stop'),true);
    else if(name==='show-qr')await showQr(id);
    else if(name==='mobile-stop')confirmAction('Revoke this phone share?','The public tunnel, key and browser sessions will stop working immediately. The local RDP host has a separate Stop RDP control.','Revoke share',()=>action('mobile-stop'),true);
    else if(name==='mobile-cycle')confirmAction('Cycle this access key?','Old QR codes and browser sessions for this link will be revoked. A new QR key will replace them.','Cycle key',()=>action('mobile-cycle',id),true);
    else if(name==='mobile-revoke')confirmAction('Revoke this access link?','Its QR code and all of its browser sessions will stop working. Other links stay active.','Revoke link',()=>action('mobile-revoke',id),true);
    else if(name==='mobile-session-revoke')confirmAction('Revoke this browser session?','This device will disconnect. It can sign in again if it still has a valid access link.','Revoke session',()=>action('mobile-session-revoke',id),true);
    else if(name==='mobile-regenerate')confirmAction('Generate a new phone share?','Previous QR codes and active browser sessions will be revoked. The new share expires in one hour.','Generate new share',()=>action('mobile-start',null,{duration:3600}),true);
    else if(name==='export-native'||name==='export-native-internet')await exportNative(button.dataset.protocol,name.endsWith('internet'));
    else if(name==='export-client-device')await exportNative(state.devices.find(d=>d.id===id).protocol,false,id);
    else if(name==='native-publish'){const protocol=button.dataset.protocol;const exists=state.client_mappings?.[protocol];confirmAction(exists?'Remove this Internet mapping?':`Publish ${protocol==='rdp'?'RDP':'NoMachine'} through your router?`,exists?'Only YesMachine’s mapping for this native protocol will be removed.':'This exposes the native server through your router. Its own authentication applies; YesMachine’s pairing key does not protect this port.',exists?'Remove mapping':'Publish native server',()=>action(exists?'client-upnp-disable':'client-upnp-enable',null,{protocol}));}
    else if(name==='clear') { query='';page=1;history.replaceState(null,'','#computers');render();$('#computer-search')?.focus(); }
    else if(name==='next'||name==='previous') {page+=name==='next'?1:-1;render();}
    else if(name==='remove') { const device=state.devices.find(d=>d.id===id); confirmAction(`Remove ${device.name}?`,'This removes its saved profile from this computer. The remote computer is unchanged.','Remove computer',async()=>{await api('devices/remove',{id});notify('Computer removed.');await refresh(true);},true); }
    else if(name==='upnp-enable') confirmAction('Publish this computer through your router?',`YesMachine will map encrypted TCP port ${state.port}. Paired devices with the access key can request access; the native desktop still requires your approval. The mapping is removed when hosting stops or YesMachine quits.`,'Enable UPnP',()=>action('upnp-enable'));
    else if(name==='upnp-cleanup') confirmAction('Remove an old YesMachine mapping?','The app will discover the router and remove only a mapping owned by this installation. Other applications are left unchanged.','Remove old mapping',()=>action('upnp-disable'));
    else if(name==='host-stop') confirmAction('Stop hosting this computer?','Remote viewers will lose their connection. The desktop and its apps remain running. Any owned router mapping will be removed first.','Stop hosting',()=>action('host-stop'),true);
    else if(name==='key-rotate') confirmAction('Replace this computer’s access key?','Existing viewers will be disconnected and previously exported profiles will stop working. Export fresh profiles for your trusted devices afterward.','Replace access key',()=>action('key-rotate'),true);
    else if(name==='quit') confirmAction('Quit YesMachine?','Hosting and viewer tunnels will close. The app will remove its router mapping and stop its own host process.','Quit YesMachine',async()=>{await action('quit');feedback('YesMachine has stopped. You can close this window.');},true);
    else if(name==='export-lan'||name==='export-internet') confirmAction('Export a private connection profile?','The file contains an access key. Anyone who receives it can request a connection to this computer. Send it privately and keep it out of shared folders.','Export profile',()=>exportProfile(name==='export-internet'));
    else if(name==='copy-fingerprint') await copy(state.fingerprint);
    else if(name==='copy-address') await copy(`${state.mapping?.address||state.lan_ip}:${state.port}`);
    else await action(name,id);
  } catch(error) { feedback(error.message,true); }
}
document.addEventListener('click',event=>handleAction(event.target.closest('[data-action]')));
$('#utility-actions').addEventListener('submit',event=>{
  event.preventDefault();
  handleAction({dataset:{action:event.submitter.value}});
});

$('#confirm-dialog').addEventListener('cancel',event=>{event.preventDefault();closeConfirm();});
$('#confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(event.submitter.value==='cancel')return closeConfirm();
  const button=$('#confirm-accept'); if(button.disabled)return;
  button.disabled=true;
  try { await confirmCallback?.();closeConfirm(); }
  catch(error) { $('#confirm-error').textContent=error.message; }
  finally {button.disabled=false;}
});
$('#device-dialog').addEventListener('cancel',event=>{event.preventDefault();cancelDevice();});
$('#device-form').addEventListener('input',()=>{deviceDirty=true;});
$('#device-protocol').addEventListener('change',()=>updateProtocol());
$('#toggle-token').addEventListener('change',event=>{$('#device-token').type=event.target.checked?'text':'password';});
$('#device-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(event.submitter?.value==='cancel')return cancelDevice();
  const form=event.currentTarget;const button=$('#device-save');if(button.disabled)return;
  const data=Object.fromEntries(new FormData(form));
  for(const node of form.querySelectorAll('.field-error'))node.textContent='';
  for(const node of form.querySelectorAll('[aria-invalid]'))node.removeAttribute('aria-invalid');
  const errors={};
  if(!data.name.trim())errors.name='Enter a computer name.';
  if(!/^[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}$/.test(data.host.trim()))errors.host='Enter an IP address or hostname, without a URL.';
  if(!Number.isInteger(Number(data.port))||Number(data.port)<1||Number(data.port)>65535)errors.port='Use a port from 1 to 65535.';
  if(data.fingerprint&&!/^[a-fA-F0-9:]{64,95}$/.test(data.fingerprint))errors.fingerprint='Paste the complete SHA-256 fingerprint.';
  if(data.token&&!/^[a-zA-Z0-9_-]{32,100}$/.test(data.token))errors.token='Paste the complete access key.';
  if(Object.keys(errors).length) {for(const [key,message]of Object.entries(errors)){$(`#${key}-error`).textContent=message;form.elements[key].setAttribute('aria-invalid','true');}const first=Object.keys(errors)[0];if(['token','fingerprint'].includes(first))$('#pair-details').open=true;form.elements[first].focus();return;}
  button.disabled=true;
  try{await api('devices/save',data);closeDevice();notify('Computer saved.');await refresh(true);}
  catch(error){$('#form-error').textContent=error.message;}
  finally{button.disabled=false;}
});
$('#import-profile').addEventListener('change',async event=>{
  const file=event.target.files[0]; if(!file)return;
  try{if(file.size>131072)throw new Error('The connection profile is too large. Choose a YesMachine JSON profile.');const profile=JSON.parse(await file.text());await api('devices/import',{profile});notify('Computer paired and saved.');await refresh(true);}
  catch(error){feedback(error instanceof SyntaxError?'The file is not valid JSON. Export a fresh YesMachine connection profile.':error.message,true);}
  finally{event.target.value='';}
});
$('#show-rdp-password').addEventListener('change',event=>{$('#rdp-password').type=event.target.checked?'text':'password';});
$('#rdp-form').addEventListener('submit',async event=>{event.preventDefault();if(event.submitter.value==='cancel')return $('#rdp-dialog').close();const username=$('#rdp-username').value.trim(),password=$('#rdp-password').value,port=Number($('#rdp-port').value);if(!username||password.length<12){$('#rdp-error').textContent='Enter a username and a password of at least 12 characters.';return;}try{await action('rdp-start',null,{username,password,port});$('#rdp-password').value='';$('#rdp-dialog').close();}catch(error){$('#rdp-error').textContent=error.message;}});
$('#rdp-dialog').addEventListener('close',()=>{$('#rdp-password').value='';});
$('#qr-form').addEventListener('submit',async event=>{event.preventDefault();if(event.submitter.value==='copy')await copy($('#private-link').value);else closeQr();});
$('#qr-dialog').addEventListener('cancel',event=>{event.preventDefault();closeQr();});
document.addEventListener('submit',async event=>{
  if(event.target.id==='mobile-form'){event.preventDefault();const duration=Number(new FormData(event.target).get('duration'));confirmAction('Generate a public phone desktop share?','This creates a Cloudflare browser endpoint and a private QR key. Anyone with that key can control this desktop until expiry or revocation. A local managed RDP host will start if needed.','Generate tunnel',()=>action('mobile-start',null,{duration}));}
  if(event.target.id==='theme-form'){event.preventDefault();const mode=event.submitter.value;if(mode==='reset'){themeDraft={...state.theme_defaults};themeDirty=true;applyTheme(themeDraft);render();return;}if(mode==='cancel'){themeDraft=null;themeDirty=false;applyTheme(state.theme);render();return;}const colors=Object.fromEntries(new FormData(event.target));if(themeRoles.some(role=>!/^#[a-f0-9]{6}$/i.test(colors[role]))){$('#theme-error').textContent='Use six-digit hex colors such as #3399ff.';return;}event.submitter.disabled=true;try{const result=await api('theme',{colors});state.theme=result.theme;themeDraft=null;themeDirty=false;applyTheme(result.theme);localStorage.setItem('yesmachine.theme',JSON.stringify(result.theme));notify('Colors saved.');}catch(error){$('#theme-error').textContent=error.message;}finally{event.submitter.disabled=false;}}
});
document.addEventListener('input',event=>{const role=event.target.dataset.colorPicker||event.target.dataset.colorText;if(!role)return;themeDraft={...(themeDraft||state.theme)};themeDirty=true;if(/^#[a-f0-9]{6}$/i.test(event.target.value)){themeDraft[role]=event.target.value;applyTheme(themeDraft);const counterpart=event.target.dataset.colorPicker?$(`[data-color-text="${role}"]`):$(`[data-color-picker="${role}"]`);counterpart.value=event.target.value;}});
document.addEventListener('toggle',event=>{if(event.target.tagName!=='DETAILS'||!event.target.open)return;const menu=$('.menu',event.target);if(!menu)return;const rect=$('summary',event.target).getBoundingClientRect();const height=menu.getBoundingClientRect().height;menu.style.left=`${Math.max(8,Math.min(innerWidth-188,rect.right-180))}px`;menu.style.top=`${rect.bottom+height+8<innerHeight?rect.bottom+4:Math.max(8,rect.top-height-4)}px`;},true);
document.addEventListener('input',event=>{if(event.target.id!=='computer-search'||event.isComposing)return;query=event.target.value;page=1;history.replaceState(null,'',`#computers${query?'?q='+encodeURIComponent(query):''}`);render();});
document.addEventListener('compositionend',event=>{if(event.target.id==='computer-search')event.target.dispatchEvent(new Event('input',{bubbles:true}));});
window.addEventListener('hashchange',()=>{page=1;render();});
window.addEventListener('beforeunload',event=>{if(deviceDirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
refresh();setInterval(()=>refresh(),3000);

$('#vnc-form').addEventListener('submit',async event=>{event.preventDefault();if(event.submitter?.value==='cancel')return $('#vnc-dialog').close();const password=$('#vnc-password').value,port=Number($('#vnc-port').value);if(!/^[ -~]{8}$/.test(password)||!Number.isInteger(port)||port<1024||port>65535){$('#vnc-error').textContent='Use exactly 8 ASCII characters and a port from 1024 to 65535.';return;}const button=$('#vnc-save');button.disabled=true;try{await action('vnc-start',null,{password,port});$('#vnc-dialog').close();}catch(error){$('#vnc-error').textContent=error.message;}finally{button.disabled=false;}});
$('#vnc-dialog').addEventListener('close',()=>{$('#vnc-password').value='';});
