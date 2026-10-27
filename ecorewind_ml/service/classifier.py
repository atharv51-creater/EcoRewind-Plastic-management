"""Pluggable image classifier for the EcoRewind scanner."""
from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import DISPLAY_LABELS, Settings

IMAGE_SIZE = 224
Image.MAX_IMAGE_PIXELS = 25_000_000


class ModelUnavailable(RuntimeError):
    """Raised when the trained model or its runtime is not usable."""


class InvalidImage(ValueError):
    """Raised when the uploaded bytes are not a readable image."""


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float
    scores: dict[str, float]


class Classifier(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def detail(self) -> str: ...

    def classify(self, image_bytes: bytes, filename: str = "") -> Prediction: ...


def _load_rgb(image_bytes: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.verify()
        with Image.open(io.BytesIO(image_bytes)) as source:
            return source.convert("RGB").resize(
                (IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("The upload is not a valid image") from exc


class KerasClassifier:
    def __init__(self, model_path: Path, labels_path: Path, threshold: float):
        self.threshold = threshold
        self.detail = "Model loaded"
        try:
            import tensorflow as tf  # noqa: PLC0415 - optional heavy dependency
        except Exception as exc:  # pragma: no cover - depends on host Python
            raise ModelUnavailable(
                "TensorFlow is not installed. Use Python 3.11/3.12 and install "
                "requirements-model.txt."
            ) from exc

        if not model_path.is_file():
            raise ModelUnavailable(f"Model file not found: {model_path}")
        if not labels_path.is_file():
            raise ModelUnavailable(f"Labels file not found: {labels_path}")

        try:
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelUnavailable(f"Could not read labels file: {exc}") from exc
        if not isinstance(labels, list) or not labels or not all(
            isinstance(item, str) and item for item in labels
        ):
            raise ModelUnavailable("labels.json must be a non-empty list of strings")

        try:
            self.model = tf.keras.models.load_model(str(model_path), compile=False)
        except Exception as exc:
            raise ModelUnavailable(f"Could not load the Keras model: {exc}") from exc

        output_size = int(self.model.output_shape[-1])
        if output_size != len(labels):
            raise ModelUnavailable(
                f"Model outputs {output_size} classes but labels.json has {len(labels)}"
            )
        self.labels = labels
        # Requests run in a thread pool; one inference at a time keeps TF predictable.
        self._lock = threading.Lock()
        self.ready = True
        self.detail = f"Model loaded from {model_path}"

    def classify(self, image_bytes: bytes, filename: str = "") -> Prediction:
        image = _load_rgb(image_bytes)
        batch = np.asarray(image, dtype=np.float32)[None, ...]
        try:
            # A direct call is much cheaper than model.predict() for a single frame.
            with self._lock:
                probabilities = np.asarray(self.model(batch, training=False))[0]
        except Exception as exc:
            raise ModelUnavailable(f"Model inference failed: {exc}") from exc
        if probabilities.shape[0] != len(self.labels):
            raise ModelUnavailable("Model returned an unexpected number of scores")
        index = int(probabilities.argmax())
        scores = {
            label: round(float(probabilities[i]), 4)
            for i, label in enumerate(self.labels)
        }
        return Prediction(
            label=self.labels[index],
            confidence=round(float(probabilities[index]), 4),
            scores=scores,
        )


class FakeClassifier:
    """Test/dev-only classifier. Never use it to credit real money."""

    def __init__(self, label: str = "other_invalid", confidence: float = 0.0):
        self.label = label
        self.confidence = confidence
        self.ready = True
        self.detail = f"Fake classifier ({label} @ {confidence:.2f})"

    def classify(self, image_bytes: bytes, filename: str = "") -> Prediction:
        if not image_bytes:
            raise InvalidImage("The upload is empty")
        scores = {label: 0.0 for label in DISPLAY_LABELS}
        scores[self.label] = self.confidence
        return Prediction(self.label, self.confidence, scores)


class UnavailableClassifier:
    def __init__(self, detail: str):
        self.detail = detail
        self.ready = False

    def classify(self, image_bytes: bytes, filename: str = "") -> Prediction:
        raise ModelUnavailable(self.detail)


def build_classifier(settings: Settings) -> Classifier:
    if settings.classifier_mode == "fake":
        return FakeClassifier(settings.fake_label, settings.fake_confidence)
    if settings.classifier_mode == "unavailable":
        return UnavailableClassifier("Classifier disabled by configuration")
    try:
        return KerasClassifier(
            settings.model_path, settings.labels_path, settings.threshold
        )
    except ModelUnavailable as exc:
        return UnavailableClassifier(str(exc))
