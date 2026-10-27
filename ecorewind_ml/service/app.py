"""EcoRewind scanner service.

The model classifies an uploaded frame. Only the classes in
ECOREWIND_ACCEPTED_CLASSES with a positive rate are paid, and the wallet credit
happens in the same SQLite transaction as the deposit record. Retries are
protected by Idempotency-Key. POST /classify is a ledger-free preview for the
live camera feed; only POST /scan moves money.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import math
import re
from decimal import Decimal, ROUND_HALF_UP

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from classifier import (
    Classifier,
    InvalidImage,
    ModelUnavailable,
    Prediction,
    build_classifier,
)
from config import DISPLAY_LABELS, Settings, load_settings
from schemas import (
    ClassifyOut,
    HealthOut,
    RatesOut,
    ScanOut,
    TransactionPage,
    WalletOut,
    WithdrawalCreate,
    WithdrawalPage,
    WithdrawalResult,
)
from wallet import (
    IdempotencyConflict,
    InsufficientFunds,
    WalletStore,
    WithdrawalStateError,
)

APP_VERSION = "1.0.0"
USER_ID_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")


def _payout(settings: Settings, weight_grams: float, rate: int) -> tuple[int, int]:
    base = int(
        (Decimal(str(weight_grams)) * Decimal(rate)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    bonus = 0
    if weight_grams >= settings.bonus_threshold_g:
        bonus = int(
            (Decimal(base) * Decimal(settings.bonus_pct) / Decimal(100)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    return base, bonus


def _reject(reason: str) -> dict:
    return {"decision": "rejected", "reason": reason, "rate": 0, "base": 0, "bonus": 0}


def _class_rejection(settings: Settings, prediction: Prediction) -> dict | None:
    """Checks that do not depend on weight. Returns a rejection, or None if payable."""
    label = prediction.label
    display = settings.display_label(label)
    confidence = prediction.confidence
    if label not in DISPLAY_LABELS:
        return _reject(f"Unknown class {label!r}; rescan the item.")
    if confidence < settings.threshold:
        return _reject(f"Low confidence ({confidence:.0%}). Rescan the item.")
    if label not in settings.accepted_classes:
        return _reject(f"{display} is not accepted by this machine.")
    if settings.rates_paise_per_gram.get(label, 0) <= 0:
        return _reject(f"No payout rate is configured for {display}.")
    return None


def _evaluate(settings: Settings, prediction: Prediction, weight_grams: float) -> dict:
    rejection = _class_rejection(settings, prediction)
    if rejection:
        return rejection
    display = settings.display_label(prediction.label)
    if (
        not math.isfinite(weight_grams)
        or weight_grams < 0
        or weight_grams > settings.max_weight_g
    ):
        return _reject(
            f"Invalid weight reading. The load cell must be between "
            f"0 and {settings.max_weight_g:g} g."
        )
    if weight_grams < settings.min_weight_g:
        return _reject(
            f"{weight_grams:g} g is under the {settings.min_weight_g:g} g "
            "minimum. Payout blocked."
        )
    rate = settings.rates_paise_per_gram[prediction.label]
    base, bonus = _payout(settings, weight_grams, rate)
    return {
        "decision": "accepted",
        "reason": f"{display}, {weight_grams:g} g accepted.",
        "rate": rate,
        "base": base,
        "bonus": bonus,
    }


def _image_sha(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def _request_hash(*parts: object) -> str:
    joined = "\0".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _validate_image(image_bytes: bytes) -> None:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_IMAGE", "message": "The upload is not a valid image"},
        ) from exc


async def _read_image(settings: Settings, image: UploadFile) -> bytes:
    image_bytes = await image.read(settings.max_image_bytes + 1)
    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_IMAGE", "message": "The image is empty"},
        )
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "IMAGE_TOO_LARGE",
                "message": f"Images must be under {settings.max_image_bytes} bytes",
            },
        )
    _validate_image(image_bytes)
    return image_bytes


async def _predict(classifier: Classifier, image_bytes: bytes, filename: str) -> Prediction:
    # Inference blocks for tens of milliseconds; keep it off the event loop.
    try:
        return await run_in_threadpool(classifier.classify, image_bytes, filename)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_UNAVAILABLE", "message": str(exc)},
        ) from exc
    except InvalidImage as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_IMAGE", "message": str(exc)},
        ) from exc


def _validate_user_id(user_id: str) -> str:
    user_id = user_id.strip()
    if not USER_ID_RE.fullmatch(user_id):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_USER_ID",
                "message": "user_id must be 1-64 characters: letters, digits, . _ @ -",
            },
        )
    return user_id


def _validate_idempotency(key: str | None) -> str:
    if not key or not IDEMPOTENCY_RE.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Send an Idempotency-Key header (1-128 safe characters)",
            },
        )
    return key


def create_app(
    settings: Settings | None = None,
    classifier: Classifier | None = None,
    store: WalletStore | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    store = store or WalletStore(settings.database_path)
    store.init()
    classifier = classifier or build_classifier(settings)

    app = FastAPI(
        title="EcoRewind Scanner Service",
        version=APP_VERSION,
        description="4-class waste classifier plus an atomic wallet ledger.",
    )
    app.state.settings = settings
    app.state.classifier = classifier
    app.state.store = store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def api_key_guard(request: Request) -> None:
        expected = settings.api_key
        if not expected:
            return
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-API-Key"},
            )

    def admin_key_guard(request: Request) -> None:
        if not settings.admin_key:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ADMIN_DISABLED",
                    "message": "Set ECOREWIND_ADMIN_KEY to use admin endpoints",
                },
            )
        supplied = request.headers.get("X-Admin-Key", "")
        if not hmac.compare_digest(supplied, settings.admin_key):
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Admin-Key"},
            )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Check the request fields",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.get("/health", response_model=HealthOut)
    def health(request: Request) -> dict:
        state = request.app.state
        return {
            "status": "ok" if state.classifier.ready else "degraded",
            "model_ready": state.classifier.ready,
            "model_detail": state.classifier.detail,
            "model_path": str(settings.model_path),
            "labels_path": str(settings.labels_path),
            "threshold": settings.threshold,
            "accepted_classes": sorted(settings.accepted_classes),
            "database_path": str(settings.database_path),
            "version": APP_VERSION,
        }

    @app.get("/rates", response_model=RatesOut)
    def rates() -> dict:
        return {
            "rates_paise_per_gram": settings.rates_paise_per_gram,
            "accepted_classes": sorted(settings.accepted_classes),
            "min_weight_g": settings.min_weight_g,
            "max_weight_g": settings.max_weight_g,
            "bonus_threshold_g": settings.bonus_threshold_g,
            "bonus_pct": settings.bonus_pct,
        }

    @app.post(
        "/classify",
        response_model=ClassifyOut,
        dependencies=[Depends(api_key_guard)],
    )
    async def classify(request: Request, image: UploadFile = File(...)) -> dict:
        """Preview for the live camera feed. Nothing is written to the ledger."""
        state = request.app.state
        image_bytes = await _read_image(settings, image)
        prediction = await _predict(state.classifier, image_bytes, image.filename or "")
        rejection = _class_rejection(settings, prediction)
        display = settings.display_label(prediction.label)
        return {
            "decision": "rejected" if rejection else "accepted",
            "reason": rejection["reason"] if rejection else f"{display} detected.",
            "label": prediction.label,
            "display_label": display,
            "confidence": prediction.confidence,
            "scores": prediction.scores,
            "rate_paise_per_gram": 0
            if rejection
            else settings.rates_paise_per_gram[prediction.label],
            "model": state.classifier.detail,
        }

    @app.post(
        "/scan",
        response_model=ScanOut,
        dependencies=[Depends(api_key_guard)],
    )
    async def scan(
        request: Request,
        image: UploadFile = File(...),
        user_id: str = Form(...),
        weight_grams: float = Form(...),
        machine_id: str = Form(""),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        state = request.app.state
        user_id = _validate_user_id(user_id)
        key = _validate_idempotency(idempotency_key)

        image_bytes = await _read_image(settings, image)
        image_sha = _image_sha(image_bytes)
        prediction = await _predict(state.classifier, image_bytes, image.filename or "")

        decision = _evaluate(settings, prediction, weight_grams)
        payout = decision["base"] + decision["bonus"]
        request_hash = _request_hash(user_id, weight_grams, machine_id, image_sha)
        try:
            transaction, wallet, duplicate = await run_in_threadpool(
                state.store.record_scan,
                user_id=user_id,
                status=decision["decision"],
                label=prediction.label,
                display_label=settings.display_label(prediction.label),
                confidence=prediction.confidence,
                weight_grams=weight_grams,
                rate_paise_per_gram=decision["rate"],
                payout_paise=payout,
                bonus_paise=decision["bonus"],
                reason=decision["reason"],
                machine_id=machine_id.strip() or None,
                image_sha256=image_sha,
                scores=prediction.scores,
                idempotency_key=key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc

        if duplicate:
            decision = {
                "decision": transaction["status"],
                "reason": transaction["reason"] or "",
            }
        return {
            "scan_id": transaction["id"],
            "duplicate": duplicate,
            "decision": decision["decision"],
            "reason": decision["reason"],
            "label": transaction["label"] or prediction.label,
            "display_label": transaction["display_label"]
            or settings.display_label(prediction.label),
            "confidence": transaction["confidence"]
            if transaction["confidence"] is not None
            else prediction.confidence,
            "scores": transaction["scores"] or prediction.scores,
            "transaction": transaction,
            "wallet": wallet,
            "model": state.classifier.detail,
        }

    @app.get("/users/{user_id}/wallet", response_model=WalletOut)
    def get_wallet(request: Request, user_id: str) -> dict:
        return request.app.state.store.get_wallet(_validate_user_id(user_id))

    @app.get("/users/{user_id}/transactions", response_model=TransactionPage)
    def transactions(
        request: Request,
        user_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        status: str | None = Query(default=None, pattern="^(accepted|rejected)$"),
    ) -> dict:
        items, total = request.app.state.store.list_transactions(
            _validate_user_id(user_id), limit=limit, offset=offset, status=status
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.post(
        "/users/{user_id}/withdrawals",
        response_model=WithdrawalResult,
        status_code=201,
    )
    def create_withdrawal(
        request: Request,
        user_id: str,
        body: WithdrawalCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        user_id = _validate_user_id(user_id)
        key = _validate_idempotency(idempotency_key)
        destination = body.destination.strip()
        if any(ord(char) < 32 for char in destination):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_DESTINATION",
                    "message": "destination must not contain control characters",
                },
            )
        request_hash = _request_hash(user_id, body.amount_paise, destination)
        try:
            withdrawal, wallet, duplicate = request.app.state.store.create_withdrawal(
                user_id=user_id,
                amount_paise=body.amount_paise,
                destination=destination,
                idempotency_key=key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
            ) from exc
        except InsufficientFunds as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "INSUFFICIENT_FUNDS", "message": str(exc)},
            ) from exc
        return {"duplicate": duplicate, "withdrawal": withdrawal, "wallet": wallet}

    @app.get("/users/{user_id}/withdrawals", response_model=WithdrawalPage)
    def withdrawals(
        request: Request,
        user_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        items, total = request.app.state.store.list_withdrawals(
            _validate_user_id(user_id), limit=limit, offset=offset
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.post(
        "/admin/withdrawals/{withdrawal_id}/complete",
        response_model=WithdrawalResult,
        dependencies=[Depends(admin_key_guard)],
    )
    def complete_withdrawal(request: Request, withdrawal_id: int) -> dict:
        try:
            withdrawal, wallet, duplicate = (
                request.app.state.store.complete_withdrawal(withdrawal_id)
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": str(exc)},
            ) from exc
        except WithdrawalStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "WITHDRAWAL_STATE", "message": str(exc)},
            ) from exc
        return {"duplicate": duplicate, "withdrawal": withdrawal, "wallet": wallet}

    @app.post(
        "/admin/withdrawals/{withdrawal_id}/cancel",
        response_model=WithdrawalResult,
        dependencies=[Depends(admin_key_guard)],
    )
    def cancel_withdrawal(request: Request, withdrawal_id: int) -> dict:
        try:
            withdrawal, wallet, duplicate = (
                request.app.state.store.cancel_withdrawal(withdrawal_id)
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": str(exc)},
            ) from exc
        except WithdrawalStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "WITHDRAWAL_STATE", "message": str(exc)},
            ) from exc
        return {"duplicate": duplicate, "withdrawal": withdrawal, "wallet": wallet}

    return app


app = create_app()
