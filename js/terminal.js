import { CONFIG, ITEM_TYPES, evaluate, formatINR, onWalletChange, loadRates, classifyImage, submitScan,
  fetchWalletSummary, getUserId, newIdempotencyKey, withRetry, announceWalletChange, isOffline } from './shared.js';

const $ = (id) => document.getElementById(id);

const state = {
  item: null,            // model class label from the last preview, e.g. 'plastic_bottle'
  weight: 250,
  conf: 0,
  label: 'Positioning item…',
  valid: false,
  decision: 'standby',
  reason: 'Align an item in the scanner chamber',
  scores: null,
  isClassifying: false,
  lastClassifyTime: 0,
  committing: false,
  hasStaticImage: false,
  staticImg: null,
  online: null,          // scanner service reachable? null until the first answer
  sourceGen: 0,          // bumped when switching camera <-> photo so late results are dropped
  queued: null,          // a photo waiting for the in-flight camera frame to finish
};

const video = $('video');
const canvas = $('overlay');
const ctx = canvas.getContext('2d');
const offCanvas = document.createElement('canvas');
offCanvas.width = 224;
offCanvas.height = 224;
const offCtx = offCanvas.getContext('2d');

/* ---------- machine log ---------- */
function log(msg, cls = '') {
  const d = document.createElement('div');
  if (cls) d.className = cls;
  d.textContent = `[${new Date().toLocaleTimeString('en-IN')}] ${msg}`;
  const l = $('log');
  l.appendChild(d);
  l.scrollTop = l.scrollHeight;
}

/* ---------- camera lifecycle ---------- */
/** Clears the preview when the image source changes so a stale verdict is never shown. */
function resetPreview() {
  state.sourceGen += 1;
  state.item = null;
  state.decision = 'standby';
  state.reason = '';
  updateUI();
}

async function startCamera() {
  state.hasStaticImage = false;
  state.staticImg = null;
  resetPreview();
  if (!navigator.mediaDevices?.getUserMedia) {
    return camFail('Camera API unavailable. Use localhost or HTTPS.');
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });
    video.srcObject = stream;
    video.onloadedmetadata = () => { video.play().catch(() => {}); };
    video.play().catch(() => {});
    $('camPill').textContent = 'Live camera';
    $('camPill').classList.add('live');
    $('camMsg').textContent = '';
    log('Continuous vision camera connected', 'g');
  } catch (e) {
    camFail('Camera stream blocked or unavailable. You can use the sample test buttons below.');
  }
}

function camFail(m) {
  $('camPill').textContent = 'Camera off';
  $('camPill').classList.remove('live');
  $('camMsg').textContent = m;
  log(m, 'y');
}

$('camBtn').onclick = startCamera;

/* ---------- real test sample item loaders ---------- */
async function loadSample(category, displayName) {
  try {
    const res = await fetch(`/api/sample?class=${category}`);
    const data = await res.json();
    if (data.ok && data.dataUrl) {
      const img = new Image();
      img.onload = () => {
        state.hasStaticImage = true;
        state.staticImg = img;
        resetPreview();
        $('camPill').textContent = displayName;
        $('camPill').classList.add('live');
        $('camMsg').textContent = `Loaded test sample: ${displayName}`;
        log(`Loaded real test sample: ${displayName}`, 'g');
        classifyImageSource(img, true);
      };
      img.src = data.dataUrl;
    }
  } catch (err) {
    log(`Failed to load sample: ${err.message}`, 'y');
  }
}

$('sampleBottleBtn').onclick = () => loadSample('plastic_bottle', 'PET Bottle');
$('sampleCanBtn').onclick = () => loadSample('aluminium_can', 'Aluminium Can');
$('sampleBagBtn').onclick = () => loadSample('plastic_bag', 'Plastic Bag');
$('sampleTrashBtn').onclick = () => loadSample('other_invalid', 'Invalid Waste');

/* ---------- file upload & drop for photo scanning ---------- */
const fileInput = $('fileInput');
$('uploadBtn').onclick = () => fileInput.click();
fileInput.onchange = (e) => {
  const file = e.target.files?.[0];
  if (file) handleImageFile(file);
};

const stage = $('stage');
stage.ondragover = (e) => { e.preventDefault(); stage.style.outline = '2px dashed #f2a900'; };
stage.ondragleave = () => { stage.style.outline = 'none'; };
stage.ondrop = (e) => {
  e.preventDefault();
  stage.style.outline = 'none';
  const file = e.dataTransfer.files?.[0];
  if (file && file.type.startsWith('image/')) handleImageFile(file);
};

function handleImageFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      state.hasStaticImage = true;
      state.staticImg = img;
      resetPreview();
      $('camPill').textContent = 'Photo file';
      $('camPill').classList.add('live');
      $('camMsg').textContent = file.name;
      log(`Loaded photo: ${file.name}`, 'g');
      classifyImageSource(img, true);
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

/* ---------- UI update ---------- */
function updateUI() {
  $('wOut').textContent = state.weight;
  const evalRes = evaluate(state.item, state.weight);

  const aiCard = $('aiCard');
  const aiConf = $('aiConf');
  const aiName = $('aiDetectedName');
  const aiDesc = $('aiDetectedDesc');
  const rupeeCard = $('rupeeCard');
  const estVal = $('est');
  const rateDetail = $('rateDetail');
  const confirmBtn = $('confirmBtn');

  if (state.decision === 'accepted') {
    aiCard.className = 'live-ai-card ok';
    aiConf.className = 'conf-pill ok';
    aiConf.textContent = `${state.conf}% match`;
    aiName.textContent = state.label;
    aiDesc.textContent = state.reason || 'Recyclable material verified by trained model';
  } else if (state.decision === 'rejected') {
    aiCard.className = 'live-ai-card err';
    aiConf.className = 'conf-pill err';
    aiConf.textContent = `${state.conf}% match`;
    aiName.textContent = state.label;
    aiDesc.textContent = state.reason || 'Non-recyclable object. Item will not be credited.';
  } else if (state.online === false) {
    aiCard.className = 'live-ai-card err';
    aiConf.className = 'conf-pill err';
    aiConf.textContent = 'Offline';
    aiName.textContent = 'Scanner service offline';
    aiDesc.textContent = state.reason || 'Check the serve.py console. The model takes a few seconds to load on start.';
  } else {
    aiCard.className = 'live-ai-card';
    aiConf.className = 'conf-pill';
    aiConf.textContent = 'Scanning…';
    aiName.textContent = 'Analyzing chamber feed…';
    aiDesc.textContent = 'Position plastic bottle, aluminium can, or plastic bag in view';
  }

  // Live rupee preview. The server recomputes the payout from the frame on deposit.
  const payable = state.decision === 'accepted' && evalRes.ok;
  if (payable) {
    rupeeCard.className = 'rupee-card';
    estVal.textContent = (evalRes.paise / 100).toFixed(2);
    const rateText = `${ITEM_TYPES[state.item]?.ratePaise || 2} paise per gram · ${state.weight} g`;
    const bonusText = evalRes.bonusPaise ? ` (includes ${CONFIG.BONUS_PCT}% heavy load bonus +${formatINR(evalRes.bonusPaise)})` : '';
    rateDetail.textContent = rateText + bonusText;
  } else {
    rupeeCard.className = 'rupee-card err';
    estVal.textContent = '0.00';
    if (state.decision === 'accepted') rateDetail.textContent = `${evalRes.title}: ${evalRes.message}`;
    else if (state.decision === 'rejected') rateDetail.textContent = state.reason || 'Item not accepted. No payout.';
    else rateDetail.textContent = 'Position item in chamber to calculate payout…';
  }

  // A rejected item can still be inserted: the server logs the rejection and pays nothing.
  confirmBtn.disabled = state.committing || state.decision === 'standby' || !currentSource();
  if (state.committing) {
    confirmBtn.className = 'btn';
    confirmBtn.textContent = 'Processing deposit…';
  } else if (state.decision === 'standby') {
    confirmBtn.className = 'btn';
    confirmBtn.textContent = state.online === false ? 'Scanner offline' : 'Hold recyclable item in view';
  } else if (payable) {
    confirmBtn.className = 'btn gold';
    confirmBtn.textContent = `Deposit & Claim ${formatINR(evalRes.paise)}`;
  } else {
    confirmBtn.className = 'btn danger';
    confirmBtn.textContent = evalRes.code === 'LOW_DENSITY' && state.decision === 'accepted'
      ? `Insert anyway: under ${CONFIG.MIN_WEIGHT_G} g (no payout)`
      : 'Insert anyway (no payout)';
  }
}

$('weight').oninput = (e) => {
  state.weight = +e.target.value;
  updateUI();
};

/* ---------- continuous classification loop ---------- */
function currentSource() {
  if (state.hasStaticImage && state.staticImg) return state.staticImg;
  return video.readyState >= 2 && video.videoWidth > 0 ? video : null;
}

/** 224x224 JPEG of the current frame, the size the model was trained on. */
function frameBlob(source) {
  offCtx.drawImage(source, 0, 0, 224, 224);
  return new Promise((resolve, reject) => offCanvas.toBlob(
    (b) => (b ? resolve(b) : reject(new Error('Could not capture the camera frame'))), 'image/jpeg', 0.85));
}

let ratesLoaded = false;
function refreshRates() {
  loadRates()
    .then(() => { ratesLoaded = true; $('weight').max = CONFIG.MAX_WEIGHT_G; updateUI(); })
    .catch(() => log('Could not load the tariff from the server; showing built-in rates until it answers', 'y'));
}

function setOnline(online, reason = '') {
  if (online && !ratesLoaded && state.online !== true) refreshRates();
  if (state.online !== online) {
    if (online) log('Scanner service online', 'g');
    else log(`Scanner service offline: ${reason}`, 'e');
    $('modelPill').textContent = online ? 'AI: 4-Class MobileNetV2' : 'AI: offline';
  }
  state.online = online;
  if (!online) {
    state.decision = 'standby';
    state.reason = reason;
  }
}

async function classifyImageSource(source, mustRun = false) {
  if (state.isClassifying) {
    if (mustRun) state.queued = source;   // a camera frame is in flight; classify the photo right after
    return;
  }
  state.isClassifying = true;
  const gen = state.sourceGen;
  try {
    const res = await classifyImage(await frameBlob(source));
    setOnline(true);
    if (gen !== state.sourceGen) return;   // the source changed while this frame was in flight
    const prevKey = state.item;
    state.item = res.label;
    state.label = res.display_label;
    state.conf = Math.round(res.confidence * 100);
    state.decision = res.decision;
    state.reason = res.reason;
    state.valid = res.decision === 'accepted';
    state.scores = res.scores;
    state.lastClassifyTime = performance.now();

    if (prevKey !== res.label && res.confidence >= 0.70) {
      log(`AI Detected: ${res.display_label} (${state.conf}%)`, state.valid ? 'g' : 'y');
    }
  } catch (err) {
    if (isOffline(err)) setOnline(false, err.message);
    // Other errors (a bad frame) are skipped so the stream keeps going.
  } finally {
    state.isClassifying = false;
    const next = state.queued;
    state.queued = null;
    if (next) classifyImageSource(next, true);
    else updateUI();
  }
}

// Tick loop running continuously every 450ms
setInterval(() => {
  if (state.committing) return;
  if (state.hasStaticImage && state.staticImg) {
    // A photo is classified once; retry only while that has not succeeded (e.g. service offline).
    if (state.decision === 'standby') classifyImageSource(state.staticImg);
    return;
  }
  if (currentSource() && !video.paused) classifyImageSource(video);
}, 450);

/* ---------- confirm deposit transaction ---------- */
function showAlert(kind, title, message = '') {
  const box = $('alertBox');
  box.replaceChildren();
  const d = document.createElement('div');
  d.className = 'status ' + kind;
  const b = document.createElement('strong');
  b.textContent = title;
  d.appendChild(b);
  if (message) d.appendChild(document.createTextNode(message));
  box.appendChild(d);
}

$('confirmBtn').onclick = async () => {
  const source = currentSource();
  if (state.committing || state.decision === 'standby' || !source) return;

  state.committing = true;
  updateUI();
  const grams = state.weight;
  const key = newIdempotencyKey();

  try {
    const image = await frameBlob(source);
    const res = await withRetry(() => submitScan({ image, grams, key }));
    const t = res.transaction;
    if (res.decision === 'accepted') {
      showAlert('ok', 'Deposit Successful!', `${res.display_label} (${grams} g). Credited ${formatINR(t.payout_paise)} to wallet ${getUserId()}. Txn #${t.id}.`);
      log(`Txn #${t.id} credited +${formatINR(t.payout_paise)} (${res.display_label})`, 'g');

      // Floating celebratory coin
      const c = document.createElement('div');
      c.className = 'coin';
      c.textContent = '+' + formatINR(t.payout_paise);
      document.body.appendChild(c);
      setTimeout(() => c.remove(), 1400);
    } else {
      showAlert('err', 'Item rejected, no payout', `${res.reason} Logged as Txn #${t.id}. Please take the item back.`);
      log(`Txn #${t.id} rejected: ${res.reason}`, 'y');
    }
    $('linked').textContent = formatINR(res.wallet.balance_paise);
    announceWalletChange();
  } catch (e) {
    showAlert('err', 'Deposit Failed', `${e.message} Nothing was charged or credited.`);
    log('Deposit error: ' + e.message, 'e');
    if (isOffline(e)) setOnline(false, e.message);
  } finally {
    state.committing = false;
    updateUI();
  }
};

async function showBalance() {
  $('linkedUser').textContent = getUserId();
  try {
    const w = await fetchWalletSummary();
    $('linked').textContent = formatINR(w.balancePaise);
  } catch {
    $('linked').textContent = '—';
  }
}
onWalletChange(showBalance);

/* ---------- scanner stage overlay drawing ---------- */
function frame(now) {
  const dpr = devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== w * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  // If a static photo is loaded, draw it directly on canvas if video is not streaming
  if (state.hasStaticImage && state.staticImg) {
    const img = state.staticImg;
    const ratio = Math.max(w / img.width, h / img.height);
    const nw = img.width * ratio;
    const nh = img.height * ratio;
    ctx.drawImage(img, (w - nw) / 2, (h - nh) / 2, nw, nh);
  }

  // Scanner target reticle
  const bw = w * 0.62;
  const bh = h * 0.68;
  const x = (w - bw) / 2;
  const y = (h - bh) / 2;

  let color = 'rgba(255,255,255,0.6)';
  let topTag = 'Align item in frame';
  let bottomTag = '';

  const evalRes = evaluate(state.item, state.weight);

  if (state.decision === 'accepted') {
    color = '#6fe3a0';
    topTag = `${state.label} (${state.conf}%)`;
    bottomTag = evalRes.ok ? `Reward: ${formatINR(evalRes.paise)}` : 'Adjust weight';
  } else if (state.decision === 'rejected') {
    color = '#ff6b5e';
    topTag = `${state.label} (${state.conf}%)`;
    bottomTag = 'No payout (Blocked)';
  } else if (state.online === false) {
    color = '#ff6b5e';
    topTag = 'Scanner offline';
  }

  // Draw corner brackets
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  const L = 28;
  [
    [x, y, 1, 1],
    [x + bw, y, -1, 1],
    [x, y + bh, 1, -1],
    [x + bw, y + bh, -1, -1],
  ].forEach(([cx, cy, sx, sy]) => {
    ctx.beginPath();
    ctx.moveTo(cx + sx * L, cy);
    ctx.lineTo(cx, cy);
    ctx.lineTo(cx, cy + sy * L);
    ctx.stroke();
  });

  // Animated laser scan sweep
  const p = ((now - state.lastClassifyTime) % 1500) / 1500;
  ctx.fillStyle = color === '#6fe3a0' ? 'rgba(111,227,160,0.25)' : 'rgba(242,169,0,0.3)';
  ctx.fillRect(x + 2, y + p * (bh - 4), bw - 4, 3);

  // Top header badge
  ctx.font = '600 13px "Plus Jakarta Sans", sans-serif';
  const tw = ctx.measureText(topTag).width + 18;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.roundRect(x, y - 28, tw, 24, 6);
  ctx.fill();
  ctx.fillStyle = '#10201d';
  ctx.fillText(topTag, x + 9, y - 12);

  // Bottom Rupee payout badge (if detected)
  if (bottomTag) {
    ctx.font = '700 13px "Plus Jakarta Sans", sans-serif';
    const bwVal = ctx.measureText(bottomTag).width + 18;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x + bw - bwVal, y + bh + 4, bwVal, 24, 6);
    ctx.fill();
    ctx.fillStyle = '#10201d';
    ctx.fillText(bottomTag, x + bw - bwVal + 9, y + bh + 20);
  }

  requestAnimationFrame(frame);
}

log('EcoRewind RVM-01 booted. Continuous vision scanner online.', 'g');
refreshRates();
showBalance();
updateUI();
startCamera();
requestAnimationFrame(frame);
