import { runAll } from './test-suite.js';
import { CONFIG, ITEM_TYPES, api, loadRates, classifyImage, submitScan, requestWithdrawal, fetchWalletSummary,
  newDemoUserId, newIdempotencyKey, announceWalletChange } from './shared.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const waitFor = async (fn, ms = 2000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true; } catch {} await sleep(50); } return false; };

function add(name, ok, err) {
  const d = document.createElement('div'); d.className = 'res ' + (ok ? 'p' : 'f');
  const b = document.createElement('b'); b.textContent = ok ? 'Pass' : 'Fail';
  const span = document.createElement('span'); span.textContent = name;
  if (err) { const s = document.createElement('small'); s.textContent = err; span.appendChild(s); }
  d.append(b, span); $('results').appendChild(d);
}

const openWallet = (host) => new Promise((res, rej) => {
  const f = document.createElement('iframe'); f.src = 'wallet.html';
  f.style.cssText = 'position:absolute;left:-9999px;width:900px;height:700px';
  f.onload = () => res(f); f.onerror = rej; host.appendChild(f);
});

const grayFrame = () => new Promise((resolve) => {
  const c = Object.assign(document.createElement('canvas'), { width: 224, height: 224 });
  const g = c.getContext('2d'); g.fillStyle = '#8a8f8c'; g.fillRect(0, 0, 224, 224);
  c.toBlob(resolve, 'image/jpeg', 0.85);
});

async function integration() {
  const out = [];
  const t = async (name, fn) => { try { await fn(); out.push([name, true]); } catch (e) { out.push([name, false, e.message]); } };
  const must = (c, m) => { if (!c) throw new Error(m); };
  const mustFail = async (fn, code) => {
    try { await fn(); } catch (e) { must(e.code === code, `expected ${code}, got ${e.code}: ${e.message}`); return; }
    throw new Error(`expected ${code}, but the request succeeded`);
  };
  // A throwaway wallet: only rejected scans are made, so no money is ever credited.
  const user = 'test-' + newDemoUserId().slice(5);
  const host = document.body; let a;
  let image, firstTxn;

  await t('Served over http(s) so modules and the camera work', () => must(location.protocol.startsWith('http'), 'Open through start.bat / start.sh, not file://'));
  await t('Camera API available (needs localhost or HTTPS)', () => must(!!navigator.mediaDevices?.getUserMedia, 'getUserMedia unavailable'));
  let online = false;
  await t('Scanner service reachable and model loaded', async () => {
    const h = await api('/health'); online = true;
    must(h.model_ready, `model not loaded: ${h.model_detail}`);
  });
  if (!online) return out;
  await t('Terminal preview uses the server tariff', async () => {
    const r = await api('/rates'); await loadRates();
    for (const [key, item] of Object.entries(ITEM_TYPES)) {
      const expected = r.accepted_classes.includes(key) ? (r.rates_paise_per_gram[key] ?? 0) : 0;
      must(item.ratePaise === expected, `${key}: preview ${item.ratePaise}, server ${expected}`);
    }
    must(CONFIG.MIN_WEIGHT_G === r.min_weight_g && CONFIG.BONUS_PCT === r.bonus_pct, 'weight/bonus rules differ');
  });
  await t('Live preview classifies a camera frame', async () => {
    image = await grayFrame();
    const r = await classifyImage(image);
    must(r.label in ITEM_TYPES, `unknown label ${r.label}`);
    must(r.confidence >= 0 && r.confidence <= 1, 'confidence out of range');
  });
  await t('Under-weight deposit is logged as rejected and pays nothing', async () => {
    const r = await submitScan({ image, grams: 50, key: 'test-k1-' + user, userId: user });
    firstTxn = r.transaction.id;
    must(r.decision === 'rejected', `decision ${r.decision}`);
    must(r.transaction.payout_paise === 0 && r.wallet.balance_paise === 0, 'money moved');
    must(r.wallet.rejected_count === 1, `rejected_count ${r.wallet.rejected_count}`);
  });
  await t('Retry with the same Idempotency-Key returns the original deposit', async () => {
    const r = await submitScan({ image, grams: 50, key: 'test-k1-' + user, userId: user });
    must(r.duplicate === true && r.transaction.id === firstTxn, 'retry created a new transaction');
    must((await fetchWalletSummary(user)).rejectedCount === 1, 'retry was counted twice');
  });
  await t('Reusing a key with different data is refused', () =>
    mustFail(() => submitScan({ image, grams: 60, key: 'test-k1-' + user, userId: user }), 'IDEMPOTENCY_CONFLICT'));
  await t('Withdrawal above the balance is refused', () =>
    mustFail(() => requestWithdrawal({ amountPaise: 100, destination: 'test@upi', key: newIdempotencyKey(), userId: user }), 'INSUFFICIENT_FUNDS'));
  await t('Admin routes are not reachable from the browser', async () => {
    const r = await fetch('/api/admin/withdrawals/1/complete', { method: 'POST' });
    must(r.status === 404, `got HTTP ${r.status}`);
  });
  await t('Ledger database and model files are not served', async () => {
    for (const path of ['/ecorewind_ml/service/data/scanner.db', '/outputs/labels.json', '/serve.py']) {
      const r = await fetch(path);
      must(r.status === 404, `${path} returned HTTP ${r.status}`);
    }
  });
  await t('Wallet tab shows the linked wallet', async () => {
    localStorage.setItem(CONFIG.USER_KEY, user);
    a = await openWallet(host);
    const doc = a.contentDocument;
    must(await waitFor(() => doc.getElementById('userId').textContent === user && doc.getElementById('rej').textContent === '1', 5000),
      `wallet shows ${doc.getElementById('userId').textContent} with ${doc.getElementById('rej').textContent} rejected`);
    must(doc.getElementById('bal').textContent === '₹0.00', 'balance should be ₹0.00');
  });
  if (a) {
    await t('Live sync: a new deposit appears on the wallet without reload', async () => {
      await submitScan({ image, grams: 50, key: newIdempotencyKey(), userId: user });
      announceWalletChange(user);
      must(await waitFor(() => a.contentDocument.getElementById('rej').textContent === '2', 2000),
        'wallet did not update within 2 s');
    });
    a.remove();
  }
  return out;
}

$('run').onclick = async () => {
  $('run').disabled = true; $('results').replaceChildren(); $('summary').textContent = 'Running…';
  const backup = localStorage.getItem(CONFIG.USER_KEY);
  let pass = 0, total = 0;
  try {
    const u = await runAll(add); pass += u.pass; total += u.total;
    for (const [n, ok, e] of await integration()) { add(n, ok, e); total++; if (ok) pass++; }
  } finally {
    backup === null ? localStorage.removeItem(CONFIG.USER_KEY) : localStorage.setItem(CONFIG.USER_KEY, backup);
    announceWalletChange();
    $('run').disabled = false;
  }
  $('summary').textContent = `${pass} of ${total} passed` + (pass === total ? '. Ready for the viva.' : '. Fix the failures above.');
};

$('manual').addEventListener('change', (e) => e.target.closest('li').classList.toggle('done', e.target.checked));
