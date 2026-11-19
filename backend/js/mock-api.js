// DEV ONLY. Delete this file and set USE_MOCK:false before production.
const r = (a, b) => +(a + Math.random() * (b - a)).toFixed(1);
const ships = Array.from({ length: 600 }, (_, i) => ({ id: 'S' + i, batchCode: 'B-' + (1000 + i), vehicleId: 'VAN-' + (i % 80), origin: 'Central WH', destination: 'Clinic ' + (i % 120), minTemp: 2, maxTemp: 8, lat: 22.5 + ((i * 37) % 30) / 10, lng: 89.5 + ((i * 53) % 25) / 10, currentTemp: 5, currentHumidity: 55, status: 'NORMAL', breachDurationMinutes: 0, eta: new Date(Date.now() + 3.6e6 * (1 + i % 5)).toISOString() }));
const audit = Array.from({ length: 60 }, (_, i) => ({ ts: new Date(Date.now() - i * 6e5).toISOString(), type: ['TEMP', 'HANDOFF', 'SLA_BREACH'][i % 3], shipmentId: 'S' + i, temp: +(5 + Math.sin(i / 2) * 3.6 + (i % 7 === 0 ? 2 : 0)).toFixed(1), detail: i % 3 === 2 ? 'Temp 9.4C for 12 min' : i % 3 ? 'Pickup INTACT' : 'Temp 5.1C', lat: 23.78 + i / 1000, lng: 90.4 + i / 1000 }));
const real = window.fetch.bind(window);
window.fetch = async (url, o = {}) => {
  if (!String(url).startsWith(APP_CONFIG.API_BASE)) return real(url, o);
  const p = url.slice(APP_CONFIG.API_BASE.length).split('?')[0], body = o.body ? JSON.parse(o.body) : {};
  const ok = (d) => new Response(JSON.stringify(d), { status: 200 });
  await new Promise((s) => setTimeout(s, 300));
  if (p === '/auth/login') { const role = { admin: 'ADMIN', dispatcher: 'DISPATCHER', driver: 'DRIVER', clinic: 'CLINIC_RECEIVER', auditor: 'AUDITOR' }[body.email.split('@')[0]]; return role ? ok({ token: 'dev', role }) : new Response('Use e.g. driver@x.com', { status: 400 }); }
  if (p === '/shipments' && o.method === 'POST') { ships.unshift({ ...ships[0], ...body, id: 'S' + Date.now(), currentTemp: 5, status: 'NORMAL' }); return ok({}); }
  if (p === '/shipments') { ships.forEach((s, i) => { if (i % 8 === 0) { s.status = 'DELIVERED'; s.breachDurationMinutes = 0; return; } const t = r(1, 10); s.currentTemp = t; s.status = t > s.maxTemp || t < s.minTemp ? (i % 9 ? 'WARNING' : 'CRITICAL') : 'NORMAL'; s.breachDurationMinutes = s.status === 'CRITICAL' ? 12 : 0; }); return ok(ships); }
  if (p === '/handoffs') return ok({ ok: true });
  if (p === '/audit/logs') return ok(audit);
  return new Response('Not found', { status: 404 });
};
