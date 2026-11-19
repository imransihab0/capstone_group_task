// Minimal IndexedDB offline queue (keyPath = idempotencyKey => no duplicates)
const open = () => new Promise((res, rej) => {
  const r = indexedDB.open('coldchain', 1);
  r.onupgradeneeded = () => { const s = r.result.createObjectStore('queue', { keyPath: 'idempotencyKey' }); s.createIndex('ts', 'timestamp'); };
  r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
});
const tx = async (mode, fn) => {
  const db = await open();
  return new Promise((res, rej) => { const t = db.transaction('queue', mode); const out = fn(t.objectStore('queue')); t.oncomplete = () => res(out.result); t.onerror = () => rej(t.error); });
};
export const enqueue = (rec) => tx('readwrite', (s) => s.put(rec));
export const remove = (k) => tx('readwrite', (s) => s.delete(k));
export const count = () => tx('readonly', (s) => s.count());
export const all = async () => (await tx('readonly', (s) => s.getAll())).sort((a, b) => a.timestamp.localeCompare(b.timestamp));

export async function flush(post) {
  for (const rec of await all()) {
    try { await post(rec); await remove(rec.idempotencyKey); } catch { break; } // stop, keep order, retry later
  }
}
