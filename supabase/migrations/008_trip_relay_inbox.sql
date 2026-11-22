-- Inbox / download side of the trip relay.
-- All functions are service-only: the trip-relay Edge Function calls them
-- with the service role after validating the caller's device credential.

ALTER TABLE groundtruth.trip_relay_trips
    ADD COLUMN claim_token_hash bytea
        CHECK (claim_token_hash IS NULL OR octet_length(claim_token_hash) = 32),
    ADD COLUMN claim_expires_at timestamptz,
    ADD CONSTRAINT trip_relay_trips_claim_pair_check
        CHECK ((claim_token_hash IS NULL) = (claim_expires_at IS NULL)),
    ADD CONSTRAINT trip_relay_trips_claim_needs_upload_check
        CHECK (claim_token_hash IS NULL OR uploaded_at IS NOT NULL);

-- ---------------------------------------------------------------------------
-- Shared device-authorization helper (internal): returns the relay device
-- row for a credential hash of the requested type on an active paid license.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth._trip_relay_device_for(
    device_credential_hash bytea,
    wanted_device_type text
) RETURNS groundtruth.trip_relay_devices
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    device groundtruth.trip_relay_devices%ROWTYPE;
BEGIN
    IF device_credential_hash IS NULL OR octet_length(device_credential_hash) <> 32 THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;
    SELECT relay_device.* INTO device
    FROM groundtruth.trip_relay_devices AS relay_device
    JOIN groundtruth.licenses AS license ON license.id = relay_device.license_id
    WHERE relay_device.credential_hash = device_credential_hash
      AND relay_device.device_type = wanted_device_type
      AND NOT relay_device.revoked
      AND NOT license.revoked
      AND license.plan IN ('30D', '1Y')
      AND license.expiry_date >= (now() AT TIME ZONE 'UTC')::date;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;
    RETURN device;
END;
$$;

-- ---------------------------------------------------------------------------
-- Upload completion (called by the Android side once the object is stored).
-- The declared sha256 must match the reservation; the 6-hour window starts
-- exactly when this commits.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth.complete_trip_relay_upload(
    device_credential_hash bytea,
    target_trip_id uuid,
    uploaded_sha256 bytea
) RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    settings groundtruth.trip_relay_config%ROWTYPE;
    device groundtruth.trip_relay_devices%ROWTYPE;
    trip groundtruth.trip_relay_trips%ROWTYPE;
    deadline timestamptz;
BEGIN
    SELECT * INTO settings FROM groundtruth.trip_relay_config WHERE singleton FOR UPDATE;
    IF NOT FOUND OR NOT settings.enabled THEN
        RAISE EXCEPTION 'relay_disabled';
    END IF;

    IF uploaded_sha256 IS NULL OR octet_length(uploaded_sha256) <> 32 THEN
        RAISE EXCEPTION 'invalid_request';
    END IF;

    device := groundtruth._trip_relay_device_for(device_credential_hash, 'android');

    SELECT * INTO trip FROM groundtruth.trip_relay_trips
    WHERE id = target_trip_id AND account_id = device.account_id FOR UPDATE;
    IF NOT FOUND OR trip.state <> 'reserved' OR trip.sha256 <> uploaded_sha256 THEN
        RAISE EXCEPTION 'not_reservable';
    END IF;

    deadline := (now() AT TIME ZONE 'UTC') + interval '6 hours';
    UPDATE groundtruth.trip_relay_trips
    SET uploaded_at = (now() AT TIME ZONE 'UTC'),
        expires_at = deadline,
        state = 'available'
    WHERE id = trip.id;
    RETURN deadline;
END;
$$;

-- ---------------------------------------------------------------------------
-- Inbox listing (Explorer). Also performs housekeeping transitions:
-- stale claims are released, expired trips become delete_pending.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth.list_trip_relay_inbox(
    device_credential_hash bytea
) RETURNS TABLE (
    trip_id uuid,
    object_key text,
    size_bytes bigint,
    state text,
    uploaded_at timestamptz,
    expires_at timestamptz,
    claimed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    device groundtruth.trip_relay_devices%ROWTYPE;
BEGIN
    device := groundtruth._trip_relay_device_for(device_credential_hash, 'pc');

    UPDATE groundtruth.trip_relay_trips AS t
    SET claim_token_hash = NULL, claim_expires_at = NULL
    WHERE t.account_id = device.account_id
      AND t.state = 'available'
      AND t.claim_expires_at IS NOT NULL
      AND t.claim_expires_at <= (now() AT TIME ZONE 'UTC');

    UPDATE groundtruth.trip_relay_trips AS t
    SET state = 'delete_pending'
    WHERE t.account_id = device.account_id
      AND t.state = 'available'
      AND t.expires_at IS NOT NULL
      AND t.expires_at <= (now() AT TIME ZONE 'UTC');

    RETURN QUERY
    SELECT t.id, t.object_key, t.size_bytes, t.state, t.uploaded_at, t.expires_at,
           t.claim_token_hash IS NOT NULL
    FROM groundtruth.trip_relay_trips AS t
    WHERE t.account_id = device.account_id
      AND t.state = 'available'
      AND t.expires_at > (now() AT TIME ZONE 'UTC')
    ORDER BY t.uploaded_at DESC;
END;
$$;

-- ---------------------------------------------------------------------------
-- Atomic download claim (Explorer). While an unexpired claim exists no other
-- claim can start; a stale claim is silently replaced. The state stays
-- 'available' during the transfer so an interrupted download can retry
-- within the window with a fresh claim.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth.claim_trip_relay_download(
    device_credential_hash bytea,
    target_trip_id uuid,
    new_claim_token_hash bytea
) RETURNS TABLE (claim_deadline timestamptz, object_key text, window_ends timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    device groundtruth.trip_relay_devices%ROWTYPE;
    trip groundtruth.trip_relay_trips%ROWTYPE;
    deadline timestamptz;
BEGIN
    IF new_claim_token_hash IS NULL OR octet_length(new_claim_token_hash) <> 32 THEN
        RAISE EXCEPTION 'invalid_request';
    END IF;

    device := groundtruth._trip_relay_device_for(device_credential_hash, 'pc');

    SELECT * INTO trip FROM groundtruth.trip_relay_trips
    WHERE id = target_trip_id AND account_id = device.account_id FOR UPDATE;
    IF NOT FOUND OR trip.state <> 'available' OR trip.expires_at IS NULL
       OR trip.expires_at <= (now() AT TIME ZONE 'UTC') THEN
        RAISE EXCEPTION 'not_available';
    END IF;

    IF trip.claim_token_hash IS NOT NULL
       AND trip.claim_expires_at > (now() AT TIME ZONE 'UTC') THEN
        RAISE EXCEPTION 'download_in_progress';
    END IF;

    deadline := (now() AT TIME ZONE 'UTC') + interval '15 minutes';
    UPDATE groundtruth.trip_relay_trips
    SET claim_token_hash = new_claim_token_hash,
        claim_expires_at = deadline
    WHERE id = trip.id;
    RETURN QUERY SELECT deadline, trip.object_key, trip.expires_at;
END;
$$;

-- ---------------------------------------------------------------------------
-- Download acknowledgment (Explorer). Called only after the complete package
-- has been saved and its size + sha256 verified locally. Marks the trip for
-- deletion so the link/object cannot be used again.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth.acknowledge_trip_relay_download(
    device_credential_hash bytea,
    target_trip_id uuid,
    ack_token_hash bytea
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    device groundtruth.trip_relay_devices%ROWTYPE;
    trip groundtruth.trip_relay_trips%ROWTYPE;
BEGIN
    IF ack_token_hash IS NULL OR octet_length(ack_token_hash) <> 32 THEN
        RAISE EXCEPTION 'invalid_request';
    END IF;

    device := groundtruth._trip_relay_device_for(device_credential_hash, 'pc');

    SELECT * INTO trip FROM groundtruth.trip_relay_trips
    WHERE id = target_trip_id AND account_id = device.account_id FOR UPDATE;
    IF NOT FOUND OR trip.state <> 'available' OR trip.downloaded_at IS NOT NULL
       OR trip.claim_token_hash IS NULL OR trip.claim_token_hash <> ack_token_hash
       OR trip.expires_at IS NULL OR trip.expires_at <= (now() AT TIME ZONE 'UTC') THEN
        RAISE EXCEPTION 'not_acknowledgeable';
    END IF;

    UPDATE groundtruth.trip_relay_trips
    SET downloaded_at = (now() AT TIME ZONE 'UTC'),
        state = 'delete_pending',
        claim_token_hash = NULL,
        claim_expires_at = NULL
    WHERE id = trip.id;
END;
$$;

-- ---------------------------------------------------------------------------
-- Server-side sweep: transitions expired/abandoned rows to delete_pending and
-- returns the object keys the caller must remove from Storage, plus rows that
-- were already delete_pending but not yet removed from Storage.
-- ---------------------------------------------------------------------------
CREATE FUNCTION groundtruth.sweep_trip_relay(
    reservation_timeout_hours integer DEFAULT 24
) RETURNS TABLE (object_key text, trip_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
BEGIN
    IF reservation_timeout_hours IS NULL OR reservation_timeout_hours < 6 THEN
        reservation_timeout_hours := 24;
    END IF;

    UPDATE groundtruth.trip_relay_trips
    SET claim_token_hash = NULL, claim_expires_at = NULL
    WHERE state = 'available'
      AND claim_expires_at IS NOT NULL
      AND claim_expires_at <= (now() AT TIME ZONE 'UTC');

    UPDATE groundtruth.trip_relay_trips
    SET state = 'delete_pending'
    WHERE (state = 'available' AND expires_at IS NOT NULL
             AND expires_at <= (now() AT TIME ZONE 'UTC'))
       OR (state = 'reserved'
             AND reserved_at <= (now() AT TIME ZONE 'UTC')
                 - make_interval(hours => reservation_timeout_hours));

    RETURN QUERY
    SELECT t.object_key, t.id
    FROM groundtruth.trip_relay_trips AS t
    WHERE t.state = 'delete_pending';
END;
$$;

-- Mark an object as removed from Storage (idempotent, delete_pending only).
CREATE FUNCTION groundtruth.mark_trip_relay_deleted(target_object_key text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
BEGIN
    UPDATE groundtruth.trip_relay_trips
    SET state = 'deleted'
    WHERE object_key = target_object_key AND state = 'delete_pending';
END;
$$;

-- ---------------------------------------------------------------------------
-- Grants: everything service-only, matching the 006/007 pattern.
-- ---------------------------------------------------------------------------
REVOKE ALL ON FUNCTION groundtruth._trip_relay_device_for(bytea, text)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.complete_trip_relay_upload(bytea, uuid, bytea)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.list_trip_relay_inbox(bytea)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.claim_trip_relay_download(bytea, uuid, bytea)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.acknowledge_trip_relay_download(bytea, uuid, bytea)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.sweep_trip_relay(integer)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION groundtruth.mark_trip_relay_deleted(text)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION groundtruth.complete_trip_relay_upload(bytea, uuid, bytea),
    groundtruth.list_trip_relay_inbox(bytea),
    groundtruth.claim_trip_relay_download(bytea, uuid, bytea),
    groundtruth.acknowledge_trip_relay_download(bytea, uuid, bytea),
    groundtruth.sweep_trip_relay(integer),
    groundtruth.mark_trip_relay_deleted(text)
    TO service_role;
