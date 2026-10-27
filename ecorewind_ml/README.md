# EcoRewind Scanner: 4-class waste classifier

Classes: `plastic_bottle`, `plastic_bag`, `aluminium_can`, `other_invalid`

## 1. Setup
    python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
    pip install -r requirements.txt

## 2. Get data (see the dataset list in the chat reply)
Put each download under `data/raw/<source>/<class_folder>/*.jpg`, or use the
crop helper for COCO-style datasets:
    python scripts/coco_crop.py --images data/taco/data --ann data/taco/data/annotations.json --out data/raw/taco

Edit `mapping.json` so every raw folder name points at one of the 4 classes.

## 3. Build the dataset (cleans, dedupes, splits 70/15/15)
    python scripts/prepare_dataset.py --raw data/raw --out data/dataset

Aim for at least 500 images per class; 1,000+ is better. Add your own photos taken
with the machine's real camera and lighting. This improves accuracy more than anything else.

## 4. Train
    python scripts/train.py --data data/dataset --out outputs
Outputs: `ecorewind_classifier.keras`, `labels.json`, `training_curves.png`,
`confusion_matrix.png`, `classification_report.txt`.

## 5. Test
    python scripts/predict.py --image path/to/photo.jpg
    python scripts/predict.py --webcam

## 6. Use in the web terminal
The web app calls this model through the scanner service in `service/`
(FastAPI + SQLite wallet ledger). `serve.py` in the project root starts it
automatically and proxies `/api/*` to it, so normally you only run
`./start.sh` (or `start.bat`) from the project root. To run the service on
its own:

    pip install -r service/requirements.txt -r service/requirements-model.txt
    cd service && ./run.sh            # http://127.0.0.1:8100/docs

Service tests: `pip install -r service/requirements-dev.txt`, then
`cd service && python -m pytest -q tests`.

`scripts/export_tfjs.py` still exports a TensorFlow.js copy if you want to run
the model in the browser instead, but the web terminal no longer does that.

## Decision rule
The service's `.env` (see `service/.env.example`) is the single source of truth:
confidence below `ECOREWIND_THRESHOLD` (0.75) is rejected as "rescan", and only
classes in `ECOREWIND_ACCEPTED_CLASSES` with a rate in `ECOREWIND_RATES` are paid.
Defaults: plastic bottle 2 paise/g, aluminium can 4 paise/g, plastic bag 1 paise/g.
The 100 g minimum, 500 g maximum and 10% bonus at 400 g are applied by the
service after this check. The web terminal reads all of these from `GET /rates`.
