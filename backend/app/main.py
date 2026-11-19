"""FastAPI entrypoint for ColdChain Pulse Backend & Static Frontend."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.db import append_audit_log, db_session, run_migrations, utc_now_iso, verify_audit_chain
from app.security import create_access_token, require_roles, verify_password
from app.telemetry import ingest_telemetry_point, seed_initial_data, telemetry_simulator_loop

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateShipmentRequest(BaseModel):
    batchCode: str = Field(..., min_length=1)
    origin: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    vehicleId: str = Field(..., min_length=1)
    minTemp: float
    maxTemp: float


class GpsCoordinates(BaseModel):
    lat: float = 0.0
    lng: float = 0.0


class HandoffRequest(BaseModel):
    idempotencyKey: Optional[str] = None
    shipmentId: str = Field(..., min_length=1)
    handoffType: str = Field(..., pattern="^(PICKUP|DELIVERY)$")
    timestamp: str
    decision: Optional[str] = Field(default=None, pattern="^(ACCEPTED|REJECTED)$")
    gpsCoordinates: GpsCoordinates
    conditionStatus: str = Field(..., pattern="^(INTACT|DAMAGED|SEAL_BROKEN)$")
    verificationMethod: str = Field(..., pattern="^(PIN|SIGNATURE)$")
    verificationData: str = Field(..., min_length=1)


class TelemetryIngestRequest(BaseModel):
    shipmentId: str = Field(..., min_length=1)
    temperature: float
    humidity: float = 55.0
    lat: float = 23.8103
    lng: float = 90.4125
    recordedAt: Optional[str] = None


def _row_to_shipment(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "batchCode": r["batch_code"],
        "vehicleId": r["vehicle_id"],
        "origin": r["origin"],
        "destination": r["destination"],
        "minTemp": r["min_temp"],
        "maxTemp": r["max_temp"],
        "lat": r["lat"],
        "lng": r["lng"],
        "currentTemp": r["current_temp"],
        "currentHumidity": r["current_humidity"],
        "status": r["status"],
        "breachDurationMinutes": r["breach_duration_minutes"],
        "eta": r["eta"],
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    seed_initial_data()
    task = None
    if os.environ.get("COLDCHAIN_DISABLE_SIMULATOR") != "1":
        interval = int(os.environ.get("COLDCHAIN_SIM_INTERVAL", "30"))
        task = asyncio.create_task(telemetry_simulator_loop(interval))
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="ColdChain Pulse API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "coldchain-pulse-backend"}


@app.post("/api/auth/login")
async def login(body: LoginRequest, request: Request):
    email = body.email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    now_iso = utc_now_iso()

    failed_reason = None
    with db_session() as conn:
        user = conn.execute(
            "SELECT id, email, password_hash, role, assigned_vehicle_id, assigned_clinic FROM users WHERE lower(email) = ?",
            (email,),
        ).fetchone()

        if not user or not verify_password(body.password, user["password_hash"]):
            failed_reason = "USER_NOT_FOUND" if not user else "INVALID_PASSWORD"
            conn.execute(
                "INSERT INTO failed_logins (email, ip_address, reason, attempted_at) VALUES (?, ?, ?, ?)",
                (email, client_ip, failed_reason, now_iso),
            )
            append_audit_log(
                conn,
                ts=now_iso,
                log_type="AUTH_FAIL",
                shipment_id="SYSTEM",
                detail=f"Failed login for {email} from {client_ip} ({failed_reason})",
            )
        else:
            token = create_access_token(dict(user))
            return {"token": token, "role": user["role"]}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )


@app.get("/api/shipments")
async def list_shipments(
    assigned: Optional[str] = Query(default=None),
    user: dict[str, Any] = Depends(
        require_roles("ADMIN", "DISPATCHER", "DRIVER", "CLINIC_RECEIVER")
    ),
):
    with db_session() as conn:
        if assigned == "me":
            role = user.get("role")
            if role == "DRIVER" and user.get("assigned_vehicle_id"):
                rows = conn.execute(
                    "SELECT * FROM shipments WHERE vehicle_id = ? ORDER BY created_at DESC",
                    (user["assigned_vehicle_id"],),
                ).fetchall()
            elif role == "CLINIC_RECEIVER" and user.get("assigned_clinic"):
                rows = conn.execute(
                    "SELECT * FROM shipments WHERE destination = ? ORDER BY created_at DESC",
                    (user["assigned_clinic"],),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM shipments WHERE status != 'DELIVERED' ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM shipments ORDER BY created_at DESC").fetchall()

        return [_row_to_shipment(r) for r in rows]


@app.post("/api/shipments", status_code=status.HTTP_201_CREATED)
async def create_shipment(
    body: CreateShipmentRequest,
    _user: dict[str, Any] = Depends(require_roles("ADMIN", "DISPATCHER")),
):
    if body.minTemp >= body.maxTemp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="minTemp must be strictly less than maxTemp",
        )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    eta_iso = (now + timedelta(hours=3)).isoformat()
    sid = f"S{uuid.uuid4().hex[:8].upper()}"

    with db_session() as conn:
        dup = conn.execute(
            "SELECT id FROM shipments WHERE batch_code = ?", (body.batchCode.strip(),)
        ).fetchone()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Batch code '{body.batchCode}' already exists",
            )

        # Ensure referenced vehicle exists
        veh = conn.execute("SELECT id FROM vehicles WHERE id = ?", (body.vehicleId.strip(),)).fetchone()
        if not veh:
            conn.execute(
                "INSERT INTO vehicles (id, plate_number, capacity_units, status) VALUES (?, ?, 500, 'ACTIVE')",
                (body.vehicleId.strip(), f"DHK-{body.vehicleId.strip()}"),
            )

        init_temp = round((body.minTemp + body.maxTemp) / 2.0, 1)
        conn.execute(
            """
            INSERT INTO shipments (
                id, batch_code, vehicle_id, driver_id, origin, destination,
                min_temp, max_temp, lat, lng, current_temp, current_humidity,
                status, breach_started_at, breach_duration_minutes, eta, created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 23.8103, 90.4125, ?, 55.0, 'NORMAL', NULL, 0.0, ?, ?)
            """,
            (
                sid, body.batchCode.strip(), body.vehicleId.strip(),
                body.origin.strip(), body.destination.strip(),
                body.minTemp, body.maxTemp, init_temp, eta_iso, now_iso,
            ),
        )
        created = conn.execute("SELECT * FROM shipments WHERE id = ?", (sid,)).fetchone()
        return _row_to_shipment(created)


@app.post("/api/handoffs")
async def submit_handoff(
    body: HandoffRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: dict[str, Any] = Depends(require_roles("ADMIN", "DRIVER", "CLINIC_RECEIVER")),
):
    idem_key = idempotency_key or body.idempotencyKey
    if not idem_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key is required",
        )

    now_iso = utc_now_iso()
    with db_session() as conn:
        existing = conn.execute(
            "SELECT id FROM handoffs WHERE idempotency_key = ?", (idem_key,)
        ).fetchone()
        if existing:
            return {"ok": True, "duplicate": True, "handoffId": existing["id"]}

        shipment = conn.execute(
            "SELECT id, status FROM shipments WHERE id = ? OR batch_code = ?",
            (body.shipmentId.strip(), body.shipmentId.strip()),
        ).fetchone()
        if not shipment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Shipment '{body.shipmentId}' not found",
            )

        hid = f"HO-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            """
            INSERT INTO handoffs (
                id, idempotency_key, shipment_id, actor_id, handoff_type, decision,
                condition_status, verification_method, verification_data,
                lat, lng, client_timestamp, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid, idem_key, shipment["id"], user["sub"], body.handoffType, body.decision,
                body.conditionStatus, body.verificationMethod, body.verificationData,
                body.gpsCoordinates.lat, body.gpsCoordinates.lng, body.timestamp, now_iso,
            ),
        )

        if body.handoffType == "DELIVERY" and body.decision != "REJECTED":
            conn.execute(
                "UPDATE shipments SET status = 'DELIVERED', breach_started_at = NULL, breach_duration_minutes = 0.0 WHERE id = ?",
                (shipment["id"],),
            )

        decision_txt = f" ({body.decision})" if body.decision else ""
        append_audit_log(
            conn,
            ts=body.timestamp,
            log_type="HANDOFF",
            shipment_id=shipment["id"],
            detail=f"{body.handoffType} {body.conditionStatus}{decision_txt} via {body.verificationMethod}",
            lat=body.gpsCoordinates.lat,
            lng=body.gpsCoordinates.lng,
        )

        return {"ok": True, "duplicate": False, "handoffId": hid}


@app.post("/api/telemetry/ingest")
async def ingest_telemetry(
    body: TelemetryIngestRequest,
    _user: dict[str, Any] = Depends(require_roles("ADMIN")),
):
    with db_session() as conn:
        try:
            return ingest_telemetry_point(
                conn,
                shipment_id=body.shipmentId,
                temperature=body.temperature,
                humidity=body.humidity,
                lat=body.lat,
                lng=body.lng,
                recorded_at=body.recordedAt,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@app.get("/api/audit/logs")
async def get_audit_logs(
    limit: int = Query(default=500, ge=1, le=5000),
    _user: dict[str, Any] = Depends(require_roles("ADMIN", "AUDITOR")),
):
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT ts, type, shipment_id, temp, detail, lat, lng
            FROM audit_logs
            WHERE type != 'AUTH_FAIL'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "ts": r["ts"],
                "type": r["type"],
                "shipmentId": r["shipment_id"],
                "temp": r["temp"],
                "detail": r["detail"],
                "lat": r["lat"],
                "lng": r["lng"],
            }
            for r in rows
        ]


@app.get("/api/audit/verify")
async def verify_audit_integrity(
    _user: dict[str, Any] = Depends(require_roles("ADMIN", "AUDITOR")),
):
    with db_session() as conn:
        return verify_audit_chain(conn)


# Serve static frontend files at '/' without blocking threadpools
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    rel = full_path.lstrip("/") or "index.html"
    target = (FRONTEND_DIR / rel).resolve()
    try:
        target.relative_to(FRONTEND_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type, _ = mimetypes.guess_type(str(target))
    return Response(content=target.read_bytes(), media_type=media_type or "application/octet-stream")
