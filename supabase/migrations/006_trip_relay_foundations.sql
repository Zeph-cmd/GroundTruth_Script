ALTER TABLE groundtruth.trip_relay_config
    ADD COLUMN IF NOT EXISTS max_reserved_bytes bigint NOT NULL DEFAULT 1000000000
    CHECK (max_reserved_bytes > 0);

CREATE TABLE groundtruth.trip_relay_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trial_payment_id uuid NOT NULL UNIQUE REFERENCES groundtruth.payments(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE groundtruth.trip_relay_account_licenses (
    license_id uuid PRIMARY KEY REFERENCES groundtruth.licenses(id),
    account_id uuid NOT NULL REFERENCES groundtruth.trip_relay_accounts(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, license_id)
);

CREATE TABLE groundtruth.trip_relay_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES groundtruth.trip_relay_accounts(id),
    license_id uuid NOT NULL,
    device_type text NOT NULL CHECK (device_type IN ('pc', 'android')),
    credential_hash bytea NOT NULL UNIQUE CHECK (octet_length(credential_hash) = 32),
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, id),
    FOREIGN KEY (account_id, license_id)
        REFERENCES groundtruth.trip_relay_account_licenses(account_id, license_id)
);

CREATE TABLE groundtruth.trip_relay_trips (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES groundtruth.trip_relay_accounts(id),
    device_id uuid NOT NULL,
    object_key text NOT NULL UNIQUE,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32),
    state text NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'available', 'delete_pending', 'deleted')),
    reserved_at timestamptz NOT NULL DEFAULT now(),
    uploaded_at timestamptz,
    expires_at timestamptz,
    downloaded_at timestamptz,
    FOREIGN KEY (account_id, device_id)
        REFERENCES groundtruth.trip_relay_devices(account_id, id),
    CHECK ((uploaded_at IS NULL AND expires_at IS NULL)
        OR (uploaded_at IS NOT NULL AND expires_at IS NOT NULL
            AND expires_at = uploaded_at + interval '6 hours')),
    CHECK (state <> 'available' OR uploaded_at IS NOT NULL),
    CHECK (downloaded_at IS NULL OR state IN ('delete_pending', 'deleted'))
);

CREATE INDEX trip_relay_trips_account_state_idx
    ON groundtruth.trip_relay_trips (account_id, state);

ALTER TABLE groundtruth.trip_relay_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.trip_relay_account_licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.trip_relay_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.trip_relay_trips ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON groundtruth.trip_relay_accounts, groundtruth.trip_relay_account_licenses,
    groundtruth.trip_relay_devices, groundtruth.trip_relay_trips FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON groundtruth.trip_relay_accounts, groundtruth.trip_relay_account_licenses,
    groundtruth.trip_relay_devices, groundtruth.trip_relay_trips TO service_role;

CREATE FUNCTION groundtruth.reserve_trip_relay_upload(
    device_credential_hash bytea,
    requested_size bigint,
    requested_sha256 bytea
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    settings groundtruth.trip_relay_config%ROWTYPE;
    device groundtruth.trip_relay_devices%ROWTYPE;
    occupied_count bigint;
    occupied_bytes numeric;
    trip_id uuid;
BEGIN
    SELECT * INTO settings FROM groundtruth.trip_relay_config WHERE singleton FOR UPDATE;
    IF NOT FOUND OR NOT settings.enabled THEN
        RAISE EXCEPTION 'relay_disabled';
    END IF;

    IF requested_size IS NULL OR requested_size <= 0 OR requested_size > settings.max_trip_bytes
       OR requested_sha256 IS NULL OR octet_length(requested_sha256) <> 32
       OR device_credential_hash IS NULL OR octet_length(device_credential_hash) <> 32 THEN
        RAISE EXCEPTION 'invalid_request';
    END IF;

    SELECT relay_device.* INTO device
    FROM groundtruth.trip_relay_devices AS relay_device
    JOIN groundtruth.licenses AS license ON license.id = relay_device.license_id
    WHERE relay_device.credential_hash = device_credential_hash
      AND relay_device.device_type = 'android'
      AND NOT relay_device.revoked
      AND NOT license.revoked
      AND license.plan IN ('30D', '1Y')
      AND license.expiry_date >= (now() AT TIME ZONE 'UTC')::date;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    SELECT count(*) INTO occupied_count
    FROM groundtruth.trip_relay_trips
    WHERE account_id = device.account_id AND state <> 'deleted';
    IF occupied_count >= settings.max_active_per_customer THEN
        RAISE EXCEPTION 'trip_limit_reached';
    END IF;

    SELECT coalesce(sum(size_bytes), 0) INTO occupied_bytes
    FROM groundtruth.trip_relay_trips WHERE state <> 'deleted';
    IF occupied_bytes + requested_size > settings.max_reserved_bytes THEN
        RAISE EXCEPTION 'storage_full';
    END IF;

    trip_id := gen_random_uuid();
    INSERT INTO groundtruth.trip_relay_trips (id, account_id, device_id, object_key, size_bytes, sha256)
    VALUES (trip_id, device.account_id, device.id, trip_id::text || '.rvtrip', requested_size, requested_sha256);
    RETURN trip_id;
END;
$$;

REVOKE ALL ON FUNCTION groundtruth.reserve_trip_relay_upload(bytea, bigint, bytea)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION groundtruth.reserve_trip_relay_upload(bytea, bigint, bytea)
    TO service_role;
