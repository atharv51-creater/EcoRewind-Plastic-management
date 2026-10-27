// Run with: node tests/run.mjs
const store = new Map();
globalThis.localStorage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
const { runAll } = await import('../js/test-suite.js');
const { pass, total } = await runAll((n, ok, err) => console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${err ? '  -> ' + err : ''}`));
console.log(`\n${pass}/${total} passed`);
process.exit(pass === total ? 0 : 1);
