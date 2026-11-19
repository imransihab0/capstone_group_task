import { api, esc, toast } from './api.js';
import { can } from './auth.js';
const ROW = 44; let map, layer, all = [], list = [], timer, alerted = new Set(), boxEl, inner;
const $ = (s) => document.querySelector(s);

function beep() { try { const c = new AudioContext(), o = c.createOscillator(); o.frequency.value = 880; o.connect(c.destination); o.start(); setTimeout(() => { o.stop(); c.close(); }, 400); } catch {} }

function row(s, y) {
  return `<div class="row" style="top:${y}px"><b>${esc(s.batchCode)}</b><span>${esc(s.vehicleId)}</span>
  <span class="t-${s.status}">${(+s.currentTemp).toFixed(1)}°C</span><span>${(+s.currentHumidity).toFixed(0)}%</span>
  <span class="badge b-${s.status}">${s.status}</span><span>${new Date(s.eta).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></div>`;
}
function render() { // list virtualization: only visible rows exist in DOM
  const top = boxEl.scrollTop, h = boxEl.clientHeight;
  const s = Math.max(0, Math.floor(top / ROW) - 5), e = Math.min(list.length, Math.ceil((top + h) / ROW) + 5);
  inner.style.height = list.length * ROW + 'px';
  inner.innerHTML = list.length ? list.slice(s, e).map((x, i) => row(x, (s + i) * ROW)).join('') : '<div class="empty">No shipments match.</div>';
  $('#count').textContent = list.length + ' shipments';
}
function applyFilter() {
  const q = $('#search').value.trim().toLowerCase(), st = $('#filter').value;
  list = all.filter((s) => (!st || s.status === st) && (!q || s.id.toLowerCase().includes(q) || s.batchCode.toLowerCase().includes(q)));
  render();
}
function alerts() {
  const crit = all.filter((s) => s.status === 'CRITICAL' && s.breachDurationMinutes > 10);
  const b = $('#critical-banner');
  if (crit.length) {
    b.textContent = `⚠ CRITICAL: ${crit.length} shipment(s) in breach > 10 min — ${crit.slice(0, 3).map((s) => s.batchCode).join(', ')}`;
    b.classList.remove('hidden');
    if (crit.some((s) => !alerted.has(s.id))) beep();
    crit.forEach((s) => alerted.add(s.id));
  } else b.classList.add('hidden');
  all.filter((s) => s.status === 'WARNING' && !alerted.has('w' + s.id)).slice(0, 3)
    .forEach((s) => { alerted.add('w' + s.id); toast(`Warning: ${s.batchCode} at ${(+s.currentTemp).toFixed(1)}°C nearing limit`); });
}
function kpis() {
  const n = (st) => all.filter((s) => s.status === st).length;
  $('#k-total').textContent = all.filter((s) => s.status !== 'DELIVERED').length; $('#k-normal').textContent = n('DELIVERED');
  $('#k-warn').textContent = n('WARNING'); $('#k-crit').textContent = n('CRITICAL');
}
function panels() {
  const hot = all.filter((s) => s.status === 'CRITICAL' || s.status === 'WARNING').sort((a, b) => (b.status === 'CRITICAL') - (a.status === 'CRITICAL') || b.breachDurationMinutes - a.breachDurationMinutes);
  $('#bell-n').textContent = hot.length;
  $('#alerts').innerHTML = hot.length ? hot.slice(0, 6).map((s) => `<div class="alert a-${s.status}"><b>${s.status === 'CRITICAL' ? '🚨 CRITICAL' : '⚠ Warning'} · ${esc(s.batchCode)}</b><span>${(+s.currentTemp).toFixed(1)}°C · ${esc(s.vehicleId)}${s.breachDurationMinutes ? ' · ' + s.breachDurationMinutes + ' min' : ''}</span></div>`).join('') : '<div class="empty">All shipments within 2–8°C ✓</div>';
  $('#tcards').innerHTML = hot.slice(0, 2).concat(all.filter((s) => s.status === 'NORMAL')).slice(0, 4).map((s) => `<div class="tcard"><span class="badge b-${s.status}">${s.status}</span><b class="t-${s.status}">${(+s.currentTemp).toFixed(1)}°C</b><small>${esc(s.batchCode)} · ${esc(s.vehicleId)} · ${(+s.currentHumidity).toFixed(0)}% RH</small></div>`).join('');
}
function drawMap() { // needs optional lat/lng on each shipment from the API
  if (!window.L) return;
  if (!map) { map = L.map('map').setView([23.8, 90.4], 7); L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map); layer = L.layerGroup().addTo(map); }
  layer.clearLayers(); const col = { NORMAL: '#16a34a', WARNING: '#f59e0b', CRITICAL: '#dc2626', DELIVERED: '#64748b' };
  all.filter((s) => s.lat != null).slice(0, 200).forEach((s) => L.circleMarker([s.lat, s.lng], { radius: s.status === 'CRITICAL' ? 8 : 5, color: '#fff', weight: 1, fillColor: col[s.status], fillOpacity: 0.95 }).bindPopup(`${esc(s.batchCode)} · ${(+s.currentTemp).toFixed(1)}°C`).addTo(layer));
  setTimeout(() => map.invalidateSize(), 60);
}
async function load(first) {
  try {
    if (first) inner.innerHTML = Array(10).fill('<div class="skeleton"></div>').join('');
    all = await api('/shipments'); kpis(); panels(); drawMap(); applyFilter(); alerts();
  } catch (e) { toast('Could not load shipments: ' + e.message); if (first) inner.innerHTML = '<div class="empty">Failed to load. Retrying…</div>'; }
}
export function initDispatcher() {
  boxEl = $('#grid'); inner = $('#grid-inner');
  boxEl.addEventListener('scroll', () => requestAnimationFrame(render));
  $('#search').addEventListener('input', applyFilter); $('#filter').addEventListener('change', applyFilter);
  document.querySelectorAll('[data-requires="create"]').forEach((el) => el.classList.toggle('hidden', !can('create')));
  const m = $('#modal');
  $('#btn-new').onclick = () => m.showModal(); $('#ship-cancel').onclick = () => m.close();
  $('#ship-form').onsubmit = async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    f.minTemp = +f.minTemp; f.maxTemp = +f.maxTemp;
    if (f.minTemp >= f.maxTemp) return ($('#ship-err').textContent = 'Min must be less than Max');
    try { await api('/shipments', { method: 'POST', body: f }); m.close(); e.target.reset(); toast('Shipment created'); load(); }
    catch (err) { $('#ship-err').textContent = err.message; }
  };
  load(true); clearInterval(timer); timer = setInterval(load, APP_CONFIG.POLL_MS);
}
export const stopDispatcher = () => clearInterval(timer);
