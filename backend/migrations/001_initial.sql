-- 001_initial.sql: Normalized ColdChain Pulse schema with append-only triggers

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ADMIN', 'DISPATCHER', 'DRIVER', 'CLINIC_RECEIVER', 'AUDITOR')),
    full_name TEXT NOT NULL,
    assigned_vehicle_id TEXT,
    assigned_clinic TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    plate_number TEXT NOT NULL UNIQUE,
    capacity_units INTEGER NOT NULL DEFAULT 500,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'MAINTENANCE', 'INACTIVE'))
);

CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    batch_code TEXT NOT NULL UNIQUE,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    driver_id TEXT REFERENCES users(id),
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    min_temp REAL NOT NULL DEFAULT 2.0,
    max_temp REAL NOT NULL DEFAULT 8.0,
    lat REAL NOT NULL DEFAULT 23.8103,
    lng REAL NOT NULL DEFAULT 90.4125,
    current_temp REAL NOT NULL DEFAULT 5.0,
    current_humidity REAL NOT NULL DEFAULT 55.0,
    status TEXT NOT NULL DEFAULT 'NORMAL' CHECK (status IN ('NORMAL', 'WARNING', 'CRITICAL', 'DELIVERED')),
    breach_started_at TEXT,
    breach_duration_minutes REAL NOT NULL DEFAULT 0.0,
    eta TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (min_temp < max_temp)
);

CREATE TABLE IF NOT EXISTS telemetry_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    temperature REAL NOT NULL,
    humidity REAL NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    handoff_type TEXT NOT NULL CHECK (handoff_type IN ('PICKUP', 'DELIVERY')),
    decision TEXT CHECK (decision IS NULL OR decision IN ('ACCEPTED', 'REJECTED')),
    condition_status TEXT NOT NULL CHECK (condition_status IN ('INTACT', 'DAMAGED', 'SEAL_BROKEN')),
    verification_method TEXT NOT NULL CHECK (verification_method IN ('PIN', 'SIGNATURE')),
    verification_data TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    client_timestamp TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('TEMP', 'HANDOFF', 'SLA_BREACH', 'AUTH_FAIL')),
    shipment_id TEXT NOT NULL,
    temp REAL,
    detail TEXT NOT NULL,
    lat REAL,
    lng REAL,
    prev_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS failed_logins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    reason TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);

-- Enforce strict append-only integrity on telemetry_logs, handoffs, and audit_logs
CREATE TRIGGER IF NOT EXISTS trg_telemetry_no_update
BEFORE UPDATE ON telemetry_logs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: telemetry_logs cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_telemetry_no_delete
BEFORE DELETE ON telemetry_logs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: telemetry_logs cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_handoffs_no_update
BEFORE UPDATE ON handoffs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: handoffs cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_handoffs_no_delete
BEFORE DELETE ON handoffs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: handoffs cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: audit_logs cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Append-only table: audit_logs cannot be deleted');
END;
