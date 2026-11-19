export const session = {
  get: () => JSON.parse(sessionStorage.getItem('cc_session') || 'null'),
  set: (s) => sessionStorage.setItem('cc_session', JSON.stringify(s)),
  clear: () => sessionStorage.removeItem('cc_session'),
};
export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const s = session.get();
  const res = await fetch(APP_CONFIG.API_BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(s ? { Authorization: 'Bearer ' + s.token } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { session.clear(); location.reload(); }
  if (!res.ok) throw new Error((await res.text().catch(() => '')) || 'Request failed (' + res.status + ')');
  return res.json();
}
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export function toast(msg, ms = 6000) {
  const d = document.createElement('div'); d.className = 'toast'; d.textContent = msg;
  document.getElementById('toasts').appendChild(d); setTimeout(() => d.remove(), ms);
}
