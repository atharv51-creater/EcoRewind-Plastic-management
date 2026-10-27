// EcoRewind shared layer: tariff preview, scanner-service API client, formatting.
// All money is integer PAISE (100 paise = ₹1). The scanner service owns the model,
// the tariff and the wallet ledger; the rules here only drive the live preview and
// are replaced by the server's values from GET /api/rates at startup.

export const CONFIG = {
  API_BASE: '/api',
  USER_KEY: 'rvm_user',          // localStorage: which wallet this browser is linked to
  DEFAULT_USER: 'demo-user',
  MACHINE_ID: 'RVM-01',
  MIN_WEIGHT_G: 100,             // the "100-gram rule"
  MAX_WEIGHT_G: 500,
  BONUS_THRESHOLD_G: 400,        // heavy-load bonus tier
  BONUS_PCT: 10,
  MILESTONE_PAISE: 10000,        // ₹100 eco-reward goal (lifetime earnings)
  CO2_KG_PER_KG_PLASTIC: 1.5,    // rough estimate, for display only
  GRAMS_PER_BOTTLE: 25,
  HISTORY_LIMIT: 200,            // the service returns at most 200 rows per page
  POLL_MS: 5000,
};

// Same shape and defaults as the service's GET /rates (ecorewind_ml/service/config.py).
export const DEFAULT_RATES = {
  rates_paise_per_gram: { plastic_bottle: 2, aluminium_can: 4, plastic_bag: 1 },
  accepted_classes: ['aluminium_can', 'plastic_bag', 'plastic_bottle'],
  min_weight_g: 100, max_weight_g: 500, bonus_threshold_g: 400, bonus_pct: 10,
};

// Keys are the model's class labels. ratePaise = paise per gram; 0 means not paid.
export const ITEM_TYPES = {
  plastic_bottle: { label: 'Plastic bottle', ratePaise: 0 },
  aluminium_can:  { label: 'Aluminium can',  ratePaise: 0 },
  plastic_bag:    { label: 'Plastic bag',    ratePaise: 0 },
  other_invalid:  { label: 'Invalid item',   ratePaise: 0 },
};

const isNum = (n) => typeof n === 'number' && Number.isFinite(n);

/** Applies a GET /rates payload to the preview rules. Throws on a malformed payload. */
export function applyRates(r) {
  const rates = r?.rates_paise_per_gram;
  if (!rates || typeof rates !== 'object' || !Array.isArray(r.accepted_classes)
    || ![r.min_weight_g, r.max_weight_g, r.bonus_threshold_g, r.bonus_pct].every(isNum)) {
    throw new Error('Malformed tariff from server');
  }
  for (const [key, item] of Object.entries(ITEM_TYPES)) {
    const rate = rates[key];
    item.ratePaise = r.accepted_classes.includes(key) && Number.isInteger(rate) && rate > 0 ? rate : 0;
  }
  Object.assign(CONFIG, { MIN_WEIGHT_G: r.min_weight_g, MAX_WEIGHT_G: r.max_weight_g,
    BONUS_THRESHOLD_G: r.bonus_threshold_g, BONUS_PCT: r.bonus_pct });
}
applyRates(DEFAULT_RATES);

/** Pure tariff preview. Order matches the server: item class first, then weight sanity and the 100 g rule. */
export function evaluate(itemKey, grams) {
  const item = ITEM_TYPES[itemKey];
  if (!item || !(item.ratePaise > 0)) {
    return { ok: false, code: 'INVALID_ITEM', title: 'Item not accepted',
      message: `${item ? item.label : 'Unknown object'} is not paid by this machine.` };
  }
  if (!isNum(grams) || grams < 0 || grams > CONFIG.MAX_WEIGHT_G) {
    return { ok: false, code: 'BAD_WEIGHT', title: 'Invalid weight reading',
      message: `The load cell must read between 0 and ${CONFIG.MAX_WEIGHT_G} g. Payout blocked.` };
  }
  if (grams < CONFIG.MIN_WEIGHT_G) {
    return { ok: false, code: 'LOW_DENSITY', title: 'Low-density anomaly',
      message: `${grams} g is under the ${CONFIG.MIN_WEIGHT_G} g minimum. Payout blocked.` };
  }
  const base = Math.round(grams * item.ratePaise);
  const bonus = grams >= CONFIG.BONUS_THRESHOLD_G ? Math.round(base * CONFIG.BONUS_PCT / 100) : 0;
  return { ok: true, code: 'OK', basePaise: base, bonusPaise: bonus, paise: base + bonus,
    title: 'Deposit accepted', message: `${item.label}, ${grams} g` };
}

export const formatINR = (paise) =>
  '₹' + (paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** "12.5" -> 1250 paise. Returns null for anything that is not a positive amount with at most 2 decimals. */
export function parseRupees(text) {
  const m = /^\s*(\d{1,6})(?:\.(\d{1,2}))?\s*$/.exec(String(text ?? ''));   // service caps at ₹10,00,000
  if (!m) return null;
  const paise = Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0'));
  return paise > 0 ? paise : null;
}

/** Quotes a CSV cell and neutralises spreadsheet formula injection. */
export const csvCell = (value) => {
  let s = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replace(/"/g, '""') + '"';
};

export const formatDateTime = (ts) =>
  new Date(ts).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });

/* ---------- linked wallet (which user this browser deposits for) ---------- */
export const USER_ID_RE = /^[A-Za-z0-9_.@-]{1,64}$/;   // same rule as the service

export function getUserId() {
  try {
    const id = localStorage.getItem(CONFIG.USER_KEY);
    if (id && USER_ID_RE.test(id)) return id;
  } catch { /* storage blocked: fall back to the shared demo wallet */ }
  return CONFIG.DEFAULT_USER;
}

export function setUserId(id) {
  if (!USER_ID_RE.test(id)) throw new Error('Wallet ID must be 1-64 letters, digits, . _ @ or -');
  try { localStorage.setItem(CONFIG.USER_KEY, id); } catch { /* ignore */ }
  announceWalletChange(id);
}

function randomHex(bytes) {
  const a = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
}

export const newDemoUserId = () => 'demo-' + randomHex(4);

/** One key per deposit/withdrawal attempt; reusing it on retry makes the server return the original result. */
export const newIdempotencyKey = () => globalThis.crypto.randomUUID?.() ?? randomHex(16);

/* ---------- API client ---------- */
export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** Reads {code, message} from service errors ({detail}) and proxy/validation errors ({error}). */
export function parseApiError(status, body) {
  const e = body?.detail ?? body?.error;
  if (e && typeof e === 'object' && !Array.isArray(e) && e.message) {
    return new ApiError(status, e.code || `HTTP_${status}`, e.message);
  }
  if (typeof e === 'string') return new ApiError(status, `HTTP_${status}`, e);
  return new ApiError(status, `HTTP_${status}`, `Request failed (HTTP ${status})`);
}

export const isOffline = (e) => e?.code === 'NETWORK' || e?.code === 'SERVICE_OFFLINE'
  || e?.code === 'MODEL_UNAVAILABLE';

export async function api(path, { method = 'GET', body, headers, timeoutMs = 15000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res, data;
  try {
    res = await fetch(CONFIG.API_BASE + path, { method, body, headers, signal: ctrl.signal });
    data = await res.json().catch(() => null);
  } catch (e) {
    throw new ApiError(0, 'NETWORK', e?.name === 'AbortError'
      ? 'The EcoRewind server took too long to answer' : 'Cannot reach the EcoRewind server');
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw parseApiError(res.status, data);
  return data;
}

const userPath = (userId) => `/users/${encodeURIComponent(userId)}`;

export async function loadRates() {
  applyRates(await api('/rates'));
}

/** Live preview for one camera frame. Never touches the ledger. */
export function classifyImage(image) {
  const form = new FormData();
  form.append('image', image, 'frame.jpg');
  return api('/classify', { method: 'POST', body: form, timeoutMs: 8000 });
}

/** The real deposit: the server re-classifies the frame, recomputes the payout and writes the ledger. */
export function submitScan({ image, grams, key, userId = getUserId() }) {
  const form = new FormData();
  form.append('image', image, 'frame.jpg');
  form.append('user_id', userId);
  form.append('weight_grams', String(grams));
  form.append('machine_id', CONFIG.MACHINE_ID);
  return api('/scan', { method: 'POST', body: form, headers: { 'Idempotency-Key': key }, timeoutMs: 20000 });
}

export function requestWithdrawal({ amountPaise, destination, key, userId = getUserId() }) {
  return api(`${userPath(userId)}/withdrawals`, {
    method: 'POST',
    body: JSON.stringify({ amount_paise: amountPaise, destination }),
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
  });
}

/** Retries once on a network failure with the same request, so the idempotency key prevents a double write. */
export async function withRetry(fn) {
  try { return await fn(); } catch (e) {
    if (e?.code !== 'NETWORK') throw e;
    return fn();
  }
}

export const normalizeWallet = (w) => ({
  userId: w.user_id, balancePaise: w.balance_paise, totalGrams: w.total_grams,
  confirmedCount: w.confirmed_count, rejectedCount: w.rejected_count, totalPaidPaise: w.total_paid_paise,
});

export const normalizeTransaction = (t) => ({
  id: t.id, ts: Date.parse(t.created_at), status: t.status,
  label: t.display_label || t.label || 'Unknown item', grams: t.weight_grams,
  paise: t.payout_paise, bonusPaise: t.bonus_paise, reason: t.reason || '', confidence: t.confidence,
});

export async function fetchWalletSummary(userId = getUserId()) {
  return normalizeWallet(await api(`${userPath(userId)}/wallet`));
}

/** Balance, counters and the latest transactions for one user. */
export async function fetchWallet(userId = getUserId()) {
  const [summary, page] = await Promise.all([
    fetchWalletSummary(userId),
    api(`${userPath(userId)}/transactions?limit=${CONFIG.HISTORY_LIMIT}`),
  ]);
  return { ...summary, totalTransactions: page.total, transactions: page.items.map(normalizeTransaction) };
}

export async function fetchWithdrawals(userId = getUserId()) {
  const page = await api(`${userPath(userId)}/withdrawals?limit=50`);
  return page.items.map((w) => ({ id: w.id, ts: Date.parse(w.created_at), amountPaise: w.amount_paise,
    destination: w.destination, status: w.status }));
}

/* ---------- live sync between tabs ---------- */
let channel;
const getChannel = () => {
  if (channel === undefined) channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('ecorewind') : null;
  return channel;
};

/** Tells other tabs of this origin to refetch. The server stays the source of truth. */
export function announceWalletChange(userId = getUserId()) {
  getChannel()?.postMessage({ type: 'wallet', userId });
}

/**
 * Calls cb when another tab changes the linked wallet or switches user, plus a slow
 * poll while the page is visible for changes made elsewhere (other machines, an
 * operator paying or cancelling a withdrawal).
 */
export function onWalletChange(cb) {
  getChannel()?.addEventListener('message', (e) => {
    if (e.data?.type === 'wallet' && e.data.userId === getUserId()) cb();
  });
  window.addEventListener('storage', (e) => { if (e.key === CONFIG.USER_KEY || e.key === null) cb(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) cb(); });
  setInterval(() => { if (!document.hidden) cb(); }, CONFIG.POLL_MS);
}

export function ecoStats(w) {
  const grams = isNum(w?.totalGrams) ? w.totalGrams : 0;
  const kg = grams / 1000;
  const earned = isNum(w?.totalPaidPaise) ? w.totalPaidPaise : 0;
  return { kg, co2: kg * CONFIG.CO2_KG_PER_KG_PLASTIC,
    items: Number.isInteger(w?.confirmedCount) ? w.confirmedCount : 0,
    bottles: Math.round(grams / CONFIG.GRAMS_PER_BOTTLE),
    rejected: Number.isInteger(w?.rejectedCount) ? w.rejectedCount : 0,
    milestonePct: Math.min(100, (earned / CONFIG.MILESTONE_PAISE) * 100) };
}
