"""Comprehensive automated tests for ColdChain Pulse Backend & Data Engineering."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import db_session, run_migrations  # noqa: E402
from app.telemetry import ingest_telemetry_point, seed_initial_data  # noqa: E402


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_coldchain.db"
    monkeypatch.setenv("COLDCHAIN_DB_PATH", str(db_file))
    monkeypatch.setenv("COLDCHAIN_DISABLE_SIMULATOR", "1")
    run_migrations(db_file)
    seed_initial_data(db_file)
    from app.main import app

    def run(coro):
        async def _runner():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await coro(client)

        return asyncio.run(_runner())

    return run, db_file


async def _login(client: httpx.AsyncClient, email: str, password: str = "MedSwift2026!") -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def test_zero_data_loss_migrations(tmp_path: Path):
    db_file = tmp_path / "mig_test.db"
    applied_1 = run_migrations(db_file, max_version="001_initial.sql")
    assert applied_1 == ["001_initial.sql"]

    with db_session(db_file) as conn:
        conn.execute(
            "INSERT INTO vehicles (id, plate_number, capacity_units, status) VALUES ('VAN-X', 'DHK-9999', 500, 'ACTIVE')"
        )

    applied_2 = run_migrations(db_file)
    assert applied_2 == ["002_indexes_and_perf.sql"]

    with db_session(db_file) as conn:
        row = conn.execute("SELECT plate_number FROM vehicles WHERE id = 'VAN-X'").fetchone()
        assert row is not None and row["plate_number"] == "DHK-9999"
        versions = [
            r["version"]
            for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        ]
        assert versions == ["001_initial.sql", "002_indexes_and_perf.sql"]


def test_append_only_triggers(api_env):
    _, db_file = api_env
    with db_session(db_file) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="Append-only"):
            conn.execute("UPDATE audit_logs SET detail = 'tampered' WHERE id = 1")
        with pytest.raises(sqlite3.DatabaseError, match="Append-only"):
            conn.execute("DELETE FROM audit_logs WHERE id = 1")

        conn.execute(
            "INSERT INTO telemetry_logs (shipment_id, temperature, humidity, lat, lng, recorded_at) "
            "VALUES ('S0', 5.0, 50.0, 23.8, 90.4, '2026-01-01T00:00:00Z')"
        )
        with pytest.raises(sqlite3.DatabaseError, match="Append-only"):
            conn.execute("UPDATE telemetry_logs SET temperature = 99.0")
        with pytest.raises(sqlite3.DatabaseError, match="Append-only"):
            conn.execute("DELETE FROM telemetry_logs")


def test_failed_login_audit_and_rbac(api_env):
    run, db_file = api_env

    async def scenario(c: httpx.AsyncClient):
        assert (await c.get("/api/shipments")).status_code == 401

        bad = await c.post("/api/auth/login", json={"email": "driver@medswift.com", "password": "wrong"})
        assert bad.status_code == 401

        t_admin = (await _login(c, "admin@medswift.com"))["token"]
        t_disp = (await _login(c, "dispatcher@medswift.com"))["token"]
        t_drv = (await _login(c, "driver@medswift.com"))["token"]
        t_cln = (await _login(c, "clinic@medswift.com"))["token"]
        t_aud = (await _login(c, "auditor@medswift.com"))["token"]

        new_ship = {
            "batchCode": "B-RBAC-1",
            "origin": "Central WH",
            "destination": "Clinic 1",
            "vehicleId": "VAN-1",
            "minTemp": 2.0,
            "maxTemp": 8.0,
        }
        assert (await c.post("/api/shipments", json=new_ship, headers={"Authorization": f"Bearer {t_drv}"})).status_code == 403
        assert (await c.post("/api/shipments", json=new_ship, headers={"Authorization": f"Bearer {t_cln}"})).status_code == 403
        assert (await c.post("/api/shipments", json=new_ship, headers={"Authorization": f"Bearer {t_aud}"})).status_code == 403
        assert (await c.post("/api/shipments", json=new_ship, headers={"Authorization": f"Bearer {t_disp}"})).status_code == 201

        assert (await c.get("/api/audit/logs", headers={"Authorization": f"Bearer {t_disp}"})).status_code == 403
        assert (await c.get("/api/audit/logs", headers={"Authorization": f"Bearer {t_drv}"})).status_code == 403
        assert (await c.get("/api/audit/logs", headers={"Authorization": f"Bearer {t_cln}"})).status_code == 403
        assert (await c.get("/api/audit/logs", headers={"Authorization": f"Bearer {t_aud}"})).status_code == 200
        assert (await c.get("/api/audit/logs", headers={"Authorization": f"Bearer {t_admin}"})).status_code == 200

        ho = {
            "idempotencyKey": "rbac-ho-1",
            "shipmentId": "S1",
            "handoffType": "PICKUP",
            "timestamp": "2026-11-19T08:00:00Z",
            "gpsCoordinates": {"lat": 23.8, "lng": 90.4},
            "conditionStatus": "INTACT",
            "verificationMethod": "PIN",
            "verificationData": "abcd1234",
        }
        assert (await c.post("/api/handoffs", json=ho, headers={"Authorization": f"Bearer {t_disp}", "Idempotency-Key": "rbac-ho-1"})).status_code == 403
        assert (await c.post("/api/handoffs", json=ho, headers={"Authorization": f"Bearer {t_aud}", "Idempotency-Key": "rbac-ho-1"})).status_code == 403

    run(scenario)

    with db_session(db_file) as conn:
        fl = conn.execute("SELECT email, reason FROM failed_logins").fetchall()
        assert len(fl) == 1
        assert fl[0]["email"] == "driver@medswift.com"
        assert fl[0]["reason"] == "INVALID_PASSWORD"


def test_excursion_engine_no_false_positives_and_critical_breach(api_env):
    _, db_file = api_env
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)

    with db_session(db_file) as conn:
        r1 = ingest_telemetry_point(conn, shipment_id="S1", temperature=9.2, humidity=55.0, lat=23.8, lng=90.4, recorded_at=t0.isoformat())
        assert r1["status"] == "WARNING"
        assert r1["slaBreachTriggered"] is False

        r2 = ingest_telemetry_point(conn, shipment_id="S1", temperature=9.1, humidity=55.0, lat=23.8, lng=90.4, recorded_at=(t0 + timedelta(minutes=6)).isoformat())
        assert r2["status"] == "WARNING"
        assert r2["slaBreachTriggered"] is False

        r3 = ingest_telemetry_point(conn, shipment_id="S1", temperature=5.0, humidity=55.0, lat=23.8, lng=90.4, recorded_at=(t0 + timedelta(minutes=8)).isoformat())
        assert r3["status"] == "NORMAL"
        assert r3["breachDurationMinutes"] == 0.0

        ingest_telemetry_point(conn, shipment_id="S1", temperature=9.5, humidity=55.0, lat=23.8, lng=90.4, recorded_at=(t0 + timedelta(minutes=10)).isoformat())
        r5 = ingest_telemetry_point(conn, shipment_id="S1", temperature=9.6, humidity=55.0, lat=23.8, lng=90.4, recorded_at=(t0 + timedelta(minutes=21)).isoformat())
        assert r5["status"] == "CRITICAL"
        assert r5["breachDurationMinutes"] == 11.0
        assert r5["slaBreachTriggered"] is True


def test_idempotent_handoff_sync_and_delivery_transition(api_env):
    run, db_file = api_env
    idem = "offline-uuid-12345"

    async def scenario(c: httpx.AsyncClient):
        t_drv = (await _login(c, "driver@medswift.com"))["token"]
        payload = {
            "idempotencyKey": idem,
            "shipmentId": "S1",
            "handoffType": "DELIVERY",
            "decision": "ACCEPTED",
            "timestamp": "2026-11-19T09:00:00Z",
            "gpsCoordinates": {"lat": 23.81, "lng": 90.41},
            "conditionStatus": "INTACT",
            "verificationMethod": "PIN",
            "verificationData": "deadbeef",
        }
        r1 = await c.post("/api/handoffs", json=payload, headers={"Authorization": f"Bearer {t_drv}", "Idempotency-Key": idem})
        assert r1.status_code == 200
        assert r1.json()["duplicate"] is False

        r2 = await c.post("/api/handoffs", json=payload, headers={"Authorization": f"Bearer {t_drv}", "Idempotency-Key": idem})
        assert r2.status_code == 200
        assert r2.json()["duplicate"] is True
        assert r2.json()["handoffId"] == r1.json()["handoffId"]

    run(scenario)

    with db_session(db_file) as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM handoffs WHERE idempotency_key = ?", (idem,)).fetchone()["c"]
        assert cnt == 1
        st = conn.execute("SELECT status FROM shipments WHERE id = 'S1'").fetchone()["status"]
        assert st == "DELIVERED"


def test_600_shipments_performance_audit_chain_and_frontend_serving(api_env):
    run, _ = api_env

    async def scenario(c: httpx.AsyncClient):
        t_disp = (await _login(c, "dispatcher@medswift.com"))["token"]
        t_aud = (await _login(c, "auditor@medswift.com"))["token"]

        t0 = time.perf_counter()
        res = await c.get("/api/shipments", headers={"Authorization": f"Bearer {t_disp}"})
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert res.status_code == 200
        assert len(res.json()) >= 600
        assert elapsed_ms < 1000.0

        verify = await c.get("/api/audit/verify", headers={"Authorization": f"Bearer {t_aud}"})
        assert verify.status_code == 200
        v_data = verify.json()
        assert v_data["valid"] is True
        assert v_data["checked"] >= 30

        home = await c.get("/")
        assert home.status_code == 200
        assert "ColdChain Pulse" in home.text

        cfg = await c.get("/js/config.js")
        assert cfg.status_code == 200
        assert "USE_MOCK: false" in cfg.text

    run(scenario)
