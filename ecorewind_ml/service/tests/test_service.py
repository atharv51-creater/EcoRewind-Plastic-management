from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from app import create_app  # noqa: E402
from classifier import FakeClassifier, Prediction, UnavailableClassifier  # noqa: E402
from config import Settings  # noqa: E402


def image_bytes(color: tuple[int, int, int] = (30, 110, 70)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, "PNG")
    return buffer.getvalue()


def settings_for(tmp_path: Path, **overrides) -> Settings:
    values = {
        "model_path": tmp_path / "missing.keras",
        "labels_path": tmp_path / "missing.json",
        "database_path": tmp_path / "test.db",
        "threshold": 0.75,
        "accepted_classes": frozenset({"plastic_bottle", "aluminium_can"}),
        "rates_paise_per_gram": {"plastic_bottle": 2, "aluminium_can": 4},
        "min_weight_g": 100.0,
        "max_weight_g": 500.0,
        "bonus_threshold_g": 400.0,
        "bonus_pct": 10,
        "api_key": None,
        "admin_key": "admin-secret",
        "cors_origins": ("http://localhost:5173",),
    }
    values.update(overrides)
    return Settings(**values)


def client_for(
    tmp_path: Path,
    label: str = "plastic_bottle",
    confidence: float = 0.95,
    **overrides,
) -> TestClient:
    app = create_app(
        settings=settings_for(tmp_path, **overrides),
        classifier=FakeClassifier(label, confidence),
    )
    return TestClient(app)


def scan(
    client: TestClient,
    *,
    user_id: str = "user-1",
    weight: float = 250,
    key: str = "scan-key-1",
    image: bytes | None = None,
    machine: str = "rvm-1",
):
    return client.post(
        "/scan",
        data={
            "user_id": user_id,
            "weight_grams": str(weight),
            "machine_id": machine,
        },
        files={"image": ("frame.png", image or image_bytes(), "image/png")},
        headers={"Idempotency-Key": key},
    )


def test_health_reports_fake_classifier_ready(tmp_path: Path) -> None:
    response = client_for(tmp_path).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_ready"] is True
    assert body["accepted_classes"] == ["aluminium_can", "plastic_bottle"]


def test_accepted_bottle_credits_wallet(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "accepted"
    assert body["label"] == "plastic_bottle"
    assert body["transaction"]["payout_paise"] == 500
    assert body["transaction"]["bonus_paise"] == 0
    assert body["wallet"]["balance_paise"] == 500
    assert body["wallet"]["total_grams"] == 250.0
    assert body["wallet"]["confirmed_count"] == 1
    assert body["wallet"]["rejected_count"] == 0


def test_heavy_bottle_gets_bonus(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path), weight=450)
    body = response.json()
    assert response.status_code == 200
    assert body["transaction"]["payout_paise"] == 990
    assert body["transaction"]["bonus_paise"] == 90
    assert body["wallet"]["balance_paise"] == 990


def test_plastic_bag_is_rejected_without_credit(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path, "plastic_bag", 0.97))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "rejected"
    assert "not accepted" in body["reason"]
    assert body["transaction"]["payout_paise"] == 0
    assert body["wallet"]["balance_paise"] == 0
    assert body["wallet"]["rejected_count"] == 1


def test_low_confidence_is_rejected(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path, "plastic_bottle", 0.4))
    body = response.json()
    assert body["decision"] == "rejected"
    assert "Low confidence" in body["reason"]
    assert body["wallet"]["balance_paise"] == 0


def test_low_weight_is_rejected_even_if_model_accepts(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path), weight=50)
    body = response.json()
    assert body["decision"] == "rejected"
    assert "minimum" in body["reason"]
    assert body["wallet"]["balance_paise"] == 0
    assert body["wallet"]["rejected_count"] == 1


def test_invalid_image_is_rejected_before_classification(tmp_path: Path) -> None:
    response = scan(client_for(tmp_path), image=b"not-an-image")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_IMAGE"
    wallet = client_for(tmp_path).get("/users/user-1/wallet").json()
    assert wallet["balance_paise"] == 0


def test_missing_idempotency_key_is_rejected(tmp_path: Path) -> None:
    response = client_for(tmp_path).post(
        "/scan",
        data={"user_id": "user-1", "weight_grams": "250"},
        files={"image": ("frame.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_idempotent_replay_does_not_double_credit(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    first = scan(client, key="same-key")
    second = scan(client, key="same-key")
    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["scan_id"] == first.json()["scan_id"]
    assert second.json()["wallet"]["balance_paise"] == 500
    history = client.get("/users/user-1/transactions").json()
    assert history["total"] == 1


def test_idempotency_conflict_with_different_payload(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert scan(client, key="same-key", weight=250).status_code == 200
    response = scan(client, key="same-key", weight=260)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get("/users/user-1/wallet").json()["balance_paise"] == 500


def test_model_unavailable_fails_closed(tmp_path: Path) -> None:
    app = create_app(
        settings=settings_for(tmp_path),
        classifier=UnavailableClassifier("Model is not trained yet"),
    )
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert health["model_ready"] is False
    response = scan(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MODEL_UNAVAILABLE"
    history = client.get("/users/user-1/transactions").json()
    assert history["total"] == 0


def test_api_key_is_enforced_when_configured(tmp_path: Path) -> None:
    client = client_for(tmp_path, api_key="machine-secret")
    missing = scan(client)
    assert missing.status_code == 401
    allowed = client.post(
        "/scan",
        data={"user_id": "user-1", "weight_grams": "250"},
        files={"image": ("frame.png", image_bytes(), "image/png")},
        headers={"Idempotency-Key": "key", "X-API-Key": "machine-secret"},
    )
    assert allowed.status_code == 200


def test_transaction_filters_and_pagination(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert scan(client, key="k1").status_code == 200
    bag_client = client_for(tmp_path, "plastic_bag", 0.95)
    assert scan(bag_client, key="k2").status_code == 200
    page = client.get("/users/user-1/transactions?status=accepted").json()
    assert page["total"] == 1
    assert page["items"][0]["status"] == "accepted"
    paged = client.get("/users/user-1/transactions?limit=1&offset=1").json()
    assert paged["total"] == 2
    assert len(paged["items"]) == 1


def test_unknown_wallet_is_zero_not_an_error(tmp_path: Path) -> None:
    response = client_for(tmp_path).get("/users/nobody/wallet")
    assert response.status_code == 200
    body = response.json()
    assert body["balance_paise"] == 0
    assert body["confirmed_count"] == 0


def test_withdrawal_lifecycle_and_idempotency(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert scan(client, weight=250).status_code == 200
    headers = {"Idempotency-Key": "withdraw-1"}
    created = client.post(
        "/users/user-1/withdrawals",
        json={"amount_paise": 200, "destination": "demo@upi"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["withdrawal"]["status"] == "pending"
    assert body["wallet"]["balance_paise"] == 300

    duplicate = client.post(
        "/users/user-1/withdrawals",
        json={"amount_paise": 200, "destination": "demo@upi"},
        headers=headers,
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["wallet"]["balance_paise"] == 300

    too_much = client.post(
        "/users/user-1/withdrawals",
        json={"amount_paise": 400, "destination": "demo@upi"},
        headers={"Idempotency-Key": "withdraw-2"},
    )
    assert too_much.status_code == 409
    assert too_much.json()["detail"]["code"] == "INSUFFICIENT_FUNDS"

    completed = client.post(
        f"/admin/withdrawals/{body['withdrawal']['id']}/complete",
        headers={"X-Admin-Key": "admin-secret"},
    )
    assert completed.status_code == 200
    assert completed.json()["withdrawal"]["status"] == "paid"
    assert completed.json()["wallet"]["balance_paise"] == 300

    again = client.post(
        f"/admin/withdrawals/{body['withdrawal']['id']}/complete",
        headers={"X-Admin-Key": "admin-secret"},
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True


def test_cancelled_withdrawal_refunds_balance(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert scan(client, weight=250).status_code == 200
    created = client.post(
        "/users/user-1/withdrawals",
        json={"amount_paise": 100, "destination": "demo@upi"},
        headers={"Idempotency-Key": "withdraw-cancel"},
    )
    withdrawal_id = created.json()["withdrawal"]["id"]
    assert created.json()["wallet"]["balance_paise"] == 400
    cancelled = client.post(
        f"/admin/withdrawals/{withdrawal_id}/cancel",
        headers={"X-Admin-Key": "admin-secret"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["withdrawal"]["status"] == "cancelled"
    assert cancelled.json()["wallet"]["balance_paise"] == 500
    paid_after_cancel = client.post(
        f"/admin/withdrawals/{withdrawal_id}/complete",
        headers={"X-Admin-Key": "admin-secret"},
    )
    assert paid_after_cancel.status_code == 409


def test_admin_endpoints_require_admin_key(tmp_path: Path) -> None:
    client = client_for(tmp_path, admin_key=None)
    response = client.post("/admin/withdrawals/1/cancel")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_DISABLED"


def test_rates_are_public(tmp_path: Path) -> None:
    body = client_for(tmp_path).get("/rates").json()
    assert body["rates_paise_per_gram"]["plastic_bottle"] == 2
    assert body["rates_paise_per_gram"]["aluminium_can"] == 4
    assert body["min_weight_g"] == 100.0


def classify(client: TestClient, image: bytes | None = None):
    return client.post(
        "/classify",
        files={"image": ("frame.png", image or image_bytes(), "image/png")},
    )


def test_classify_previews_without_touching_the_ledger(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = classify(client)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "accepted"
    assert body["label"] == "plastic_bottle"
    assert body["rate_paise_per_gram"] == 2
    wallet = client.get("/users/user-1/wallet").json()
    assert wallet["balance_paise"] == 0
    assert wallet["rejected_count"] == 0
    assert client.get("/users/user-1/transactions").json()["total"] == 0


def test_classify_uses_the_same_class_rules_as_scan(tmp_path: Path) -> None:
    low = classify(client_for(tmp_path, "plastic_bottle", 0.4)).json()
    assert low["decision"] == "rejected"
    assert "Low confidence" in low["reason"]
    assert low["rate_paise_per_gram"] == 0
    unpaid = classify(client_for(tmp_path, "plastic_bag", 0.97)).json()
    assert unpaid["decision"] == "rejected"
    assert "not accepted" in unpaid["reason"]


def test_classify_rejects_bad_uploads_and_missing_model(tmp_path: Path) -> None:
    assert classify(client_for(tmp_path), image=b"not an image").status_code == 400
    app = create_app(
        settings=settings_for(tmp_path),
        classifier=UnavailableClassifier("offline"),
    )
    response = classify(TestClient(app))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MODEL_UNAVAILABLE"


def test_classify_requires_api_key_when_configured(tmp_path: Path) -> None:
    client = client_for(tmp_path, api_key="machine-secret")
    assert classify(client).status_code == 401


def test_default_settings_pay_bags_and_allow_local_frontend(monkeypatch) -> None:
    from config import load_settings

    for name in ("ECOREWIND_RATES", "ECOREWIND_ACCEPTED_CLASSES", "ECOREWIND_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert settings.rates_paise_per_gram == {
        "plastic_bottle": 2,
        "aluminium_can": 4,
        "plastic_bag": 1,
    }
    assert settings.accepted_classes == {"plastic_bottle", "aluminium_can", "plastic_bag"}
    assert "http://127.0.0.1:8000" in settings.cors_origins
    assert "http://localhost:8000" in settings.cors_origins


def test_relative_env_paths_resolve_from_the_service_folder(monkeypatch, tmp_path: Path) -> None:
    from config import SERVICE_DIR, load_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECOREWIND_DB_PATH", "./data/other.db")
    assert load_settings().database_path == (SERVICE_DIR / "data" / "other.db").resolve()
