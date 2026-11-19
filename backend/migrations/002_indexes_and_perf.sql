-- 002_indexes_and_perf.sql: Performance indexes for 500+ concurrent shipments & audit queries

CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status);
CREATE INDEX IF NOT EXISTS idx_shipments_vehicle ON shipments(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_shipments_destination ON shipments(destination);
CREATE INDEX IF NOT EXISTS idx_telemetry_shipment_ts ON telemetry_logs(shipment_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_handoffs_shipment ON handoffs(shipment_id, synced_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ts_type ON audit_logs(id DESC, type);
CREATE INDEX IF NOT EXISTS idx_failed_logins_email_ts ON failed_logins(email, attempted_at DESC);
