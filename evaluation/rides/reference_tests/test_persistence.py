"""U1, U2, V1, V4."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import CARD, CENTER, PASSWORD, START, World, at
from rides import Platform


def build(db: Path) -> tuple[Platform, dict[str, str]]:
    p = Platform(START, db)
    w = World(p)
    near, _ = w.driver(location=at(0.5))
    backup, _ = w.driver(location=at(1))
    rider = w.rider()
    card = p.add_card(rider, CARD, 12, 2030, "737")
    p.top_up_wallet(rider, card, "40")
    quote = p.quote_ride(rider, CENTER, at(3), "economy")
    ride = p.request_ride(rider, quote["quote_id"], card, idempotency_key="key-1")
    scheduler = w.rider()
    scheduled = p.schedule_ride(scheduler, CENTER, at(2), "economy", START + timedelta(hours=1), w.card(scheduler))
    restaurant = w.restaurant()
    item = p.add_menu_item(restaurant, "Tea", "6", "food", 3)
    wallet = p.payment_methods(rider)[0]["id"]
    order = p.place_order(rider, restaurant, [{"item_id": item, "quantity": 2}], at(1), wallet)
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.authenticate(p.get_account(scheduler)["email"], "wrongpass99")
    p.advance(seconds=5)
    return p, {"near": near, "backup": backup, "rider": rider, "card": card, "ride": ride, "scheduled": scheduled,
               "order": order, "restaurant": restaurant, "scheduler": scheduler, "admin": w.admin}  # fmt: skip


def test_round_trip_continues_as_if_never_stopped(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    p, ids = build(db)
    p.save()
    q = Platform.open(db)
    assert q.now() == p.now()
    assert q.ride(ids["ride"]) == p.ride(ids["ride"])
    assert q.order(ids["order"]) == p.order(ids["order"])
    assert q.wallet_balance(ids["rider"]) == p.wallet_balance(ids["rider"])
    assert q.notifications(ids["near"]) == p.notifications(ids["near"])
    assert q.audit_log(ids["admin"]) == p.audit_log(ids["admin"])
    # the same calls give the same results on both platforms
    for platform in (p, q):
        platform.advance(seconds=10)  # the pending offer expires
    assert q.ride(ids["ride"])["offered_to"] == p.ride(ids["ride"])["offered_to"] == ids["backup"]
    assert q.request_ride(ids["rider"], "QTE-000001", ids["card"], idempotency_key="key-1") == ids["ride"]
    for platform in (p, q):
        platform.accept_ride(ids["backup"], ids["ride"])
        platform.advance(minutes=60)
    assert q.order(ids["order"])["status"] == p.order(ids["order"])["status"] == "expired"
    assert q.ride(ids["scheduled"]) == p.ride(ids["scheduled"])
    assert q.ride(ids["scheduled"])["status"] == "no_driver"  # offered to the free driver, then expired
    assert q.wallet_balance(ids["rider"]) == Decimal("40.00")
    # counters continue
    assert q.register_rider("New", "new@x.com", "+573008887766", PASSWORD) == p.register_rider(
        "New", "new@x.com", "+573008887766", PASSWORD)
    # the lock was persisted: 15 minutes have passed since, so the right password works again
    email = q.get_account(ids["scheduler"])["email"]
    assert q.authenticate(email, PASSWORD) == ids["scheduler"]


def test_lock_and_password_hash_survive(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    p, ids = build(db)
    p.save()
    q = Platform.open(db)
    email = q.get_account(ids["scheduler"])["email"]
    with pytest.raises(PermissionError):
        q.authenticate(email, PASSWORD)  # still locked
    q.advance(minutes=15)
    assert q.authenticate(email, PASSWORD) == ids["scheduler"]
    with pytest.raises(PermissionError):
        q.authenticate(email, "wrongpass99")


def test_open_restores_last_save_only(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    p = Platform(START, db)
    admin = p.create_admin("A", "a@x.com", "+573001112222", PASSWORD)
    p.save()
    p.register_rider("R", "r@x.com", "+573001112223", PASSWORD)
    p.advance(minutes=5)
    q = Platform.open(db)
    assert q.now() == START
    with pytest.raises(KeyError):
        q.get_account("RID-000001")
    assert q.get_account(admin)["role"] == "admin"
    assert q.register_rider("R", "r@x.com", "+573001112223", PASSWORD) == "RID-000001"


def test_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    Platform(START, db).save()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        conn.execute("UPDATE schema_version SET version = 99")
    conn.close()
    with pytest.raises(ValueError):
        Platform.open(db)
    with pytest.raises(ValueError):
        Platform.open(tmp_path / "missing.db")


def test_no_secrets_in_database(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    p, _ = build(db)
    p.save()
    raw = db.read_bytes()
    for secret in (PASSWORD.encode(), CARD.encode(), b"737", b"wrongpass99"):
        assert secret not in raw
    with sqlite3.connect(db) as conn:
        dump = "\n".join(conn.iterdump())
    conn.close()
    assert PASSWORD not in dump and CARD not in dump


def test_platform_without_database_can_save(p: Platform) -> None:
    p.save()  # nothing to do, nothing raised
