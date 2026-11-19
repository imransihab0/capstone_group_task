import { api, esc } from './api.js';
import * as db from './db.js';
import { can } from './auth.js';
const $ = (s) => document.querySelector(s);
let cond = null, method = null, pin = '', qr, drawing = false, signed = false;

export const post = (rec) => api('/handoffs', { method: 'POST', body: rec, headers: { 'Idempotency-Key': rec.idempotencyKey } });
export async function updateNet() {
  const n = await db.count(), bar = $('#netbar');
  const on = navigator.onLine;
  bar.className = 'netbar ' + (on && n === 0 ? 'on' : 'off');
  bar.textContent = on ? (n ? `Online — syncing ${n} queued items…` : 'Online (Synced)') : `Offline Mode — ${n} items queued for sync`;
}
export async function sync() { if (navigator.onLine) { await db.flush(post); updateNet(); } }

const sha = async (t) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(t)))].map((b) => b.toString(16).padStart(2, '0')).join('');
const gps = () => new Promise((res) => navigator.geolocation ? navigator.geolocation.getCurrentPosition(
  (p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }), () => res({ lat: 0, lng: 0 }), { timeout: 8000, enableHighAccuracy: true }) : res({ lat: 0, lng: 0 }));
const pick = (sel, cb) => document.querySelectorAll(sel).forEach((b) => b.onclick = () => { document.querySelectorAll(sel).forEach((x) => x.classList.remove('active')); b.classList.add('active'); cb(b.dataset); });
const dots = () => ($('#pin-dots').textContent = '●'.repeat(pin.length) + '○'.repeat(4 - pin.length));

function detail(s) {
  if (!s) return;
  const pos = Math.max(0, Math.min(100, (s.currentTemp / 12) * 100));
  $('#detail').innerHTML = `<div class="flex justify-between items-center"><b>${esc(s.batchCode)}</b><span class="badge b-${s.status}">${s.status}</span></div>
  <p class="text-sm text-slate-500">${esc(s.origin)} → ${esc(s.destination)} · ETA ${new Date(s.eta).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
  <div class="gauge"><i style="left:${(s.minTemp / 12) * 100}%;width:${((s.maxTemp - s.minTemp) / 12) * 100}%"></i><u style="left:${pos}%"></u></div>
  <div class="flex justify-between text-xs text-slate-500"><span>0°C</span><b class="t-${s.status}">${(+s.currentTemp).toFixed(1)}°C · safe ${s.minTemp}–${s.maxTemp}°C</b><span>12°C</span></div>`;
}
async function loadAssigned() {
  const box = $('#assigned'); box.innerHTML = '<div class="skeleton"></div>'.repeat(3);
  try {
    const l = (await api('/shipments?assigned=me')).filter((s) => s.status !== 'DELIVERED').slice(0, 6);
    box.innerHTML = l.length ? l.map((s) => `<button class="dcard" data-id="${esc(s.id)}"><span><b>${esc(s.batchCode)}</b><small>${esc(s.destination)}</small></span><span class="badge b-${s.status}">${s.status}</span></button>`).join('') : '<div class="empty">Nothing assigned right now.</div>';
    box.onclick = (e) => { const b = e.target.closest('.dcard'); if (!b) return; $('#shipment-id').value = b.dataset.id; detail(l.find((x) => x.id === b.dataset.id)); box.querySelectorAll('.dcard').forEach((x) => x.classList.toggle('sel', x === b)); };
    box.querySelector('.dcard')?.click();
  } catch { box.innerHTML = '<div class="empty">Offline — assigned list unavailable. You can still type a Shipment ID.</div>'; }
}
export function initDriver(mode = 'driver') {
  const clinic = mode === 'clinic';
  $('#d-title').textContent = clinic ? 'Incoming shipments' : 'Assigned deliveries';
  $('#btn-submit').classList.toggle('hidden', clinic); $('#clinic-actions').classList.toggle('hidden', !clinic);
  if (clinic) { $('#handoff-type').value = 'DELIVERY'; $('#handoff-type').disabled = true; } else $('#handoff-type').disabled = false;
  loadAssigned();
  if (!$('#pinpad').children.length) {
    $('#pinpad').innerHTML = [1, 2, 3, 4, 5, 6, 7, 8, 9, '⌫', 0, 'OK'].map((k) => `<button class="pin-key" data-k="${k}">${k}</button>`).join('');
    $('#pinpad').onclick = (e) => { const k = e.target.dataset.k; if (!k) return; if (k === '⌫') pin = pin.slice(0, -1); else if (k !== 'OK' && pin.length < 4) pin += k; dots(); };
  }
  dots();
  pick('.cond-btn', (d) => cond = d.v);
  pick('.vm-btn', (d) => { method = d.m; $('#pin-box').classList.toggle('hidden', method !== 'PIN'); $('#sig-box').classList.toggle('hidden', method !== 'SIGNATURE'); });
  const c = $('#sig'), x = c.getContext('2d'); x.lineWidth = 3; x.lineCap = 'round';
  const pt = (e) => { const r = c.getBoundingClientRect(); return [(e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height]; };
  c.onpointerdown = (e) => { drawing = true; x.beginPath(); x.moveTo(...pt(e)); };
  c.onpointermove = (e) => { if (drawing) { x.lineTo(...pt(e)); x.stroke(); signed = true; } };
  c.onpointerup = () => drawing = false;
  $('#sig-clear').onclick = () => { x.clearRect(0, 0, c.width, c.height); signed = false; };

  $('#btn-scan').onclick = async () => {
    try {
      qr = qr || new Html5Qrcode('qr-reader');
      await qr.start({ facingMode: 'environment' }, { fps: 10, qrbox: 220 }, (t) => { $('#shipment-id').value = t; qr.stop().catch(() => {}); });
    } catch { $('#driver-msg').textContent = 'Camera unavailable — type the ID.'; }
  };
  const submit = async (decision) => {
    const msg = $('#driver-msg'), id = $('#shipment-id').value.trim();
    if (!can('handoff')) return;
    if (!id || !cond || !method) return (msg.textContent = 'Shipment, condition and sign-off are required.');
    if (method === 'PIN' && pin.length !== 4) return (msg.textContent = 'Enter 4-digit PIN.');
    if (method === 'SIGNATURE' && !signed) return (msg.textContent = 'Please sign.');
    $('#btn-submit').disabled = true; msg.textContent = 'Capturing location…';
    const rec = {
      idempotencyKey: crypto.randomUUID(), shipmentId: id, handoffType: $('#handoff-type').value,
      timestamp: new Date().toISOString(), ...(decision ? { decision } : {}), gpsCoordinates: await gps(), conditionStatus: cond, verificationMethod: method,
      verificationData: method === 'PIN' ? await sha(pin + id) : $('#sig').toDataURL('image/png'),
    };
    try {
      if (!navigator.onLine) throw new Error('offline');
      await post(rec); msg.textContent = '✓ Submitted';
    } catch { await db.enqueue(rec); msg.textContent = '⏳ Saved offline — will sync automatically'; }
    pin = ''; dots(); $('#sig-clear').click(); $('#shipment-id').value = ''; $('#btn-submit').disabled = false; updateNet();
  };
  $('#btn-submit').onclick = () => submit(); $('#btn-accept').onclick = () => submit('ACCEPTED'); $('#btn-reject').onclick = () => submit('REJECTED');
}
