import { api, fetchWalletSummary, formatINR, getUserId, setUserId, newDemoUserId, onWalletChange } from './shared.js';

async function state() {
  const out = document.getElementById('state');
  try {
    const [health, w] = await Promise.all([api('/health'), fetchWalletSummary()]);
    out.textContent = `Server ${health.model_ready ? 'online, model ready' : 'online, model NOT loaded'} · Wallet ${getUserId()}: `
      + `${formatINR(w.balancePaise)}, ${w.confirmedCount} accepted, ${w.rejectedCount} rejected`;
  } catch (e) {
    out.textContent = `Server unreachable: ${e.message}`;
  }
}

document.getElementById('reset').onclick = () => {
  if (!confirm('Start a new, empty demo wallet? The current one stays on the server under its ID.')) return;
  setUserId(newDemoUserId());
  state();
};
onWalletChange(state);
document.getElementById('demo').addEventListener('change', (e) => e.target.closest('li').classList.toggle('done', e.target.checked));
state();
