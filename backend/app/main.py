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

def get_frontend_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent,         # backend/
        Path(__file__).resolve().parent.parent.parent,  # root/
        Path.cwd(),                                     # /var/task or current dir
        Path.cwd() / "backend",
        Path("/var/task"),
        Path("/var/task/backend"),
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c.resolve()
    return candidates[0].resolve()


FRONTEND_DIR = get_frontend_dir()

INDEX_HTML_FALLBACK = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ColdChain Pulse — MedSwift Logistics</title>
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#142C4E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<link rel="stylesheet" href="css/style.css">
<script src="js/config.js"></script>
</head>
<body>
<div id="boot" style="padding:2rem">Loading… (if this stays, press F12 and check the Console; js/ and css/ must sit next to index.html)</div>
<div id="netbar" class="netbar"></div>
<div id="critical-banner" class="hidden critical-banner" role="alert"></div>
<div id="toasts" class="fixed top-24 right-4 z-50 space-y-2"></div>

<!-- LOGIN -->
<section id="view-login" class="login hidden">
  <div class="brand">
    <div class="flex items-center gap-3 text-lg font-bold"><svg width="30" height="30" viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="9" fill="#0891b2"/><path d="M5 17h5l3-7 5 13 3-6h6" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg> ColdChain Pulse</div>
    <div>
      <h1 class="text-4xl font-bold leading-tight">Every dose, every degree,<br>every handoff — verified.</h1>
      <ul class="mt-6"><li>● Live temperature &amp; excursion monitoring</li><li>● Offline-first chain-of-custody for drivers</li><li>● Tamper-evident compliance reporting</li></ul>
    </div>
    <p class="text-sm text-cyan-200">© MedSwift Logistics Ltd.</p>
  </div>
  <div class="flex items-center justify-center p-6">
    <div class="w-full max-w-sm">
      <h2 class="text-2xl font-bold">Welcome back</h2>
      <p class="text-sm text-slate-500 mb-4">Sign in to your workspace</p>
      <label class="lbl" for="email">Email</label>
      <input id="email" class="field" type="email" placeholder="you@medswift.com" autocomplete="username">
      <label class="lbl" for="password">Password</label>
      <input id="password" class="field" type="password" placeholder="••••••••" autocomplete="current-password">
      <button id="btn-login" class="btn btn-primary w-full mt-4">Sign in</button>
      <p id="login-err" class="text-red-600 text-sm mt-2"></p>
      <p id="demo" class="hidden text-xs text-slate-500 mt-4 bg-slate-100 rounded-lg p-3">Demo: admin@, dispatcher@, driver@, clinic@, auditor@ + any domain (e.g. driver@x.com), any password.</p>
    </div>
  </div>
</section>

<div id="shell" class="shell hidden">
<aside class="side">
  <div class="brandrow"><svg width="30" height="30" viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="9" fill="#0891b2"/><path d="M5 17h5l3-7 5 13 3-6h6" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg><div><b>ColdChain Pulse</b><small>MedSwift Logistics</small></div></div>
  <nav id="nav" class="nav"></nav>
  <div class="sidefoot"><span id="avatar" class="avatar"></span><div><b id="who" class="capitalize"></b><button id="btn-logout">Logout</button></div></div>
</aside>
<div class="main">
<header id="hdr" class="topbar">
  <div id="topsearch" class="flex-1 max-w-md"><input id="search" class="field" placeholder="🔍  Search shipment / batch ID"></div>
  <a href="#dispatcher" class="bell" title="Active alerts">🔔<em id="bell-n">0</em></a>
</header>

<section id="view-dispatcher" class="hidden p-4 md:p-6 space-y-4">
  <div class="flex items-end justify-between flex-wrap gap-2">
    <div><h2 class="text-xl font-bold">Dispatcher Dashboard</h2><p class="text-sm text-slate-500">Live cold-chain telemetry · refreshed every 30 s</p></div>
    <button id="btn-new" class="btn btn-primary" data-requires="create">+ New Shipment</button>
  </div>
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
    <div class="kpi"><span><i style="background:#087E8B"></i>Active shipments</span><b id="k-total">–</b></div>
    <div class="kpi"><span><i style="background:#64748b"></i>Delivered</span><b id="k-normal">–</b></div>
    <div class="kpi"><span><i style="background:#f59e0b"></i>Warning</span><b id="k-warn">–</b></div>
    <div class="kpi"><span><i style="background:#dc2626"></i>Critical</span><b id="k-crit">–</b></div>
  </div>
  <div class="grid lg:grid-cols-3 gap-4">
    <div class="panel lg:col-span-2"><div class="phead">Live shipment tracking</div><div id="map"></div></div>
    <div class="panel"><div class="phead">Alerts <small>critical first</small></div><div id="alerts" class="p-3 space-y-2"></div></div>
  </div>
  <div id="tcards" class="grid grid-cols-2 lg:grid-cols-4 gap-3"></div>
  <div class="panel">
    <div class="phead">Recent shipments <select id="filter" class="field !w-auto !py-1.5"><option value="">All status</option><option>NORMAL</option><option>WARNING</option><option>CRITICAL</option><option>DELIVERED</option></select><span id="count" class="text-xs text-slate-500 font-normal"></span></div>
    <div class="grid-head"><span>Batch</span><span>Vehicle</span><span>Temp</span><span>Humidity</span><span>Status</span><span>ETA</span></div>
    <div id="grid" class="grid-body"><div id="grid-inner"></div></div>
  </div>
</section>

<dialog id="modal" class="w-full max-w-md p-0">
  <form id="ship-form" method="dialog" class="p-6">
    <h2 class="font-bold text-lg mb-1">Create Shipment</h2><p class="text-sm text-slate-500 mb-2">Set route and safe temperature range.</p>
    <label class="lbl">Batch / Shipment ID</label><input name="batchCode" class="field" required>
    <label class="lbl">Origin Warehouse</label><input name="origin" class="field" required>
    <label class="lbl">Destination Rural Clinic</label><input name="destination" class="field" required>
    <label class="lbl">Refrigerated Vehicle</label><input name="vehicleId" class="field" required>
    <div class="grid grid-cols-2 gap-3"><div><label class="lbl">Min °C</label><input name="minTemp" type="number" step="0.1" value="2" class="field" required></div>
    <div><label class="lbl">Max °C</label><input name="maxTemp" type="number" step="0.1" value="8" class="field" required></div></div>
    <p id="ship-err" class="text-red-600 text-sm mt-2"></p>
    <div class="flex justify-end gap-2 pt-4"><button type="button" id="ship-cancel" class="btn">Cancel</button><button class="btn btn-primary">Create</button></div>
  </form>
</dialog>

<section id="view-driver" class="hidden p-3 md:p-6 max-w-xl mx-auto space-y-4 pb-24">
  <div><h2 id="d-title" class="text-xl font-bold">Assigned deliveries</h2><p class="text-sm text-slate-500">Tap a shipment to start the handoff</p></div>
  <div id="assigned" class="space-y-2"></div>
  <div id="detail" class="card"></div>
  <div class="card">
    <h2 class="h2"><span class="num">1</span>Scan package QR</h2>
    <div id="qr-reader" class="w-full rounded-xl overflow-hidden"></div>
    <button id="btn-scan" class="btn btn-primary w-full mt-2">📷  Start Camera</button>
    <input id="shipment-id" class="field mt-3" placeholder="Or type Shipment ID">
    <select id="handoff-type" class="field mt-2"><option value="PICKUP">Pickup (warehouse)</option><option value="DELIVERY">Delivery (clinic)</option></select>
  </div>
  <div class="card">
    <h2 class="h2"><span class="num">2</span>Package condition</h2>
    <div class="grid grid-cols-3 gap-2" id="cond"><button class="btn cond-btn" data-v="INTACT">Intact</button><button class="btn cond-btn" data-v="DAMAGED">Damaged</button><button class="btn cond-btn" data-v="SEAL_BROKEN">Seal Broken</button></div>
  </div>
  <div class="card">
    <h2 class="h2"><span class="num">3</span>PIN or signature</h2>
    <div class="grid grid-cols-2 gap-2 mb-3"><button class="btn vm-btn" data-m="PIN">PIN</button><button class="btn vm-btn" data-m="SIGNATURE">Signature</button></div>
    <div id="pin-box" class="hidden"><div id="pin-dots" class="text-3xl tracking-[0.5em] text-center h-10 text-teal-700"></div><div id="pinpad" class="grid grid-cols-3 gap-2"></div></div>
    <div id="sig-box" class="hidden"><canvas id="sig" class="sig" width="400" height="180"></canvas><button id="sig-clear" class="btn w-full mt-2">Clear</button></div>
  </div>
  <button id="btn-submit" class="btn btn-ok w-full text-lg">Confirm Handoff</button>
  <div id="clinic-actions" class="hidden grid grid-cols-2 gap-3"><button id="btn-reject" class="btn btn-bad text-lg">Reject</button><button id="btn-accept" class="btn btn-ok text-lg">Accept</button></div>
  <p id="driver-msg" class="text-center text-sm text-slate-600"></p>
</section>

<section id="view-audit" class="hidden p-4 md:p-6 space-y-4">
  <div class="flex flex-wrap gap-2 items-end">
    <div class="mr-auto"><h2 class="text-xl font-bold">Compliance &amp; Audit</h2><p class="text-sm text-slate-500">Append-only records · read-only access</p></div>
    <button id="btn-csv" class="btn btn-dark">Export CSV</button><button id="btn-pdf" class="btn btn-primary">PDF Batch Report</button>
  </div>
  <div class="grid grid-cols-3 gap-3"><div class="kpi"><span>Records</span><b id="a-all">–</b></div><div class="kpi"><span>Handoffs</span><b id="a-ho">–</b></div><div class="kpi"><span><i style="background:#dc2626"></i>SLA breaches</span><b id="a-sla">–</b></div></div>
  <div class="panel"><div class="phead">Temperature history <small>safe band 2–8°C</small></div><div id="chart" class="p-3"></div></div>
  <div class="panel overflow-x-auto">
    <div class="tabs"><button class="tab on" data-f="">All</button><button class="tab" data-f="TEMP">Temperature</button><button class="tab" data-f="HANDOFF">Handoffs</button><button class="tab" data-f="SLA_BREACH">SLA breaches</button></div>
    <table class="w-full text-sm"><thead class="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500"><tr><th class="p-3">Time</th><th>Type</th><th>Shipment</th><th>Detail</th><th>GPS</th></tr></thead><tbody id="audit-body"></tbody></table>
  </div>
</section>
</div></div>

<script type="module" src="js/app.js"></script>
</body>
</html>"""


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
    if os.environ.get("COLDCHAIN_DISABLE_SIMULATOR") != "1" and not os.environ.get("VERCEL"):
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
    f_dir = get_frontend_dir()
    rel = full_path.lstrip("/") or "index.html"
    target = (f_dir / rel).resolve()
    try:
        target.relative_to(f_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        target = f_dir / "index.html"
    if not target.is_file():
        if rel == "index.html" or not rel.startswith(("js/", "css/", "icons/")):
            return Response(content=INDEX_HTML_FALLBACK, media_type="text/html")
        raise HTTPException(status_code=404, detail=f"Asset not found: {rel}")
    media_type, _ = mimetypes.guess_type(str(target))
    headers = {}
    if target.name == "sw.js":
        headers["Service-Worker-Allowed"] = "/"
        headers["Cache-Control"] = "no-cache"
    return Response(
        content=target.read_bytes(),
        media_type=media_type or "application/octet-stream",
        headers=headers,
    )
