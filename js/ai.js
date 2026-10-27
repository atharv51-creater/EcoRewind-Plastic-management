// Optional real in-browser object detection (TensorFlow.js + COCO-SSD) loaded from CDN on demand.
const SCRIPTS = [
  { src: 'https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js',
    integrity: 'sha384-vE8hbVJ4lezako5rlvE7bY0BVzWlFhZncPlckrqNwcUQpVtgbENTgZ8TBbnPjZre' },
  { src: 'https://cdn.jsdelivr.net/npm/@tensorflow-models/coco-ssd@2.2.3/dist/coco-ssd.min.js',
    integrity: 'sha384-7qLdgfEQyO9ZQi9ArRHigK+IBto4XPk468jAqc+fnsXaZIcMAhQeLwzggRK7aESl' },
];
const SCRIPT_TIMEOUT_MS = 20000;
const MODEL_TIMEOUT_MS = 45000;
const DETECT_TIMEOUT_MS = 10000;
let model = null;

function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

const load = ({ src, integrity }) => new Promise((res, rej) => {
  const s = document.createElement('script');
  s.src = src; s.integrity = integrity; s.crossOrigin = 'anonymous';
  s.onload = res; s.onerror = () => rej(new Error('Could not load ' + src));
  document.head.appendChild(s);
});

export async function loadModel() {
  if (model) return model;
  if (!window.tf) await withTimeout(load(SCRIPTS[0]), SCRIPT_TIMEOUT_MS, 'TensorFlow.js load timed out');
  if (!window.cocoSsd) await withTimeout(load(SCRIPTS[1]), SCRIPT_TIMEOUT_MS, 'COCO-SSD load timed out');
  model = await withTimeout(window.cocoSsd.load({ base: 'lite_mobilenet_v2' }), MODEL_TIMEOUT_MS, 'Detection model load timed out');
  return model;
}

async function waitForVideo(video) {
  if (video.videoWidth > 0 && video.videoHeight > 0 && video.readyState >= 2) return;
  await withTimeout(new Promise((resolve) => {
    const check = () => {
      if (video.videoWidth > 0 && video.readyState >= 2) {
        video.removeEventListener('loadeddata', check); video.removeEventListener('loadedmetadata', check);
        resolve();
      }
    };
    video.addEventListener('loadeddata', check); video.addEventListener('loadedmetadata', check);
  }), 5000, 'Camera stream is not ready');
  if (!video.videoWidth || !video.videoHeight) throw new Error('Camera stream has no frame yet');
}

// COCO classes mapped to machine item keys. Anything else is treated as invalid trash.
const MAP = { bottle: 'pet', cup: 'cup', 'wine glass': 'cup' };

/** Samples the video frame and returns the classified object. */
export async function classify(video, tries = 4) {
  await waitForVideo(video);

  // 1. Try real-time inference with our custom trained EcoRewind ML model
  try {
    const offscreen = document.createElement('canvas');
    offscreen.width = 224;
    offscreen.height = 224;
    const ctx = offscreen.getContext('2d');
    ctx.drawImage(video, 0, 0, 224, 224);
    const dataUrl = offscreen.toDataURL('image/jpeg', 0.85);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3500);
    const res = await fetch('/api/classify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataUrl }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (res.ok) {
      const data = await res.json();
      if (data.ok) {
        return {
          key: data.key,
          label: data.label,
          conf: Math.round((data.confidence || 0) * 100),
          box: null,
          vw: video.videoWidth,
          vh: video.videoHeight,
          source: 'ecorewind_ml',
          scores: data.scores,
        };
      }
    }
  } catch (err) {
    // If backend ML is offline, gracefully fall back to client-side detection
    console.info('Backend ML offline, falling back to browser detector:', err);
  }

  // 2. Fallback: browser COCO-SSD detection
  const m = await loadModel();
  let best = null;
  for (let i = 0; i < tries; i++) {
    const preds = await withTimeout(m.detect(video), DETECT_TIMEOUT_MS, 'Detection timed out');
    for (const p of preds) if (p.score >= 0.4 && (!best || p.score > best.score)) best = p;
    await new Promise(r => setTimeout(r, 250));
  }
  if (!best) return { key: 'rock', label: 'No recyclable object found', conf: 0, box: null, vw: video.videoWidth, vh: video.videoHeight };
  return { key: MAP[best.class] || 'rock', label: MAP[best.class] ? undefined : `${best.class} (not recyclable)`,
    conf: Math.round(best.score * 100), box: best.bbox, vw: video.videoWidth, vh: video.videoHeight };
}

