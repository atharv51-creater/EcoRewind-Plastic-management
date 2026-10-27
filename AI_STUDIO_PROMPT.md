ROLE
You are a senior front-end engineer and product designer. Build a complete, production-quality front end for an existing, working back end called EcoRewind. Do not build, change or "improve" the back end. Talk to it only through the REST API described below, exactly as written.

PROJECT
EcoRewind is a reverse vending machine (RVM) for India. A person enters their mobile number on the machine, holds a plastic bottle, aluminium can or plastic bag up to the machine's camera, places it on the scale and inserts it. A trained image model checks the item, the server works out the payout by weight, and the money lands in the person's EcoRewind wallet, which they can withdraw to UPI.

The front end has two jobs:
1. Be the machine's own touchscreen, and show the machine physically working while it does it (the item going in, being scanned, weighed, sorted into a bin, coins and a receipt coming out), driven by real API responses.
2. Be the user's wallet: balance, history, impact and UPI withdrawals.

TECH STACK
- React 18 + TypeScript + Vite. Must run with `npm install && npm run dev` and build with `npm run build`.
- Routing: React Router.
- Styling: CSS Modules plus one global tokens file (CSS custom properties). No Tailwind CDN, no CDN <script> tags, no import maps in the final build. Every dependency is installed from npm and bundled.
- Animation: Framer Motion and CSS transforms. All illustrations are hand-built inline SVG. No video, GIF, Lottie or stock illustration files.
- Icons: lucide-react only, one stroke width (1.75) everywhere.
- No other UI kit (no MUI, Chakra, shadcn).
- The built app must work under this Content-Security-Policy, which the existing server sends: `script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob: mediastream:; connect-src 'self'`. So: no inline <script>, no eval, no third-party requests except Google Fonts.
- The machine page uses the camera. If this platform uses a metadata.json for frame permissions, request "camera".

API MODES (so the preview works without my server)
- One central module `src/services/api.ts` exports a typed client with two interchangeable implementations behind the same interface:
  - `live`: real HTTP calls to `${VITE_API_BASE_URL}` (default `/api`).
  - `mock`: an in-memory simulator of the same API (persisted in localStorage, 300–800 ms simulated latency) that follows exactly the same rules, response shapes and error codes as below.
- Choose with `VITE_API_MODE=live|mock` (default `mock`). Show a small, honest "Demo data" pill in the footer and on the machine when in mock mode.
- In `vite.config.ts`, proxy `/api` to `http://127.0.0.1:8000` so live mode works in local development with the real server running.
- Provide `.env.example` with `VITE_API_MODE=live` and `VITE_API_BASE_URL=/api`.
- All endpoint paths live in one file, `src/services/endpoints.ts`.

THE REAL API (exact contract; do not invent other endpoints)
There is no login, registration, OTP, JWT, roles or admin API available to the browser. Do not create them. A person is identified by a wallet ID, which in this UI is their 10-digit mobile number (any string matching `^[A-Za-z0-9_.@-]{1,64}$` is valid; the demo default is `demo-user`). Remember the linked wallet ID in localStorage under the key `rvm_user` so all pages and tabs share it.

All money is integer paise (100 paise = ₹1). Never use floating-point rupees in state or calculations; format only for display with en-IN grouping, e.g. ₹1,234.50. Weights are grams. Timestamps are ISO-8601 UTC strings; show them in local time.

GET /health
-> 200 { status: "ok" | "degraded", model_ready: boolean, model_detail: string, threshold: number (0.75), accepted_classes: string[], version: string }

GET /rates
-> 200 { rates_paise_per_gram: { plastic_bottle: 2, aluminium_can: 4, plastic_bag: 1 }, accepted_classes: ["aluminium_can", "plastic_bag", "plastic_bottle"], min_weight_g: 100, max_weight_g: 500, bonus_threshold_g: 400, bonus_pct: 10 }
Load this at start and use it for every rate, limit and preview in the UI. Never hard-code rates in components (the mock uses these values as its defaults).

POST /classify  (live preview; never changes any data)
Body: multipart/form-data, field `image` = JPEG of the current frame (224x224, quality 0.85).
-> 200 { decision: "accepted" | "rejected", reason: string, label: "plastic_bottle" | "aluminium_can" | "plastic_bag" | "other_invalid", display_label: string, confidence: number (0-1), scores: { [label]: number }, rate_paise_per_gram: number, model: string }
"decision" here only says whether the item class is payable (confidence >= threshold and class accepted). It ignores weight.

POST /scan  (the real deposit)
Headers: `Idempotency-Key: <uuid>` (required; a new one per deposit attempt).
Body: multipart/form-data: `image` (JPEG of the frame at the moment of insertion), `user_id`, `weight_grams` (number as string), `machine_id` ("RVM-01").
-> 200 { scan_id, duplicate: boolean, decision: "accepted" | "rejected", reason, label, display_label, confidence, scores, transaction: Transaction, wallet: Wallet, model }
A rejected item is NOT an HTTP error: it returns 200 with decision "rejected" and payout 0, and it is recorded in history. The server re-classifies the image itself and its answer is final; always show the server's result, even if it differs from the preview.
Retrying with the same key and the same data returns the original result with duplicate: true (it never pays twice). The same key with different data -> 409 IDEMPOTENCY_CONFLICT.

GET /users/{user_id}/wallet
-> 200 Wallet { user_id, balance_paise, total_grams, confirmed_count, rejected_count, total_paid_paise, created_at: string | null, updated_at: string | null }
An unknown user returns a zero wallet, not an error.

GET /users/{user_id}/transactions?limit=1..200&offset=0&status=accepted|rejected
-> 200 { items: Transaction[], total, limit, offset }, newest first.
Transaction { id, kind: "deposit", status: "accepted" | "rejected", label, display_label, confidence, weight_grams, rate_paise_per_gram, payout_paise, bonus_paise, reason, machine_id, scores, created_at }

POST /users/{user_id}/withdrawals
Headers: `Idempotency-Key`, `Content-Type: application/json`.
Body: { amount_paise: integer 1-100000000, destination: string 3-120 chars (a UPI ID) }
-> 201 { duplicate, withdrawal: Withdrawal, wallet: Wallet }. The amount leaves the balance at once; status stays "pending" until an operator pays or cancels it (cancelling refunds it). Not enough balance -> 409 INSUFFICIENT_FUNDS.

GET /users/{user_id}/withdrawals?limit&offset
-> 200 { items: Withdrawal[], total, limit, offset }
Withdrawal { id, user_id, amount_paise, destination, status: "pending" | "paid" | "cancelled", created_at, updated_at }

Errors: parse all three shapes into one ApiError { status, code, message }:
- `{ "detail": { "code": "...", "message": "..." } }` from the service
- `{ "error": { "code": "...", "message": "...", "details"?: [...] } }` from the proxy and from validation (422 VALIDATION_ERROR)
- `{ "detail": "Not Found" }` (a plain string)
Codes: INVALID_USER_ID 400, IDEMPOTENCY_KEY_REQUIRED 400, EMPTY_IMAGE 400, INVALID_IMAGE 400, IMAGE_TOO_LARGE 413, MODEL_UNAVAILABLE 503, SERVICE_OFFLINE 503, IDEMPOTENCY_CONFLICT 409, INSUFFICIENT_FUNDS 409, INVALID_DESTINATION 400, VALIDATION_ERROR 422. Treat a failed fetch or a timeout as code NETWORK. Show human messages, never raw JSON.

Server rules (the mock must implement exactly these; the UI uses them only for previews):
1. The item class must be in accepted_classes, have a rate > 0, and confidence >= threshold (0.75). Otherwise rejected, with reasons like "Low confidence (71%). Rescan the item." or "Invalid item is not accepted by this machine."
2. Weight must be finite and 0 <= weight <= max_weight_g, otherwise rejected ("Invalid weight reading. The load cell must be between 0 and 500 g.").
3. Weight below min_weight_g (100 g) is rejected ("80 g is under the 100 g minimum. Payout blocked.").
4. base = round_half_up(weight x rate); bonus = weight >= bonus_threshold_g ? round_half_up(base x bonus_pct / 100) : 0; payout = base + bonus.
   Examples: bottle 250 g -> 500 paise (₹5.00); bottle 400 g -> 800 + 80 = ₹8.80; can 450 g -> 1800 + 180 = ₹19.80; bag 140 g -> ₹1.40.

Cross-tab live updates (must stay compatible with the existing pages):
- After every deposit or withdrawal, post `{ type: "wallet", userId }` on `new BroadcastChannel("ecorewind")`.
- Any page showing a wallet refetches when it receives that message for its user, when the `rvm_user` localStorage key changes, when the tab becomes visible again, and every 5 s while visible.

PAGES

1. `/` Landing (public)
- One honest headline ("Drop a bottle. Get paid." or similar), one line with the real rates from /rates ("2 paise a gram for plastic bottles, 4 for cans"), and two actions: "Use the machine" and "Open my wallet".
- "How it works" in 3 steps with small custom SVG drawings.
- The animated demo machine (see SIMULATION, demo variant) using sample data only. No API calls for the demo.
- A rates table from /rates, and a short note on why the machine checks both the item and its weight.
- No testimonials, no invented statistics, no partner logos.

2. `/machine` The machine (main screen; designed for a 1280x800 kiosk and laptops, still usable on a phone)
The page IS the machine: a front view of a physical reverse vending machine, drawn in SVG/CSS, with its parts doing their jobs in sync with the app state. The touchscreen UI lives inside the machine's screen.
a) Attract screen (IDLE): "EcoRewind RVM-01", today's rates, "Tap to start". After 90 s without interaction, any session ends and the machine returns here.
b) Link wallet (SESSION_START): on-screen numeric keypad for a 10-digit mobile number (validated, with a clear delete key; the physical keyboard works too), plus "Use demo wallet" (`demo-user`). Show the masked number (98•••••210) and the current balance once linked. Say plainly that this demo has no OTP.
c) Scanning (DETECTING): the camera feed appears in the machine's scanner window. Every ~600 ms, with only one request in flight, send the frame to POST /classify and show the live verdict: item name, a confidence bar, and either "We take this" (green) or the reason it won't be paid (red). If the source changes (camera <-> photo) while a request is in flight, discard the late result. Pause this loop while a deposit is processing.
   Sources: live camera (getUserMedia, rear camera preferred, 640x480 ideal), "Use a photo" (file picker or drag-and-drop onto the scanner window), and in mock mode a "Sample items" tray (bottle, can, bag, stone) whose results are simulated. If the camera is blocked or missing, say so plainly and offer the photo option. Never show an empty black box.
d) Weighing: a scale platform inside the chamber with a digital readout. The weight comes from a "Simulated load cell" control: a slider from 0 to max_weight_g in 5 g steps plus quick chips (80 g, 150 g, 250 g, 450 g). Label it clearly as a demo control. The live estimate reads like "250 g x 2 paise = ₹5.00", with a bonus line when it applies, all from /rates.
e) Insert: one large primary button whose label states what will happen: "Insert & get ₹5.00" when the preview is payable, or "Insert anyway, no payout" (red outline) when it is not. Disabled, with the reason shown, while nothing is detected or the machine is offline.
f) On insert (INSERTING -> PROCESSING): capture the current frame, play the insertion animation and POST /scan with a fresh Idempotency-Key. On a NETWORK failure, retry once with the same key and the same image blob. While waiting, keep the scanner beam moving and show "Checking your item…" (never a bare spinner).
g) Result:
   - ACCEPTED: the item drops into the matching bin (PET / CANS / BAGS), the screen counts up the payout, coins drop into the tray, a receipt line prints ("#128  Plastic bottle  250 g  ₹5.00"), the balance updates from the response's wallet, and the BroadcastChannel message is sent. If duplicate is true, show the same result marked "Already recorded".
   - REJECTED: the conveyor runs backwards and the item comes back out of the slot; the screen shows the server's reason in plain words and "Please take your item back". No coins.
   - ERROR (offline, 503, etc.): "Nothing was charged or credited", the reason, and a retry button.
   Then go back to DETECTING for the next item.
h) Finish: "Done" ends the session and shows a session receipt on the screen (items, weights, each payout, total, new balance, time) with "Open wallet", then returns to the attract screen after 20 s.
i) Offline: if /health or any request gives SERVICE_OFFLINE, NETWORK or MODEL_UNAVAILABLE, the machine shows "Out of service" (amber status light, screen message, insert disabled), checks /health every 5 s and recovers by itself, reloading /rates when it does.
j) "Inside the machine" panel beside the machine (collapsible on small screens): a vertical pipeline of the real steps, Camera -> AI model -> Scale -> Rules -> Ledger -> Wallet, each lighting up as it happens with the real values (confidence 96%, 250 g, ₹5.00, Txn #128, new balance). Under it, a machine log (timestamped, last 20 events, monospace). This is the "working along" view for demos.
Implement the machine as an explicit finite state machine (a typed useReducer): OFFLINE, IDLE, SESSION_START, DETECTING, INSERTING, PROCESSING, ACCEPTED, REJECTED, ERROR, RECEIPT. Every visual and every button derives from that state, not from scattered booleans.

3. `/wallet` Wallet
- If no wallet is linked: an "Enter your mobile number" form (same validation) and "Use demo wallet".
- Header: masked wallet ID, "Switch wallet", and a sync indicator ("Updated 12:48:09" or "Can't reach the server, showing the last data").
- Balance card: available balance (counts up on change), lifetime earnings (total_paid_paise), and a progress bar to a ₹100 lifetime-earnings reward.
- Impact row: kg recycled (total_grams / 1000), CO₂ avoided (1.5 kg per kg, labelled "estimate"), bottle equivalents (25 g each), items accepted, items rejected.
- History: table with a filter (All / Accepted / Rejected), newest first, pagination with limit/offset (25 per page) and the total count. Rejected rows show the server's reason. A row that arrives live gets a brief highlight and a small toast ("+₹5.00 for a plastic bottle").
- Export CSV of the loaded rows: quote every cell, and prefix cells starting with = + - @ tab or carriage return with an apostrophe (formula-injection safe).
- Withdraw to UPI: amount in rupees (parsed to whole paise; at most 2 decimals; more than 0; not more than the balance), UPI ID validated with `^[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9.-]{1,}$`, a confirm modal showing the amount and UPI ID, a new Idempotency-Key per request (retry once on NETWORK with the same key), then a success state. One line explains that requests stay pending until an operator pays them, and cancelled ones are refunded.
- Withdrawals list with status chips: Pending (amber), Paid (green), Cancelled, refunded (grey).

4. `/live` Presentation mode (for demos and the project viva)
- Side by side on a wide screen: the full machine on the left, and on the right a phone frame showing the wallet for the same wallet ID, updating live as items are accepted. It must update through BroadcastChannel and polling, not shared React state, to prove the sync is real. Stacks on narrow screens.

5. A 404 page with a link home.

OUT OF SCOPE (do not build): login, registration, OTP, admin or operator dashboards, user lists, rate editing. The back end exposes none of these to browsers. If you think one is needed, list it under "Suggested back-end additions" instead of faking it.

SIMULATION (the machine drawing; the heart of the design)
Draw one detailed, clean SVG illustration of the machine, used in two variants: `demo` (landing page, sample data, auto-plays) and `live` (machine page, driven by the state machine).
- Front view of a real RVM cabinet: rounded body in deep forest green with an off-white front panel, a brand plate "EcoRewind" at the top, a status light (green ready, blue busy, amber out of service), the touchscreen in the upper middle, a round insertion slot with rubber flaps on the LEFT, a coin tray and receipt slot on the RIGHT, and at the bottom three bin doors labelled PET, CANS and BAGS with small fill indicators that rise for items accepted in this session.
- A "Show inside" toggle cuts the front panel away to reveal the chamber: conveyor rollers, the camera and scanner window, the scale platform with its readout, a diverter flap and the three bins.
- Animations (transform and opacity only, 60 fps, no layout shift):
  1. Insert: the item (an SVG bottle, can, bag or generic object matching the label) slides into the slot and the flaps flex.
  2. Scan: a light line sweeps over the item with a soft glow; the scanner window's corner brackets turn green or red.
  3. Weigh: the scale readout ticks up to the weight.
  4. Accepted: the diverter flips towards the right bin, the item drops, the bin indicator rises; the screen counts up the payout; 2 to 5 coins drop into the tray (more for bigger payouts); a receipt edges out of the slot.
  5. Rejected: the conveyor rollers spin backwards and the item slides back out of the slot; the status light pulses amber once.
- `demo` variant: loops with sample data (bottle 250 g -> ₹5.00, then a stone -> rejected, then a can 450 g -> ₹19.80), with Play, Pause and Replay controls; it starts when scrolled into view and pauses when off-screen.
- prefers-reduced-motion: no movement; show each step's final frame with a short text caption instead.
- Responsive: the machine scales as one unit and never overflows the viewport; on phones it sits above its controls.

DESIGN DIRECTION (white, warm and physical, not "AI-generated")
- The page background is white (#FFFFFF). Use a very light warm grey (#F6F6F3) only for section bands and table headers, and 1px borders (#E5E6E1) instead of shadows. One soft shadow is allowed, under the machine only.
- Colour tokens (CSS variables, used everywhere): --green-900 #12372A (machine body, headings), --green-700 #1F5C45 (primary buttons, links), --green-100 #E5F0EA (accepted backgrounds), --ink #1C2321 (text), --muted #5B655F (secondary text), --amber #E09B2D (money, coins, rewards; used sparingly), --red #B9402B (rejections), --red-100 #FBEAE7, --line #E5E6E1. The machine's touchscreen is a dark panel (#0E1F19) with off-white text, like a real kiosk screen.
- Typography: "Bricolage Grotesque" (600/700) for headings and big numbers, "Plus Jakarta Sans" (400/500/600) for text, "JetBrains Mono" for the machine log, receipts and scale readout, all from Google Fonts. Tabular numerals for every amount. A clear size scale (14/16/20/28/40/56 px) and spacing on an 8 px grid.
- Layout: a real 12-column grid with deliberate asymmetry (for example, machine 7 columns, panel 5), aligned edges and plenty of whitespace. A consistent 12 px radius on cards and fully rounded chips.
- Avoid: purple or blue gradients, glassmorphism, neon glows, floating blobs, sparkle or "AI" icons, emoji, robot illustrations, "AI-powered" badges, three identical feature cards with icons in circles, fake testimonials or numbers, and words like "revolutionize", "seamless", "unlock", "empower", "next-gen".
- Copy: short, concrete and human, for an Indian user: "Drop a bottle. Get paid.", "We take plastic bottles, cans and plastic bags.", "Please take your item back. It's lighter than 100 g.", "₹5.00 added to your wallet." Use ₹ and Indian number grouping. No lorem ipsum anywhere.
- Motion: purposeful and quick (150–250 ms UI transitions, count-ups on money). Nothing loops forever except the demo machine and the scanner beam while scanning.
- Accessibility: WCAG AA contrast, full keyboard use (including the keypad and the insert button), visible focus rings in --green-700, aria-live="polite" for machine screen messages and aria-live="assertive" for results, labels on every input, and a text alternative for every animation state.

CODE QUALITY
- Folders: src/components (Button, Input, Card, Table, Modal, Toast, Loader, EmptyState, StatusChip, Money, Keypad), src/machine (SVG parts, state machine, animations), src/pages, src/services (endpoints.ts, api.ts, live client, mock client, errors.ts), src/hooks (useWallet, useRates, useHealth, useCamera, useWalletSync), src/context (WalletIdContext), src/utils (money, csv, ids), src/styles (tokens.css, global.css).
- Strict TypeScript types for every request and response above. No `any`.
- A single fetch wrapper with timeouts (15 s by default, 20 s for /scan, 8 s for /classify) that returns typed data or throws ApiError.
- Every data view has loading (skeletons, not bare spinners), empty and error states with a retry. Never a blank screen.
- Unit tests (Vitest) for money formatting, rupee parsing, the payout rules (all the examples above), CSV escaping, error parsing, and the machine reducer's transitions.
- No console errors or warnings, no unused code, no secrets.

DELIVERABLES
1. The complete source code, file by file.
2. A README: setup; `npm run dev` in mock mode; running in live mode against the real server (start the EcoRewind server so it listens on http://127.0.0.1:8000, set VITE_API_MODE=live, run `npm run dev`; the Vite proxy forwards /api); environment variables; folder structure; where the endpoints live; and how to build for production.
3. A short list of assumptions, and any back-end additions you would suggest (for example OTP login or an operator dashboard), kept clearly separate from what you built.

The API contract above is complete and final. If something in it looks contradictory, ask me before building; otherwise build it without inventing new endpoints.
