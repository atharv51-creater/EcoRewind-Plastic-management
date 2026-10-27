# EcoRewind: Reverse Vending Machine Simulation (all phases)

## Run
1. One-time setup (Python 3.11 or 3.12, because TensorFlow needs it):
   `cd ecorewind_ml && python -m venv .venv && .venv/bin/pip install -r service/requirements.txt -r service/requirements-model.txt`
   (Windows: `.venv\Scripts\pip`). The trained model must be in `ecorewind_ml/outputs/`.
2. In this folder run `start.bat` (Windows) or `./start.sh` (Mac/Linux). Both start `serve.py`, which serves the pages with security headers, starts the scanner service on port 8100, and proxies `/api/*` to it. Wait for "Scanner service ready" in the console.
3. Open `http://localhost:8000/` for the launcher, or open `terminal.html` and `wallet.html` in two tabs side by side.

Serve over localhost: ES modules and the webcam do not work from `file://`. Both tabs must use the same address (do not mix `localhost` and `127.0.0.1`), because the linked wallet ID is stored per address. Fonts load from Google Fonts; offline they fall back to system fonts.

## Pages
| Page | Purpose |
|---|---|
| `terminal.html` | Phase 2: machine kiosk (camera, weight slider, AI scan, confirm) |
| `wallet.html` | Phase 3: balance, eco impact, history, CSV export, UPI withdrawals, live sync |
| `tests.html` | Phase 4: automated unit and live-server integration tests plus a manual checklist |
| `viva.html` | Phase 5: demo script, data-flow diagram, likely viva questions, fallback plan |

## Architecture
`terminal.html` → `serve.py` `/api` proxy → scanner service (`ecorewind_ml/service`, FastAPI) → SQLite ledger (`ecorewind_ml/service/data/scanner.db`) ← `wallet.html`

- The terminal sends a 224×224 camera frame to `POST /api/classify` every 450 ms for the live preview. This never touches the ledger.
- On Deposit it sends the frame and weight to `POST /api/scan`. The server re-classifies the frame, applies the tariff and records the deposit and the wallet credit in one transaction. The browser cannot set the payout.
- The wallet reads `GET /api/users/{id}/wallet`, `/transactions` and `/withdrawals`. The terminal pings it over BroadcastChannel after a deposit, and it also polls every 5 s.
- Withdrawals (`POST /api/users/{id}/withdrawals`) take the money from the balance at once and stay pending until an operator calls the service's `/admin/withdrawals/{id}/complete` or `/cancel` with `X-Admin-Key`. Admin routes are not proxied, so call them on port 8100 directly.
- The wallet ID is a demo stand-in for login: it is stored in localStorage (`rvm_user`, default `demo-user`) and shared by all tabs on the same address. "New demo wallet" switches to a fresh ID; old wallets stay in the ledger.

## Code
- `js/shared.js` preview tariff (`evaluate`, refreshed from `GET /api/rates`), API client, live sync, formatting. Money is integer paise.
- `js/terminal.js`, `js/wallet.js`, `js/viva.js` page logic.
- `serve.py` static server, `/api` proxy, starts and stops the service.
- `ecorewind_ml/service/` scanner service: `app.py` routes and rules, `wallet.py` ledger, `classifier.py` model wrapper, `config.py` settings from `.env`.
- `js/ai.js` is the old in-browser COCO-SSD detector. Nothing imports it any more.

## Testing
- Service: `cd ecorewind_ml/service && ../.venv/bin/python -m pytest -q tests` (24 tests: tariff, idempotency, withdrawals, auth, preview). Needs `pip install -r requirements-dev.txt` once.
- Browser: open `tests.html` and press Run all tests (22 unit checks plus 13 checks against the live server). The server checks use a throwaway `test-…` wallet with rejected scans only.
- Terminal: `node tests/run.mjs` runs the 22 unit checks without a browser (Node 18+; `package.json` sets ES-module mode).
- Clicks on the terminal UI itself are covered by the manual checklist in `tests.html`.

## Demo backup
If the live run fails, `screenshots/` has terminal, wallet, tests and viva page captures to show instead. They contain sample data only; see `screenshots/README.md`.

## Rules
These are enforced by the server and configured in `ecorewind_ml/service/.env` (see `.env.example`). The terminal shows the same rules as a live preview.
- The model must classify the item as a paid class with at least 75% confidence, otherwise payout is blocked (checked first).
- Weights must be finite and between 0 and 500 g; under 100 g is blocked as a low-density anomaly.
- Plastic bottle 2 paise/g (250 g = ₹5.00), aluminium can 4 paise/g, plastic bag 1 paise/g, +10% at 400 g or more.
- A rejected item can still be inserted; the server logs it as a rejected transaction and pays nothing.

## Data safety
- The ledger is a SQLite database owned by the service. Each deposit and its wallet credit, and each withdrawal and its debit, happen in one `BEGIN IMMEDIATE` transaction. Balances have a database check that stops them going negative.
- Every deposit and withdrawal carries an `Idempotency-Key`. The terminal and wallet retry once on a network error with the same key, so a lost response is never paid twice. Reusing a key with different data is refused (409).
- The browser holds no money, only the linked wallet ID. There is no login yet, so anyone who can reach the server can read any wallet ID; add authentication before real payouts.

## Security
- Every page has a Content-Security-Policy meta tag; `serve.py` adds CSP, `nosniff`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`, COOP and CORP headers. Both servers bind to `127.0.0.1` by default.
- `serve.py` only serves the root HTML pages and `css/`, `js/`, `screenshots/`. The ledger database, `.env`, model and dataset are not reachable over HTTP.
- The `/api` proxy forwards an allow-list of routes, never `/admin/*`, caps request bodies at 10 MB, and returns a generic 503 when the service is down instead of internal error text.
- If you set `ECOREWIND_API_KEY` for the service, export the same variable before running `serve.py`; the proxy adds it server-side so it never reaches the browser.
- `'unsafe-eval'` is still allowed on the terminal page for the old TensorFlow.js path in `js/ai.js`, which is no longer used; it can be removed together with that file.
- Dynamic text and alerts are written with `textContent`, not `innerHTML`, so server or model strings cannot inject markup.
- CSV export quotes every field and prefixes leading `= + - @` cells to block spreadsheet formula injection.
- Third-party network dependencies: Google Fonts only.
