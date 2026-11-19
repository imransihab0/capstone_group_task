import { session, toast } from './api.js';
import { login, views, guard } from './auth.js';
import { initDispatcher, stopDispatcher } from './dispatcher.js';
import { initDriver, updateNet, sync } from './driver.js';
import { initAudit } from './audit.js';
const $ = (s) => document.querySelector(s);
const ALL = ['login', 'dispatcher', 'driver', 'audit'];
const inits = { dispatcher: initDispatcher, driver: () => initDriver('driver'), clinic: () => initDriver('clinic'), audit: initAudit };
const LABEL = { dispatcher: '📊 Dashboard', driver: '🚚 My Deliveries', clinic: '📦 Receiving', audit: '🧾 Compliance' };

function show(v) {
  stopDispatcher();
  if (v !== 'login' && !guard(v)) return show(views()[0]); // route guard
  ALL.forEach((n) => $('#view-' + n).classList.toggle('hidden', n !== (v === 'clinic' ? 'driver' : v)));
  $('#shell').classList.toggle('hidden', v === 'login'); $('#topsearch').classList.toggle('invisible', v !== 'dispatcher');
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('on', a.getAttribute('href') === '#' + v));
  if (v !== 'login') { try { inits[v](); } catch (e) { fallback(e); } } // error boundary
}
function route() {
  $('#boot')?.remove();
  const s = session.get();
  if (!s) return show('login');
  const v = location.hash.slice(1);
  $('#who').textContent = s.role.replace('_', ' ').toLowerCase(); $('#avatar').textContent = s.role[0];
  $('#nav').innerHTML = views().map((x) => `<a href="#${x}">${LABEL[x]}</a>`).join('');
  show(guard(v) ? v : views()[0]);
}
function fallback(e) {
  console.error(e);
  document.body.insertAdjacentHTML('beforeend', '<div class="fixed inset-0 bg-white p-8 z-[70] text-center"><h2 class="text-xl font-bold">Something went wrong</h2><button class="btn bg-sky-700 text-white mt-4" onclick="location.reload()">Reload</button></div>');
}
window.addEventListener('error', (e) => fallback(e.error));
window.addEventListener('unhandledrejection', (e) => toast('Error: ' + (e.reason?.message || 'unexpected')));

$('#btn-login').onclick = async () => {
  try { await login($('#email').value, $('#password').value); location.hash = ''; route(); }
  catch (e) { $('#login-err').textContent = 'Login failed: ' + e.message; }
};
$('#password').addEventListener('keydown', (e) => e.key === 'Enter' && $('#btn-login').click());
$('#btn-logout').onclick = () => { session.clear(); location.hash = ''; route(); };
window.addEventListener('hashchange', route);
window.addEventListener('online', sync);
window.addEventListener('offline', updateNet);

(async () => {
  if (APP_CONFIG.USE_MOCK) { await import('./mock-api.js'); $('#demo').classList.remove('hidden'); } // DEV ONLY — remove for production
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => { });
  updateNet(); sync(); route();
})();
