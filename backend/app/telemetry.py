"""IoT telemetry ingestion, excursion/breach state machine, seeding, and 30s simulator."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import append_audit_log, db_session, utc_now_iso
from app.security import hash_password

DEMO_USERS = [
    ("u-admin", "admin@medswift.com", "MedSwift2026!", "ADMIN", "System Admin", None, None),
    ("u-disp", "dispatcher@medswift.com", "MedSwift2026!", "DISPATCHER", "Lead Dispatcher", None, None),
    ("u-drv", "driver@medswift.com", "MedSwift2026!", "DRIVER", "Rahim Uddin", "VAN-1", None),
    ("u-cln", "clinic@medswift.com", "MedSwift2026!", "CLINIC_RECEIVER", "Dr. Farhana Rahman", None, "Clinic 1"),
    ("u-aud", "auditor@medswift.com", "MedSwift2026!", "AUDITOR", "External Regulator", None, None),
]


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def evaluate_excursion(
    *,
    current_status: str,
    min_temp: float,
    max_temp: float,
    breach_started_at: Optional[str],
    temp: float,
    recorded_at: str,
) -> tuple[str, Optional[str], float, bool]:
    """Compute next shipment status, breach_started_at, breach_duration_minutes, and whether SLA_BREACH triggered."""
    if current_status == "DELIVERED":
        return "DELIVERED", None, 0.0, False

    if temp < min_temp or temp > max_temp:
        start_ts = breach_started_at or recorded_at
        elapsed_min = max(
            0.0, (_parse_iso(recorded_at) - _parse_iso(start_ts)).total_seconds() / 60.0
        )
        if elapsed_min > 10.0:
            triggered_sla = current_status != "CRITICAL"
            return "CRITICAL", start_ts, round(elapsed_min, 1), triggered_sla
        return "WARNING", start_ts, round(elapsed_min, 1), False

    if temp <= min_temp + 0.5 or temp >= max_temp - 0.5:
        return "WARNING", None, 0.0, False

    return "NORMAL", None, 0.0, False


def ingest_telemetry_point(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    temperature: float,
    humidity: float,
    lat: float,
    lng: float,
    recorded_at: Optional[str] = None,
) -> dict[str, object]:
    """Append a telemetry reading and update shipment excursion state."""
    ts = recorded_at or utc_now_iso()
    row = conn.execute(
        "SELECT id, status, min_temp, max_temp, breach_started_at FROM shipments WHERE id = ?",
        (shipment_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Shipment '{shipment_id}' not found")

    conn.execute(
        """
        INSERT INTO telemetry_logs (shipment_id, temperature, humidity, lat, lng, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (shipment_id, temperature, humidity, lat, lng, ts),
    )

    new_status, new_start, breach_mins, triggered_sla = evaluate_excursion(
        current_status=row["status"],
        min_temp=row["min_temp"],
        max_temp=row["max_temp"],
        breach_started_at=row["breach_started_at"],
        temp=temperature,
        recorded_at=ts,
    )

    if row["status"] != "DELIVERED":
        conn.execute(
            """
            UPDATE shipments
            SET current_temp = ?, current_humidity = ?, lat = ?, lng = ?,
                status = ?, breach_started_at = ?, breach_duration_minutes = ?
            WHERE id = ?
            """,
            (temperature, humidity, lat, lng, new_status, new_start, breach_mins, shipment_id),
        )

    if triggered_sla:
        append_audit_log(
            conn,
            ts=ts,
            log_type="SLA_BREACH",
            shipment_id=shipment_id,
            temp=temperature,
            detail=f"Temp {temperature:.1f}°C breached for {breach_mins:.1f} min",
            lat=lat,
            lng=lng,
        )

    return {
        "shipmentId": shipment_id,
        "status": new_status,
        "breachDurationMinutes": breach_mins,
        "slaBreachTriggered": triggered_sla,
    }


def seed_initial_data(db_path=None, shipment_count: int = 600) -> None:
    """Idempotently seed demo users, vehicles, 600 shipments, and initial audit logs."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with db_session(db_path) as conn:
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if user_count == 0:
            for uid, email, pwd, role, name, veh, clinic in DEMO_USERS:
                conn.execute(
                    """
                    INSERT INTO users (id, email, password_hash, role, full_name, assigned_vehicle_id, assigned_clinic, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uid, email, hash_password(pwd), role, name, veh, clinic, now_iso),
                )

        veh_count = conn.execute("SELECT COUNT(*) AS c FROM vehicles").fetchone()["c"]
        if veh_count == 0:
            conn.executemany(
                "INSERT INTO vehicles (id, plate_number, capacity_units, status) VALUES (?, ?, ?, ?)",
                [(f"VAN-{i}", f"DHK-MED-{1000 + i}", 500, "ACTIVE") for i in range(80)],
            )

        ship_count = conn.execute("SELECT COUNT(*) AS c FROM shipments").fetchone()["c"]
        if ship_count == 0:
            rng = random.Random(42)
            rows = []
            for i in range(shipment_count):
                sid = f"S{i}"
                batch = f"B-{1000 + i}"
                veh = f"VAN-{i % 80}"
                drv = "u-drv" if i % 80 == 1 else None
                dest = f"Clinic {i % 120}"
                lat = round(22.5 + ((i * 37) % 30) / 10.0, 4)
                lng = round(89.5 + ((i * 53) % 25) / 10.0, 4)
                eta = (now + timedelta(hours=1 + (i % 5))).isoformat()

                if i % 8 == 0:
                    st, temp, b_start, b_min = "DELIVERED", 4.8, None, 0.0
                elif i in (3, 19):
                    st = "CRITICAL"
                    temp = 9.4
                    b_start = (now - timedelta(minutes=12)).isoformat()
                    b_min = 12.0
                elif i % 25 == 0:
                    st, temp, b_start, b_min = "WARNING", 7.8, None, 0.0
                else:
                    st, temp, b_start, b_min = "NORMAL", round(rng.uniform(3.2, 6.8), 1), None, 0.0

                hum = round(rng.uniform(45.0, 65.0), 1)
                rows.append(
                    (
                        sid, batch, veh, drv, "Central WH", dest,
                        2.0, 8.0, lat, lng, temp, hum, st, b_start, b_min, eta, now_iso,
                    )
                )

            conn.executemany(
                """
                INSERT INTO shipments (
                    id, batch_code, vehicle_id, driver_id, origin, destination,
                    min_temp, max_temp, lat, lng, current_temp, current_humidity,
                    status, breach_started_at, breach_duration_minutes, eta, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            # Seed initial audit history so Compliance Portal has immediate chart & table data
            for k in range(30):
                ts = (now - timedelta(minutes=(30 - k) * 10)).isoformat()
                sid = f"S{k}"
                if k in (3, 19):
                    append_audit_log(
                        conn, ts=ts, log_type="SLA_BREACH", shipment_id=sid,
                        temp=9.4, detail="Temp 9.4°C breached for 12.0 min",
                        lat=23.78 + k / 1000.0, lng=90.40 + k / 1000.0,
                    )
                elif k % 4 == 0:
                    append_audit_log(
                        conn, ts=ts, log_type="HANDOFF", shipment_id=sid,
                        temp=None, detail="PICKUP INTACT (PIN)",
                        lat=23.78 + k / 1000.0, lng=90.40 + k / 1000.0,
                    )
                else:
                    t_val = round(4.5 + (k % 5) * 0.4, 1)
                    append_audit_log(
                        conn, ts=ts, log_type="TEMP", shipment_id=sid,
                        temp=t_val, detail=f"Temp {t_val:.1f}°C",
                        lat=23.78 + k / 1000.0, lng=90.40 + k / 1000.0,
                    )


def run_simulation_tick(db_path=None) -> int:
    """Simulate one 30-second IoT telemetry tick across all active shipments."""
    now_iso = utc_now_iso()
    rng = random.Random()
    updated = 0

    with db_session(db_path) as conn:
        active = conn.execute(
            """
            SELECT id, status, min_temp, max_temp, current_temp, current_humidity,
                   lat, lng, breach_started_at
            FROM shipments
            WHERE status != 'DELIVERED'
            """
        ).fetchall()

        telemetry_batch = []
        shipment_updates = []
        sla_events = []

        for s in active:
            drift = rng.uniform(-0.3, 0.3)
            # Keep most vehicles stable; occasionally nudge a small subset toward excursion
            if s["id"] in ("S3", "S19"):
                new_temp = round(max(8.5, min(10.5, s["current_temp"] + drift)), 1)
            elif rng.random() < 0.02:
                new_temp = round(rng.choice([7.8, 8.3, 1.8]), 1)
            else:
                target = max(s["min_temp"] + 1.0, min(s["max_temp"] - 1.0, s["current_temp"] + drift))
                new_temp = round(target, 1)

            new_hum = round(max(35.0, min(75.0, s["current_humidity"] + rng.uniform(-1.0, 1.0))), 1)
            new_lat = round(s["lat"] + rng.uniform(-0.002, 0.002), 4)
            new_lng = round(s["lng"] + rng.uniform(-0.002, 0.002), 4)

            new_status, new_start, breach_mins, triggered_sla = evaluate_excursion(
                current_status=s["status"],
                min_temp=s["min_temp"],
                max_temp=s["max_temp"],
                breach_started_at=s["breach_started_at"],
                temp=new_temp,
                recorded_at=now_iso,
            )

            telemetry_batch.append((s["id"], new_temp, new_hum, new_lat, new_lng, now_iso))
            shipment_updates.append(
                (new_temp, new_hum, new_lat, new_lng, new_status, new_start, breach_mins, s["id"])
            )
            if triggered_sla:
                sla_events.append((s["id"], new_temp, breach_mins, new_lat, new_lng))
            updated += 1

        conn.executemany(
            "INSERT INTO telemetry_logs (shipment_id, temperature, humidity, lat, lng, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            telemetry_batch,
        )
        conn.executemany(
            """
            UPDATE shipments
            SET current_temp = ?, current_humidity = ?, lat = ?, lng = ?,
                status = ?, breach_started_at = ?, breach_duration_minutes = ?
            WHERE id = ?
            """,
            shipment_updates,
        )
        for sid, temp, mins, lat, lng in sla_events:
            append_audit_log(
                conn,
                ts=now_iso,
                log_type="SLA_BREACH",
                shipment_id=sid,
                temp=temp,
                detail=f"Temp {temp:.1f}°C breached for {mins:.1f} min",
                lat=lat,
                lng=lng,
            )

    return updated


async def telemetry_simulator_loop(interval_seconds: int = 30) -> None:
    """Background asyncio task running IoT telemetry ticks every interval_seconds."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            run_simulation_tick()
        except Exception:
            # Keep background loop alive even if a single tick encounters contention
            pass
