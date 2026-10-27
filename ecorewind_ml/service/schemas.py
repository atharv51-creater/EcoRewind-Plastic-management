"""Pydantic response schemas for the scanner service."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WalletOut(BaseModel):
    user_id: str
    balance_paise: int
    total_grams: float
    confirmed_count: int
    rejected_count: int
    total_paid_paise: int
    created_at: str | None
    updated_at: str | None


class TransactionOut(BaseModel):
    id: int
    kind: str
    status: str
    label: str | None
    display_label: str | None
    confidence: float | None
    weight_grams: float
    rate_paise_per_gram: int
    payout_paise: int
    bonus_paise: int
    reason: str | None
    machine_id: str | None
    scores: dict[str, float]
    created_at: str


class ScanOut(BaseModel):
    scan_id: int
    duplicate: bool = False
    decision: str
    reason: str
    label: str
    display_label: str
    confidence: float
    scores: dict[str, float]
    transaction: TransactionOut
    wallet: WalletOut
    model: str


class ClassifyOut(BaseModel):
    """Live preview only. `decision` ignores weight; POST /scan makes the real call."""

    decision: str
    reason: str
    label: str
    display_label: str
    confidence: float
    scores: dict[str, float]
    rate_paise_per_gram: int
    model: str


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class WithdrawalCreate(BaseModel):
    amount_paise: int = Field(gt=0, le=100_000_000)
    destination: str = Field(min_length=3, max_length=120)


class WithdrawalOut(BaseModel):
    id: int
    user_id: str
    amount_paise: int
    destination: str
    status: str
    created_at: str
    updated_at: str


class WithdrawalPage(BaseModel):
    items: list[WithdrawalOut]
    total: int
    limit: int
    offset: int


class WithdrawalResult(BaseModel):
    duplicate: bool = False
    withdrawal: WithdrawalOut
    wallet: WalletOut


class RatesOut(BaseModel):
    rates_paise_per_gram: dict[str, int]
    accepted_classes: list[str]
    min_weight_g: float
    max_weight_g: float
    bonus_threshold_g: float
    bonus_pct: int


class HealthOut(BaseModel):
    status: str
    model_ready: bool
    model_detail: str
    model_path: str
    labels_path: str
    threshold: float
    accepted_classes: list[str]
    database_path: str
    version: str
