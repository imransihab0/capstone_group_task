import { api, session } from './api.js';
export const ACCESS = {
  ADMIN: ['dispatcher', 'driver', 'clinic', 'audit'],
  DISPATCHER: ['dispatcher'],
  DRIVER: ['driver'],
  CLINIC_RECEIVER: ['clinic'],
  AUDITOR: ['audit'],
};
export const can = (action) => {
  const r = session.get()?.role;
  if (action === 'create') return r === 'ADMIN' || r === 'DISPATCHER';
  if (action === 'handoff') return ['ADMIN', 'DRIVER', 'CLINIC_RECEIVER'].includes(r);
  return false;
};
export const views = () => ACCESS[session.get()?.role] || [];
export async function login(email, password) {
  const s = await api('/auth/login', { method: 'POST', body: { email, password } });
  session.set(s); return s;
}
export const guard = (view) => views().includes(view);
