'use strict';
const $ = id => document.getElementById(id);
const peers = new Map();
let socket, selfId, iceServers = [], active = false, retryTimer, retries = 0, generation = 0;
const params = new URLSearchParams(location.hash.slice(1));
$('room').value = params.get('room') || Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
$('server').value = params.get('server') || `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;
$('name').value = sessionStorage.getItem('p2p-name') || '';
// Remove the old proxy's offline worker on an upgraded GitHub Pages installation.
if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister())).catch(() => {});
function log(text, author) {
  const item = document.createElement('div'); item.className = author ? 'message' : 'system';
  if (author) { const label = document.createElement('small'); label.textContent = `${author} · ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`; item.append(label); }
  item.append(document.createTextNode(text)); $('messages').append(item);
  while ($('messages').children.length > 500) $('messages').firstChild.remove();
  $('messages').scrollTop = $('messages').scrollHeight;
}
function render() {
  $('peers').replaceChildren(); let ready = 0;
  for (const p of peers.values()) {
    const open = p.channel?.readyState === 'open'; if (open) ready++;
    const li = document.createElement('li'); li.textContent = `${p.name} — ${open ? 'чат подключён' : p.pc.connectionState}`; $('peers').append(li);
  }
  $('count').textContent = selfId ? peers.size + 1 : 0;
  $('send').disabled = !ready;
}
function signal(to, data) {
  if (socket?.readyState !== WebSocket.OPEN || !selfId) throw Error('Signaling недоступен');
  socket.send(JSON.stringify({type:'signal', to, data}));
}
function remove(id) {
  const p = peers.get(id); if (!p) return;
  peers.delete(id); clearTimeout(p.timer); p.channel?.close(); p.pc.close(); render();
}
function bindChannel(p, channel) {
  if (channel.label !== 'chat' || p.channel) { channel.close(); return; }
  p.channel = channel;
  channel.onopen = () => { log(`${p.name}: чат подключён`); render(); };
  channel.onclose = render; channel.onerror = render;
  channel.onmessage = event => {
    if (typeof event.data !== 'string' || event.data.length > 25000) return;
    try { const m = JSON.parse(event.data); if (m.type === 'chat' && typeof m.text === 'string' && m.text.length <= 4000) log(m.text, p.name); } catch {}
  };
}
function add(info, initiator) {
  if (peers.has(info.id)) return peers.get(info.id);
  const pc = new RTCPeerConnection({iceServers});
  const p = {pc, name:info.name, initiator, candidates:[], chain:Promise.resolve(), timer:null, attempts:0};
  peers.set(info.id, p);
  pc.onicecandidate = e => { if (e.candidate) { try { signal(info.id, {candidate:e.candidate.toJSON()}); } catch {} } };
  pc.ondatachannel = e => bindChannel(p, e.channel);
  pc.onconnectionstatechange = () => {
    render(); clearTimeout(p.timer);
    if (pc.connectionState === 'connected') p.attempts = 0;
    if (['failed','disconnected'].includes(pc.connectionState)) {
      p.timer = setTimeout(() => {
        if (!peers.has(info.id)) return;
        if (initiator && p.attempts++ < 3 && socket?.readyState === WebSocket.OPEN) {
          p.chain = p.chain.then(() => offer(info.id, true)).catch(e => log(e.message));
        } else log(`Нет соединения с ${p.name}. Проверьте TURN или повторно войдите в комнату.`);
      }, pc.connectionState === 'failed' ? 1500 : 8000);
    }
  };
  if (initiator) bindChannel(p, pc.createDataChannel('chat', {ordered:true}));
  render(); return p;
}
async function offer(id, restart = false) {
  const p = peers.get(id); if (!p) return;
  await p.pc.setLocalDescription(await p.pc.createOffer({iceRestart:restart}));
  signal(id, {description:p.pc.localDescription.toJSON()});
}
async function receive(id, data) {
  const p = peers.get(id); if (!p) return;
  if (data.description) {
    await p.pc.setRemoteDescription(data.description);
    for (const c of p.candidates.splice(0)) await p.pc.addIceCandidate(c);
    if (data.description.type === 'offer') {
      await p.pc.setLocalDescription(await p.pc.createAnswer());
      signal(id, {description:p.pc.localDescription.toJSON()});
    }
  } else if (data.candidate) {
    if (p.pc.remoteDescription) await p.pc.addIceCandidate(data.candidate);
    else if (p.candidates.length < 256) p.candidates.push(data.candidate);
  }
}
function controls(joining) {
  ['name','room','server','join'].forEach(id => $(id).disabled = joining);
  $('leave').disabled = !joining;
}
function connect() {
  const epoch = ++generation;
  selfId = null; for (const id of [...peers.keys()]) remove(id);
  $('status').textContent = 'Подключение к signaling…';
  const ws = socket = new WebSocket($('server').value.trim());
  const deadline = setTimeout(() => ws.close(), 15000);
  ws.onopen = () => ws.send(JSON.stringify({type:'join', room:$('room').value.trim(), name:$('name').value.trim()}));
  ws.onmessage = event => {
    if (generation !== epoch) return;
    let m; try { m = JSON.parse(event.data); } catch { return; }
    if (m.type === 'joined') {
      clearTimeout(deadline); selfId = m.id; retries = 0; iceServers = m.iceServers;
      $('status').textContent = 'В комнате · signaling подключён'; log('Вы вошли в комнату.');
      // Only the newcomer offers; the existing participant answers. No offer collision.
      for (const info of m.peers) { const p = add(info, true); p.chain = p.chain.then(() => offer(info.id)).catch(e => log(e.message)); }
      render();
    } else if (m.type === 'peer-joined') { add(m, false); }
    else if (m.type === 'peer-left') { const p = peers.get(m.id); if (p) log(`${p.name} вышел`); remove(m.id); }
    else if (m.type === 'signal') { const p = peers.get(m.from); if (p) p.chain = p.chain.then(() => receive(m.from, m.data)).catch(e => log(`WebRTC: ${e.message}`)); }
    else if (m.type === 'error') { log(m.message); if (!selfId) stop(); }
  };
  ws.onerror = () => { $('notice').textContent = 'Signaling недоступен. Проверьте URL и TLS.'; };
  ws.onclose = () => {
    clearTimeout(deadline); if (generation !== epoch) return;
    $('status').textContent = 'Signaling отключён';
    // Live DataChannels continue until the reconnect creates a fresh room membership.
    if (active) { const delay = Math.min(15000, 1000 * 2 ** Math.min(retries++,4)); retryTimer = setTimeout(connect, delay); }
  };
}
function stop() {
  active = false; generation++; clearTimeout(retryTimer); socket?.close(); selfId = null;
  for (const id of [...peers.keys()]) remove(id);
  controls(false); render(); $('status').textContent = 'Не подключён';
}
$('joinForm').onsubmit = e => {
  e.preventDefault(); $('notice').textContent = '';
  try {
    const url = new URL($('server').value.trim());
    if (!['ws:','wss:'].includes(url.protocol) || url.hostname === '0.0.0.0') throw Error('Укажите ws:// или wss:// с настоящим адресом сервера');
    if (location.protocol === 'https:' && url.protocol !== 'wss:') throw Error('Для HTTPS-страницы требуется wss://');
    if (!window.RTCPeerConnection) throw Error('Браузер не поддерживает WebRTC');
    sessionStorage.setItem('p2p-name', $('name').value.trim());
    active = true; controls(true); connect();
  } catch (err) { stop(); $('notice').textContent = err.message; }
};
$('leave').onclick = stop;
$('copy').onclick = async () => {
  const url = new URL(location.href); url.hash = new URLSearchParams({room:$('room').value.trim(),server:$('server').value.trim()});
  try { await navigator.clipboard.writeText(url.href); $('notice').textContent = 'Приглашение скопировано'; }
  catch { window.prompt('Скопируйте приглашение:', url.href); }
};
$('sendForm').onsubmit = e => {
  e.preventDefault(); const text = $('text').value.trim(); if (!text || text.length > 4000) return;
  let sent = 0; const payload = JSON.stringify({type:'chat',text});
  for (const p of peers.values()) {
    if (p.channel?.readyState === 'open' && p.channel.bufferedAmount < 262144) {
      try { p.channel.send(payload); sent++; } catch {}
    }
  }
  if (sent) { log(text, `${$('name').value.trim()} (вы)`); $('text').value = ''; }
  $('notice').textContent = sent === peers.size && sent ? '' : `Отправлено ${sent} из ${peers.size} участников. Остальным сообщение не доставлено.`;
};
$('text').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('sendForm').requestSubmit(); } };
