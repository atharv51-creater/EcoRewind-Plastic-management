import { CONFIG, formatINR, formatDateTime, onWalletChange, ecoStats, csvCell, fetchWallet, fetchWithdrawals,
  getUserId, setUserId, newDemoUserId, USER_ID_RE, parseRupees, requestWithdrawal, newIdempotencyKey, withRetry,
  announceWalletChange } from './shared.js';

const $ = (id) => document.getElementById(id);
let wallet = null;
let lastTopId = null;      // newest txn id already shown; null means "first load for this user"
let shownUser = null;
let refreshing = null;
let queued = false;

function render(w) {
  const announce = lastTopId !== null;
  const s = ecoStats(w);
  $('userId').textContent = w.userId;
  $('bal').textContent = formatINR(w.balancePaise);
  $('kg').textContent = s.kg.toFixed(2);
  $('co2').textContent = s.co2.toFixed(2);
  $('bottles').textContent = s.bottles;
  $('rej').textContent = s.rejected;
  $('mileFill').style.width = s.milestonePct + '%';
  $('mileBar').setAttribute('aria-valuenow', Math.round(s.milestonePct));
  const left = Math.max(0, CONFIG.MILESTONE_PAISE - w.totalPaidPaise);
  $('mileTxt').textContent = left ? `${formatINR(left)} more lifetime earnings to reach the ${formatINR(CONFIG.MILESTONE_PAISE)} eco reward` : 'Eco reward unlocked';

  const f = $('filter').value;
  const list = w.transactions.filter(t => f === 'all' || t.status === f);
  $('empty').hidden = list.length > 0;
  $('histNote').hidden = w.totalTransactions <= w.transactions.length;
  $('histNote').textContent = `Showing the latest ${w.transactions.length} of ${w.totalTransactions} transactions.`;
  const tb = $('rows'); tb.replaceChildren();
  list.forEach(t => {
    const tr = document.createElement('tr');
    if (announce && t.id > lastTopId) tr.className = 'fresh';
    const ok = t.status === 'accepted';
    const cells = [
      ['#' + t.id], [formatDateTime(t.ts)], [t.label], [t.grams + ' g', 'r'],
      [null], [ok ? '+' + formatINR(t.paise) : '₹0.00', 'r']];
    cells.forEach(([txt, cls], i) => {
      const td = document.createElement('td'); if (cls) td.className = cls;
      if (i === 4) { const sp = document.createElement('span'); sp.className = 'tag ' + (ok ? 'ok' : 'no');
        sp.textContent = ok ? 'Confirmed by AI validation' : (t.reason || 'Rejected'); td.appendChild(sp); }
      else td.textContent = txt;
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });

  const top = w.transactions[0];
  if (announce && top && top.id > lastTopId) {
    toast(top.status === 'accepted' ? `Deposit confirmed: +${formatINR(top.paise)}` : `Deposit rejected: ${top.reason}`);
  }
  lastTopId = top?.id ?? 0;
}

const WD_STATUS = { pending: ['wait', 'Pending'], paid: ['ok', 'Paid'], cancelled: ['no', 'Cancelled, refunded'] };

function renderWithdrawals(items) {
  $('wdEmpty').hidden = items.length > 0;
  const tb = $('wdRows'); tb.replaceChildren();
  items.forEach(w => {
    const tr = document.createElement('tr');
    const [cls, text] = WD_STATUS[w.status] || ['no', w.status];
    [['#' + w.id], [formatDateTime(w.ts)], [w.destination], [null], [formatINR(w.amountPaise), 'r']].forEach(([txt, c], i) => {
      const td = document.createElement('td'); if (c) td.className = c;
      if (i === 3) { const sp = document.createElement('span'); sp.className = 'tag ' + cls; sp.textContent = text; td.appendChild(sp); }
      else td.textContent = txt;
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
}

/** Refetches from the server. Calls that arrive mid-refresh are folded into one follow-up refresh. */
async function refresh() {
  if (refreshing) { queued = true; return refreshing; }
  refreshing = (async () => {
    const userId = getUserId();
    if (userId !== shownUser) { shownUser = userId; lastTopId = null; }
    try {
      const [w, wds] = await Promise.all([fetchWallet(userId), fetchWithdrawals(userId)]);
      if (userId !== getUserId()) return;   // user switched mid-flight; the queued refresh redraws
      wallet = w;
      render(w);
      renderWithdrawals(wds);
      $('syncTxt').textContent = 'Synced ' + new Date().toLocaleTimeString('en-IN');
    } catch (e) {
      $('syncTxt').textContent = `Server unreachable: ${e.message}`;
    }
  })();
  try { await refreshing; } finally {
    refreshing = null;
    if (queued) { queued = false; refresh(); }
  }
}

let tt;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 3200);
}

function showWdAlert(kind, title, message = '') {
  const box = $('wdAlert'); box.replaceChildren();
  if (!title) return;
  const d = document.createElement('div'); d.className = 'status ' + kind; d.style.marginBottom = '.8rem';
  const b = document.createElement('strong'); b.textContent = title; d.appendChild(b);
  if (message) d.appendChild(document.createTextNode(message));
  box.appendChild(d);
}

$('filter').onchange = () => { if (wallet) render(wallet); };

$('reset').onclick = () => {
  if (!confirm('Start a new, empty demo wallet? The current wallet stays on the server under its ID.')) return;
  setUserId(newDemoUserId());
  refresh();
};

$('switchUser').onclick = () => {
  const id = prompt('Wallet ID to link this browser to (letters, digits, . _ @ -):', getUserId());
  if (id === null) return;
  if (!USER_ID_RE.test(id.trim())) { toast('That wallet ID is not valid.'); return; }
  setUserId(id.trim());
  refresh();
};

$('csv').onclick = () => {
  if (!wallet) return;
  const rows = [['Txn', 'Time', 'Item', 'Grams', 'Status', 'Paise', 'Reason']];
  wallet.transactions.forEach(t => rows.push([t.id, new Date(t.ts).toISOString(), t.label, t.grams, t.status, t.paise, t.reason]));
  const csv = rows.map(r => r.map(csvCell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: `ecorewind_${wallet.userId}_transactions.csv` });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

$('wdForm').onsubmit = async (e) => {
  e.preventDefault();
  const amountPaise = parseRupees($('wdAmount').value);
  const destination = $('wdDest').value.trim();
  if (amountPaise === null) return showWdAlert('err', 'Enter an amount', ' in rupees, for example 50 or 12.50.');
  if (!/^[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9.-]{1,}$/.test(destination)) {
    return showWdAlert('err', 'Enter a UPI ID', ' such as name@bank.');
  }
  if (wallet && amountPaise > wallet.balancePaise) {
    return showWdAlert('err', 'Not enough balance', ` You can withdraw up to ${formatINR(wallet.balancePaise)}.`);
  }
  const key = newIdempotencyKey();
  $('wdBtn').disabled = true;
  showWdAlert('', '');
  try {
    const res = await withRetry(() => requestWithdrawal({ amountPaise, destination, key }));
    showWdAlert('ok', 'Withdrawal requested.', ` ${formatINR(res.withdrawal.amount_paise)} to ${res.withdrawal.destination} is pending (request #${res.withdrawal.id}).`);
    $('wdAmount').value = '';
    announceWalletChange();
    await refresh();
  } catch (err) {
    showWdAlert('err', 'Withdrawal failed.', ' ' + err.message);
  } finally {
    $('wdBtn').disabled = false;
  }
};

onWalletChange(refresh);   // terminal deposits, user switches in other tabs, and a slow poll
refresh();
