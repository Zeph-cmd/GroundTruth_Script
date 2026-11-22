CREATE TABLE IF NOT EXISTS groundtruth.trip_relay_config (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled boolean NOT NULL DEFAULT false CHECK (NOT enabled),
    max_trip_bytes bigint NOT NULL DEFAULT 200000000 CHECK (max_trip_bytes > 0),
    max_active_per_customer integer NOT NULL DEFAULT 5 CHECK (max_active_per_customer = 5),
    retention_seconds integer NOT NULL DEFAULT 21600 CHECK (retention_seconds = 21600)
);

INSERT INTO groundtruth.trip_relay_config (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE groundtruth.trip_relay_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON groundtruth.trip_relay_config FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON groundtruth.trip_relay_config TO service_role;
