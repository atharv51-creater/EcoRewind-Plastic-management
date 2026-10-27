"""
Export the trained model so terminal.html can run it in the browser with TensorFlow.js.

    python scripts/export_tfjs.py --model outputs/ecorewind_classifier.keras --out outputs/tfjs_model
"""
import argparse
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="outputs/ecorewind_classifier.keras")
ap.add_argument("--out", default="outputs/tfjs_model")
a = ap.parse_args()

# tensorflowjs_converter reads Keras files directly (use tensorflowjs>=4.x)
cmd = ["tensorflowjs_converter", "--input_format=keras", a.model, a.out]
print(" ".join(cmd))
sys.exit(subprocess.call(cmd))
