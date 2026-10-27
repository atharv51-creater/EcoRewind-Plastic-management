// Shared unit tests for the pure logic: run in the browser (tests.html) and in Node (node tests/run.mjs).
// The ledger itself lives in the scanner service and is tested by ecorewind_ml/service/tests.
import { CONFIG, DEFAULT_RATES, ITEM_TYPES, applyRates, evaluate, formatINR, csvCell, parseRupees, parseApiError,
  ecoStats, getUserId, setUserId, normalizeTransaction } from './shared.js';

const eq = (a, b, m = '') => { if (a !== b) throw new Error(`${m} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); };
const throws = (fn, m) => { let threw = false; try { fn(); } catch { threw = true; } eq(threw, true, m); };
// Every test starts from the built-in tariff, even if the page loaded different server rates.
const withDefaults = (fn) => () => { applyRates(DEFAULT_RATES); return fn(); };

export const tests = [
  ['250 g plastic bottle pays ₹5.00 (500 paise)', () => { const r = evaluate('plastic_bottle', 250); eq(r.ok, true); eq(r.paise, 500); }],
  ['99 g bottle is rejected as low-density', () => { const r = evaluate('plastic_bottle', 99); eq(r.ok, false); eq(r.code, 'LOW_DENSITY'); }],
  ['100 g bottle is accepted (boundary)', () => { const r = evaluate('plastic_bottle', 100); eq(r.ok, true); eq(r.paise, 200); }],
  ['0 g bottle is rejected', () => eq(evaluate('plastic_bottle', 0).ok, false)],
  ['NaN weight is rejected', () => { const r = evaluate('plastic_bottle', NaN); eq(r.ok, false); eq(r.code, 'BAD_WEIGHT'); }],
  ['Weight above 500 g is rejected', () => { const r = evaluate('plastic_bottle', 501); eq(r.ok, false); eq(r.code, 'BAD_WEIGHT'); }],
  ['Invalid item at 300 g is blocked', () => { const r = evaluate('other_invalid', 300); eq(r.ok, false); eq(r.code, 'INVALID_ITEM'); }],
  ['Item check runs before the weight rule (invalid 50 g)', () => eq(evaluate('other_invalid', 50).code, 'INVALID_ITEM')],
  ['Unknown item key is blocked', () => eq(evaluate('???', 300).ok, false)],
  ['No bonus at 399 g', () => eq(evaluate('plastic_bottle', 399).bonusPaise, 0)],
  ['10% bonus at 400 g (800 + 80)', () => { const r = evaluate('plastic_bottle', 400); eq(r.paise, 880); eq(r.bonusPaise, 80); }],
  ['Aluminium can 140 g = 560 paise', () => eq(evaluate('aluminium_can', 140).paise, 560)],
  ['Plastic bag 140 g = 140 paise', () => eq(evaluate('plastic_bag', 140).paise, 140)],
  ['Server tariff replaces the preview rules', () => {
    applyRates({ ...DEFAULT_RATES, rates_paise_per_gram: { plastic_bottle: 3, aluminium_can: 4, plastic_bag: 1 },
      accepted_classes: ['plastic_bottle', 'aluminium_can'], min_weight_g: 150, bonus_pct: 20 });
    eq(evaluate('plastic_bottle', 200).paise, 600);
    eq(evaluate('plastic_bag', 200).code, 'INVALID_ITEM', 'class not in accepted_classes');
    eq(evaluate('plastic_bottle', 120).code, 'LOW_DENSITY');
    eq(evaluate('plastic_bottle', 400).bonusPaise, 240);
    eq(CONFIG.MIN_WEIGHT_G, 150);
  }],
  ['Malformed server tariff is refused and leaves rules unchanged', () => {
    throws(() => applyRates({ rates_paise_per_gram: { plastic_bottle: 99 } }), 'malformed tariff');
    eq(ITEM_TYPES.plastic_bottle.ratePaise, 2);
  }],
  ['formatINR formats paise correctly', () => { eq(formatINR(4550), '₹45.50'); eq(formatINR(0), '₹0.00'); eq(formatINR(100000), '₹1,000.00'); }],
  ['CSV cells are quoted and formula-safe', () => {
    eq(csvCell('=1+1'), '"\'=1+1"'); eq(csvCell('a"b'), '"a""b"'); eq(csvCell(null), '""');
  }],
  ['Rupee amounts parse to whole paise', () => {
    eq(parseRupees('5'), 500); eq(parseRupees('12.5'), 1250); eq(parseRupees(' 0.01 '), 1); eq(parseRupees('12.50'), 1250);
    eq(parseRupees('0'), null); eq(parseRupees('1.234'), null); eq(parseRupees('-1'), null); eq(parseRupees('1e3'), null);
    eq(parseRupees(''), null); eq(parseRupees('1000000'), null);
  }],
  ['API errors are read from service and proxy responses', () => {
    let e = parseApiError(409, { detail: { code: 'INSUFFICIENT_FUNDS', message: 'Available balance is 0 paise' } });
    eq(e.code, 'INSUFFICIENT_FUNDS'); eq(e.status, 409); eq(e.message, 'Available balance is 0 paise');
    e = parseApiError(503, { error: { code: 'SERVICE_OFFLINE', message: 'down' } }); eq(e.code, 'SERVICE_OFFLINE');
    e = parseApiError(404, { detail: 'Not Found' }); eq(e.code, 'HTTP_404'); eq(e.message, 'Not Found');
    e = parseApiError(502, null); eq(e.code, 'HTTP_502');
  }],
  ['Server transactions map to the wallet table format', () => {
    const t = normalizeTransaction({ id: 7, created_at: '2026-10-28T10:00:00+00:00', status: 'accepted', label: 'plastic_bottle',
      display_label: 'Plastic bottle', weight_grams: 250, payout_paise: 500, bonus_paise: 0, reason: null, confidence: 0.9 });
    eq(t.id, 7); eq(t.label, 'Plastic bottle'); eq(t.grams, 250); eq(t.paise, 500); eq(t.reason, '');
    eq(t.ts, Date.UTC(2026, 9, 28, 10));
  }],
  ['Eco stats: kg, rejected count, milestone uses lifetime earnings', () => {
    const s = ecoStats({ totalGrams: 500, confirmedCount: 1, rejectedCount: 1, balancePaise: 0, totalPaidPaise: 5000 });
    eq(s.kg, 0.5); eq(s.rejected, 1); eq(s.items, 1); eq(s.bottles, 20); eq(s.milestonePct, 50);
    eq(ecoStats({ totalPaidPaise: 999999 }).milestonePct, 100);
    eq(ecoStats(null).kg, 0);
  }],
  ['Linked wallet ID defaults, persists and rejects bad IDs', () => {
    localStorage.removeItem(CONFIG.USER_KEY); eq(getUserId(), CONFIG.DEFAULT_USER);
    setUserId('demo-abc123'); eq(getUserId(), 'demo-abc123');
    throws(() => setUserId('bad id/../x'), 'invalid id'); eq(getUserId(), 'demo-abc123');
    localStorage.setItem(CONFIG.USER_KEY, '<script>'); eq(getUserId(), CONFIG.DEFAULT_USER);
    localStorage.removeItem(CONFIG.USER_KEY);
  }],
].map(([name, fn]) => [name, withDefaults(fn)]);

export async function runAll(onResult) {
  let pass = 0;
  try {
    for (const [name, fn] of tests) {
      try { await fn(); pass++; onResult(name, true); } catch (e) { onResult(name, false, e.message); }
    }
  } finally {
    applyRates(DEFAULT_RATES);
  }
  return { pass, total: tests.length };
}
