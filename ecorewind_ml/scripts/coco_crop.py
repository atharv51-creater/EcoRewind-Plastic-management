"""
Convert a COCO-format detection dataset (e.g. TACO, Open Images exports) into
cropped per-category image folders that prepare_dataset.py can read.

Usage:
    python scripts/coco_crop.py --images data/taco/data --ann data/taco/data/annotations.json --out data/raw/taco
"""
import argparse
import json
from pathlib import Path

from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--ann", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-size", type=int, default=64)
    ap.add_argument("--pad", type=float, default=0.08, help="padding around the box, as a fraction")
    args = ap.parse_args()

    coco = json.load(open(args.ann))
    cats = {c["id"]: c["name"].lower().strip() for c in coco["categories"]}
    imgs = {i["id"]: i for i in coco["images"]}
    out = Path(args.out)
    n = 0
    for a in coco["annotations"]:
        info = imgs[a["image_id"]]
        path = Path(args.images) / info["file_name"]
        if not path.exists():
            continue
        x, y, w, h = a["bbox"]
        if min(w, h) < args.min_size:
            continue
        with Image.open(path) as im:
            im = im.convert("RGB")
            px, py = w * args.pad, h * args.pad
            box = (max(0, x - px), max(0, y - py), min(im.width, x + w + px), min(im.height, y + h + py))
            d = out / cats[a["category_id"]]
            d.mkdir(parents=True, exist_ok=True)
            im.crop(tuple(int(v) for v in box)).save(d / f"{path.stem}_{a['id']}.jpg", quality=92)
            n += 1
    print(f"Saved {n} crops to {out}")


if __name__ == "__main__":
    main()
