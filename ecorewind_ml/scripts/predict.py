"""
Run the trained model on an image or the webcam and make the machine decision.

    python scripts/predict.py --image sample.jpg
    python scripts/predict.py --webcam
"""
import argparse
import json
from pathlib import Path

import numpy as np
from tensorflow import keras

IMG = 224
# Which detected classes this CLI reports as accepted. The web app's real rules
# (classes, rates, threshold) live in service/.env; keep this in step with them.
ACCEPTED = {"plastic_bottle", "aluminium_can", "plastic_bag"}


class Scanner:
    def __init__(self, model_dir="outputs", threshold=0.75):
        d = Path(model_dir)
        self.model = keras.models.load_model(d / "ecorewind_classifier.keras")
        self.labels = json.load(open(d / "labels.json"))
        self.threshold = threshold

    def classify(self, rgb_array):
        """rgb_array: HxWx3 uint8 (RGB). Returns a decision dict."""
        from PIL import Image
        img = Image.fromarray(rgb_array).resize((IMG, IMG))
        x = np.expand_dims(np.asarray(img, dtype="float32"), 0)
        probs = self.model.predict(x, verbose=0)[0]
        i = int(probs.argmax())
        label, conf = self.labels[i], float(probs[i])

        if conf < self.threshold:
            decision, reason = "rejected", f"Low confidence ({conf:.0%}). Rescan the item."
        elif label in ACCEPTED:
            decision, reason = "accepted", f"Detected {label.replace('_', ' ')}"
        else:
            decision, reason = "rejected", f"{label.replace('_', ' ')} is not accepted"
        return {
            "label": label,
            "confidence": round(conf, 4),
            "decision": decision,
            "reason": reason,
            "scores": {self.labels[k]: round(float(p), 4) for k, p in enumerate(probs)},
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--webcam", action="store_true")
    ap.add_argument("--model-dir", default="outputs")
    ap.add_argument("--threshold", type=float, default=0.75)
    args = ap.parse_args()
    scanner = Scanner(args.model_dir, args.threshold)

    if args.image:
        from PIL import Image
        arr = np.asarray(Image.open(args.image).convert("RGB"))
        print(json.dumps(scanner.classify(arr), indent=2))
        return

    if args.webcam:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise SystemExit("Could not open the webcam")
        print("Press SPACE to scan, Q to quit")
        text, color = "Press SPACE to scan", (255, 255, 255)
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            view = frame.copy()
            cv2.putText(view, text, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
            cv2.imshow("EcoRewind scanner", view)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if k == 32:
                r = scanner.classify(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                text = f"{r['decision'].upper()}: {r['label']} {r['confidence']:.0%}"
                color = (80, 200, 80) if r["decision"] == "accepted" else (60, 60, 230)
                print(json.dumps(r))
        cap.release()
        cv2.destroyAllWindows()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
