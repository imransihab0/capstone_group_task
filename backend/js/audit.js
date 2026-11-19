import { api, esc, toast } from './api.js';
const $ = (s) => document.querySelector(s); let logs = [];
const gpsTxt = (l) => (l.lat != null ? `${(+l.lat).toFixed(4)}, ${(+l.lng).toFixed(4)}` : '—');
function draw(f) {
  const rows = logs.filter((l) => !f || l.type === f);
  $('#audit-body').innerHTML = rows.length ? rows.map((l) => `<tr class="border-t"><td class="p-3">${esc(new Date(l.ts).toLocaleString())}</td><td><span class="chip c-${esc(l.type)}">${esc(l.type.replace('_', ' '))}</span></td><td>${esc(l.shipmentId)}</td><td>${esc(l.detail)}</td><td>${gpsTxt(l)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty">No records.</td></tr>';
}
function chart() { // temperature history with safe band; red dots = out of range
  const pts = logs.filter((l) => l.temp != null).slice().reverse();
  if (!pts.length) return ($('#chart').innerHTML = '<div class="empty">No temperature data.</div>');
  const W = 700, H = 190, x = (i) => 30 + i * (W - 45) / Math.max(1, pts.length - 1), y = (t) => H - 18 - (t / 12) * (H - 34);
  $('#chart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="w-full"><rect x="30" y="${y(8)}" width="${W - 45}" height="${y(2) - y(8)}" fill="#dcfce7"/>
  <polyline fill="none" stroke="#087E8B" stroke-width="2.5" points="${pts.map((p, i) => x(i) + ',' + y(p.temp)).join(' ')}"/>
  ${pts.map((p, i) => (p.temp > 8 || p.temp < 2 ? `<circle cx="${x(i)}" cy="${y(p.temp)}" r="4.5" fill="#dc2626"/>` : '')).join('')}
  <text x="2" y="${y(8) + 4}" font-size="10" fill="#475569">8°C</text><text x="2" y="${y(2) + 4}" font-size="10" fill="#475569">2°C</text></svg>`;
}
export async function initAudit() {
  $('#audit-body').innerHTML = '<tr><td colspan="5">' + Array(8).fill('<div class="skeleton"></div>').join('') + '</td></tr>';
  try { logs = await api('/audit/logs'); } catch (e) { toast(e.message); $('#audit-body').innerHTML = '<tr><td colspan="5" class="empty">Failed to load audit logs.</td></tr>'; return; }
  $('#a-all').textContent = logs.length; $('#a-ho').textContent = logs.filter((l) => l.type === 'HANDOFF').length; $('#a-sla').textContent = logs.filter((l) => l.type === 'SLA_BREACH').length;
  draw(''); chart();
  document.querySelectorAll('.tab').forEach((t) => (t.onclick = () => { document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x === t)); draw(t.dataset.f); }));
  $('#btn-csv').onclick = () => {
    const q = (v) => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const csv = ['timestamp,type,shipmentId,detail,lat,lng', ...logs.map((l) => [l.ts, l.type, l.shipmentId, l.detail, l.lat, l.lng].map(q).join(','))].join('\n');
    Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([csv], { type: 'text/csv' })), download: 'audit-export.csv' }).click();
  };
  $('#btn-pdf').onclick = () => {
    const { jsPDF } = window.jspdf, d = new jsPDF(); let y = 15;
    d.setFontSize(14); d.text('MedSwift Logistics - ColdChain Batch Report', 10, y); y += 8;
    d.setFontSize(9); d.text('Generated: ' + new Date().toISOString(), 10, y); y += 6;
    d.text(`Records: ${logs.length}   SLA breaches: ${logs.filter((l) => l.type === 'SLA_BREACH').length}`, 10, y); y += 8;
    logs.forEach((l) => {
      if (y > 280) { d.addPage(); y = 15; }
      if (l.type === 'SLA_BREACH') d.setTextColor(185, 28, 28); else d.setTextColor(0);
      d.text(`${new Date(l.ts).toISOString()} | ${l.type} | ${l.shipmentId} | ${l.detail} | ${gpsTxt(l)}`.slice(0, 120), 10, y); y += 5;
    });
    d.save('batch-report.pdf');
  };
}
