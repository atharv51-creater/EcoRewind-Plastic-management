"""
Build a clean, balanced 4-class dataset from raw downloads / archive dataset.

Classes: plastic_bottle, plastic_bag, aluminium_can, other_invalid

Usage:
    python scripts/prepare_dataset.py --raw ../archive --out data/dataset
"""
import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

from PIL import Image

CLASSES = ["plastic_bottle", "plastic_bag", "aluminium_can", "other_invalid"]
EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def valid_image(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return min(im.size) >= 64
    except Exception:
        return False


def locate_raw_dir(raw_arg: str) -> Path:
    candidates = [
        Path(raw_arg),
        Path("..") / raw_arg,
        Path("archive"),
        Path("../archive"),
        Path("archive/images/images"),
        Path("../archive/images/images"),
        Path("data/raw"),
    ]
    raw = None
    for c in candidates:
        if c.exists() and c.is_dir():
            raw = c
            break
    if raw is None:
        raise SystemExit(f"Raw folder not found: {raw_arg}")

    # Traverse nested image hierarchy if present (e.g. archive/images/images)
    if (raw / "images" / "images").is_dir():
        raw = raw / "images" / "images"
    elif (raw / "images").is_dir():
        sub = raw / "images"
        if (sub / "images").is_dir():
            raw = sub / "images"
        else:
            raw = sub
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="archive")
    ap.add_argument("--out", default="data/dataset")
    ap.add_argument("--mapping", default="mapping.json")
    ap.add_argument("--split", nargs=3, type=float, default=[0.70, 0.15, 0.15])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-per-class", type=int, default=1200, help="Target max images per class (0 = no limit)")
    args = ap.parse_args()

    mapping_path = Path(args.mapping)
    if not mapping_path.exists() and (Path("..") / args.mapping).exists():
        mapping_path = Path("..") / args.mapping

    mapping = {k.lower(): v for k, v in json.load(open(mapping_path)).items() if not k.startswith("_")}
    raw = locate_raw_dir(args.raw)
    out = Path(args.out)

    print(f"Loading raw data from: {raw}")
    print(f"Target classes: {CLASSES}")

    # Group files by target class and folder name to allow balanced subcategory sampling
    grouped = {c: {} for c in CLASSES}
    seen = set()
    skipped_unmapped = set()
    bad, dupes = 0, 0

    subfolders = sorted(p for p in raw.iterdir() if p.is_dir())
    for folder in subfolders:
        target = mapping.get(folder.name.lower())
        if target is None:
            skipped_unmapped.add(folder.name)
            continue
        if target not in CLASSES:
            raise SystemExit(f"mapping.json: '{target}' is not one of {CLASSES}")

        category_files = []
        for f in folder.rglob("*"):
            if f.suffix.lower() not in EXTS:
                continue
            if not valid_image(f):
                bad += 1
                continue
            h = file_hash(f)
            if h in seen:
                dupes += 1
                continue
            seen.add(h)
            category_files.append(f)

        grouped[target][folder.name] = category_files

    if skipped_unmapped:
        print("Ignored folders (not in mapping.json):", ", ".join(sorted(skipped_unmapped)))
    print(f"Corrupt/too-small images removed: {bad} | exact duplicates removed: {dupes}")

    empty = [c for c, sub_dict in grouped.items() if not any(sub_dict.values())]
    if empty:
        raise SystemExit(f"No images found for: {empty}. Check raw directory and mapping.json")

    rng = random.Random(args.seed)
    buckets = {c: [] for c in CLASSES}

    for cls, sub_dict in grouped.items():
        sub_names = list(sub_dict.keys())
        total_in_class = sum(len(v) for v in sub_dict.values())
        print(f"Class '{cls}': {len(sub_names)} subcategories, {total_in_class} images total")

        if args.max_per_class and total_in_class > args.max_per_class:
            # Stratified sampling across all subcategories in this class
            per_sub = max(1, args.max_per_class // len(sub_names))
            selected = []
            for name, files in sub_dict.items():
                rng.shuffle(files)
                selected.extend(files[:per_sub])
            # If slightly short due to integer division, fill from remaining
            if len(selected) < args.max_per_class:
                remaining = []
                for name, files in sub_dict.items():
                    remaining.extend(files[per_sub:])
                rng.shuffle(remaining)
                selected.extend(remaining[: (args.max_per_class - len(selected))])
            rng.shuffle(selected)
            buckets[cls] = selected
        else:
            all_files = []
            for files in sub_dict.values():
                all_files.extend(files)
            rng.shuffle(all_files)
            buckets[cls] = all_files

    if out.exists():
        shutil.rmtree(out)
    tr, va, _ = args.split

    print(f"\n{'class':<16}{'train':>8}{'val':>8}{'test':>8}{'total':>8}")
    for cls, files in buckets.items():
        n = len(files)
        n_tr, n_va = int(n * tr), int(n * va)
        parts = {"train": files[:n_tr], "val": files[n_tr : n_tr + n_va], "test": files[n_tr + n_va :]}
        for split, items in parts.items():
            d = out / split / cls
            d.mkdir(parents=True, exist_ok=True)
            for i, src in enumerate(items):
                with Image.open(src) as im:
                    im.convert("RGB").save(d / f"{cls}_{i:05d}.jpg", quality=92)
        print(f"{cls:<16}{len(parts['train']):>8}{len(parts['val']):>8}{len(parts['test']):>8}{n:>8}")

    counts = [len(v) for v in buckets.values()]
    print(f"\nFinal balance ratio: max={max(counts)} min={min(counts)} (ratio: {max(counts)/min(counts):.2f}x)")
    print(f"Done -> {out}")


if __name__ == "__main__":
    main()
