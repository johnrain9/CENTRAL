CREATE TABLE IF NOT EXISTS task_id_reservations (
    reservation_id TEXT PRIMARY KEY,
    series TEXT NOT NULL,
    start_number INTEGER NOT NULL,
    end_number INTEGER NOT NULL,
    reserved_by TEXT NOT NULL,
    reserved_for TEXT,
    note TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'expired')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (start_number > 0),
    CHECK (end_number >= start_number)
);

CREATE INDEX IF NOT EXISTS idx_task_id_reservations_series_status
ON task_id_reservations (series, status, start_number);

CREATE INDEX IF NOT EXISTS idx_task_id_reservations_expires_at
ON task_id_reservations (expires_at);

CREATE TABLE IF NOT EXISTS task_id_reservation_events (
    event_id INTEGER PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (reservation_id) REFERENCES task_id_reservations (reservation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_id_reservation_events_reservation
ON task_id_reservation_events (reservation_id, created_at);
