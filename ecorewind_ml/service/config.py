"""Environment-driven settings for the EcoRewind scanner service."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during early setup
    load_dotenv = None

SERVICE_DIR = Path(__file__).resolve().parent
ML_DIR = SERVICE_DIR.parent

DEFAULT_MODEL_PATH = ML_DIR / "outputs" / "ecorewind_classifier.keras"
DEFAULT_LABELS_PATH = ML_DIR / "outputs" / "labels.json"
DEFAULT_DB_PATH = SERVICE_DIR / "data" / "scanner.db"

DEFAULT_RATES = {"plastic_bottle": 2, "aluminium_can": 4, "plastic_bag": 1}
# serve.py on :8000 proxies the browser, Vite dev servers use :5173.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
DISPLAY_LABELS = {
    "plastic_bottle": "Plastic bottle",
    "plastic_bag": "Plastic bag",
    "aluminium_can": "Aluminium can",
    "other_invalid": "Invalid item",
}

if load_dotenv:
    load_dotenv(SERVICE_DIR / ".env")


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    # Relative paths in .env are relative to this folder, not the shell's cwd.
    return (path if path.is_absolute() else SERVICE_DIR / path).resolve()


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    return values or default


def _rates(name: str, default: dict[str, int]) -> dict[str, int]:
    raw = os.getenv(name)
    if not raw:
        return dict(default)
    rates: dict[str, int] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        try:
            label, rate = part.split(":", 1)
            label = label.strip()
            value = int(rate.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must look like plastic_bottle:2,aluminium_can:4") from exc
        if not label or value < 0:
            raise ValueError(f"Invalid rate entry for {label!r}")
        rates[label] = value
    return rates or dict(default)


@dataclass(frozen=True)
class Settings:
    model_path: Path
    labels_path: Path
    threshold: float = 0.75
    accepted_classes: frozenset[str] = frozenset(DEFAULT_RATES)
    rates_paise_per_gram: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RATES))
    min_weight_g: float = 100.0
    max_weight_g: float = 500.0
    bonus_threshold_g: float = 400.0
    bonus_pct: int = 10
    database_path: Path = DEFAULT_DB_PATH
    api_key: str | None = None
    admin_key: str | None = None
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    max_image_bytes: int = 8_000_000
    classifier_mode: str = "keras"
    fake_label: str = "other_invalid"
    fake_confidence: float = 0.0

    def display_label(self, label: str) -> str:
        return DISPLAY_LABELS.get(label, label.replace("_", " ").title())


def load_settings() -> Settings:
    rates = _rates("ECOREWIND_RATES", DEFAULT_RATES)
    mode = os.getenv("ECOREWIND_CLASSIFIER", "keras").strip().lower()
    if mode not in {"keras", "fake", "unavailable"}:
        raise ValueError("ECOREWIND_CLASSIFIER must be keras, fake, or unavailable")
    return Settings(
        model_path=_path("ECOREWIND_MODEL_PATH", DEFAULT_MODEL_PATH),
        labels_path=_path("ECOREWIND_LABELS_PATH", DEFAULT_LABELS_PATH),
        threshold=_float("ECOREWIND_THRESHOLD", 0.75),
        accepted_classes=frozenset(_csv("ECOREWIND_ACCEPTED_CLASSES", tuple(rates))),
        rates_paise_per_gram=rates,
        min_weight_g=_float("ECOREWIND_MIN_WEIGHT_G", 100.0),
        max_weight_g=_float("ECOREWIND_MAX_WEIGHT_G", 500.0),
        bonus_threshold_g=_float("ECOREWIND_BONUS_THRESHOLD_G", 400.0),
        bonus_pct=_int("ECOREWIND_BONUS_PCT", 10),
        database_path=_path("ECOREWIND_DB_PATH", DEFAULT_DB_PATH),
        api_key=os.getenv("ECOREWIND_API_KEY") or None,
        admin_key=os.getenv("ECOREWIND_ADMIN_KEY") or None,
        cors_origins=_csv("ECOREWIND_CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
        max_image_bytes=_int("ECOREWIND_MAX_IMAGE_BYTES", 8_000_000),
        classifier_mode=mode,
        fake_label=os.getenv("ECOREWIND_FAKE_LABEL", "other_invalid"),
        fake_confidence=_float("ECOREWIND_FAKE_CONFIDENCE", 0.0),
    )
