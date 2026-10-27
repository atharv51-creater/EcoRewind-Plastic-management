"""SQLite wallet ledger for the scanner service.

Money is always stored as integer paise. Every scan and withdrawal is recorded
inside a single BEGIN IMMEDIATE transaction, and idempotency keys make retries safe.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    user_id TEXT PRIMARY KEY,
    balance_paise INTEGER NOT NULL DEFAULT 0 CHECK (balance_paise >= 0),
    total_grams REAL NOT NULL DEFAULT 0 CHECK (total_grams >= 0),
    confirmed_count INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    total_paid_paise INTEGER NOT NULL DEFAULT 0 CHECK (total_paid_paise >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('deposit')),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
    label TEXT,
    display_label TEXT,
    confidence REAL,
    weight_grams REAL NOT NULL,
    rate_paise_per_gram INTEGER NOT NULL DEFAULT 0,
    payout_paise INTEGER NOT NULL DEFAULT 0 CHECK (payout_paise >= 0),
    bonus_paise INTEGER NOT NULL DEFAULT 0 CHECK (bonus_paise >= 0),
    reason TEXT,
    machine_id TEXT,
    image_sha256 TEXT,
    scores_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_transactions_user_created
    ON transactions (user_id, id DESC);

CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    destination TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'cancelled')),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_withdrawals_user_created
    ON withdrawals (user_id, id DESC);
"""


class WalletError(RuntimeError):
    pass


class IdempotencyConflict(WalletError):
    pass


class InsufficientFunds(WalletError):
    pass


class WithdrawalStateError(WalletError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _wallet_dict(row: sqlite3.Row | None, user_id: str) -> dict:
    if row is None:
        return {
            "user_id": user_id,
            "balance_paise": 0,
            "total_grams": 0.0,
            "confirmed_count": 0,
            "rejected_count": 0,
            "total_paid_paise": 0,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "user_id": row["user_id"],
        "balance_paise": row["balance_paise"],
        "total_grams": float(row["total_grams"]),
        "confirmed_count": row["confirmed_count"],
        "rejected_count": row["rejected_count"],
        "total_paid_paise": row["total_paid_paise"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _txn_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "label": row["label"],
        "display_label": row["display_label"],
        "confidence": row["confidence"],
        "weight_grams": float(row["weight_grams"]),
        "rate_paise_per_gram": row["rate_paise_per_gram"],
        "payout_paise": row["payout_paise"],
        "bonus_paise": row["bonus_paise"],
        "reason": row["reason"],
        "machine_id": row["machine_id"],
        "scores": json.loads(row["scores_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _withdrawal_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "amount_paise": row["amount_paise"],
        "destination": row["destination"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class WalletStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(SCHEMA)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_wallet(connection: sqlite3.Connection, user_id: str, now: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO wallets (user_id, created_at, updated_at) VALUES (?, ?, ?)",
            (user_id, now, now),
        )

    @staticmethod
    def _wallet(connection: sqlite3.Connection, user_id: str) -> dict:
        row = connection.execute(
            "SELECT * FROM wallets WHERE user_id = ?", (user_id,)
        ).fetchone()
        return _wallet_dict(row, user_id)

    def get_wallet(self, user_id: str) -> dict:
        connection = self._connect()
        try:
            return self._wallet(connection, user_id)
        finally:
            connection.close()

    def record_scan(
        self,
        *,
        user_id: str,
        status: str,
        label: str | None,
        display_label: str | None,
        confidence: float | None,
        weight_grams: float,
        rate_paise_per_gram: int,
        payout_paise: int,
        bonus_paise: int,
        reason: str,
        machine_id: str | None,
        image_sha256: str,
        scores: dict[str, float],
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[dict, dict, bool]:
        now = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM transactions WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "This Idempotency-Key was already used with different scan data"
                    )
                wallet = self._wallet(connection, user_id)
                connection.commit()
                return _txn_dict(existing), wallet, True

            self._ensure_wallet(connection, user_id, now)
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    user_id, kind, status, label, display_label, confidence,
                    weight_grams, rate_paise_per_gram, payout_paise, bonus_paise,
                    reason, machine_id, image_sha256, scores_json, idempotency_key,
                    request_hash, created_at
                ) VALUES (?, 'deposit', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    status,
                    label,
                    display_label,
                    confidence,
                    weight_grams,
                    rate_paise_per_gram,
                    payout_paise,
                    bonus_paise,
                    reason,
                    machine_id,
                    image_sha256,
                    json.dumps(scores, sort_keys=True),
                    idempotency_key,
                    request_hash,
                    now,
                ),
            )
            if status == "accepted":
                connection.execute(
                    """
                    UPDATE wallets SET
                        balance_paise = balance_paise + ?,
                        total_grams = total_grams + ?,
                        confirmed_count = confirmed_count + 1,
                        total_paid_paise = total_paid_paise + ?,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (payout_paise, weight_grams, payout_paise, now, user_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE wallets SET
                        rejected_count = rejected_count + 1,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (now, user_id),
                )
            transaction = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            wallet = self._wallet(connection, user_id)
            connection.commit()
            return _txn_dict(transaction), wallet, False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_transactions(
        self, user_id: str, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> tuple[list[dict], int]:
        where = ["user_id = ?"]
        params: list[object] = [user_id]
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)
        connection = self._connect()
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM transactions WHERE {clause}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM transactions WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [_txn_dict(row) for row in rows], int(total)
        finally:
            connection.close()

    def create_withdrawal(
        self,
        *,
        user_id: str,
        amount_paise: int,
        destination: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[dict, dict, bool]:
        now = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM withdrawals WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "This Idempotency-Key was already used with different withdrawal data"
                    )
                wallet = self._wallet(connection, user_id)
                connection.commit()
                return _withdrawal_dict(existing), wallet, True

            self._ensure_wallet(connection, user_id, now)
            wallet = self._wallet(connection, user_id)
            if wallet["balance_paise"] < amount_paise:
                raise InsufficientFunds(
                    f"Available balance is {wallet['balance_paise']} paise"
                )
            cursor = connection.execute(
                """
                INSERT INTO withdrawals (
                    user_id, amount_paise, destination, status, idempotency_key,
                    request_hash, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    user_id,
                    amount_paise,
                    destination,
                    idempotency_key,
                    request_hash,
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE wallets SET balance_paise = balance_paise - ?, updated_at = ? WHERE user_id = ?",
                (amount_paise, now, user_id),
            )
            withdrawal = connection.execute(
                "SELECT * FROM withdrawals WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            wallet = self._wallet(connection, user_id)
            connection.commit()
            return _withdrawal_dict(withdrawal), wallet, False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_withdrawals(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict], int]:
        connection = self._connect()
        try:
            total = connection.execute(
                "SELECT COUNT(*) FROM withdrawals WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
            return [_withdrawal_dict(row) for row in rows], int(total)
        finally:
            connection.close()

    def _transition_withdrawal(
        self, withdrawal_id: int, target: str
    ) -> tuple[dict, dict, bool]:
        if target not in {"paid", "cancelled"}:
            raise ValueError("target must be paid or cancelled")
        now = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Withdrawal not found")
            if row["status"] == target:
                wallet = self._wallet(connection, row["user_id"])
                connection.commit()
                return _withdrawal_dict(row), wallet, True
            if row["status"] != "pending":
                raise WithdrawalStateError(
                    f"Cannot change a {row['status']} withdrawal to {target}"
                )
            connection.execute(
                "UPDATE withdrawals SET status = ?, updated_at = ? WHERE id = ?",
                (target, now, withdrawal_id),
            )
            if target == "cancelled":
                connection.execute(
                    "UPDATE wallets SET balance_paise = balance_paise + ?, updated_at = ? WHERE user_id = ?",
                    (row["amount_paise"], now, row["user_id"]),
                )
            updated = connection.execute(
                "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
            ).fetchone()
            wallet = self._wallet(connection, row["user_id"])
            connection.commit()
            return _withdrawal_dict(updated), wallet, False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_withdrawal(self, withdrawal_id: int) -> tuple[dict, dict, bool]:
        return self._transition_withdrawal(withdrawal_id, "paid")

    def cancel_withdrawal(self, withdrawal_id: int) -> tuple[dict, dict, bool]:
        return self._transition_withdrawal(withdrawal_id, "cancelled")
