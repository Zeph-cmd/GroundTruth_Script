ALTER TABLE groundtruth.license_activations
    ADD CONSTRAINT license_activations_id_license_device_key UNIQUE (id, license_id, device_type);

ALTER TABLE groundtruth.trip_relay_devices
    ADD COLUMN activation_id uuid NOT NULL,
    ADD CONSTRAINT trip_relay_devices_activation_fk
        FOREIGN KEY (activation_id, license_id, device_type)
        REFERENCES groundtruth.license_activations(id, license_id, device_type);

CREATE FUNCTION groundtruth.enroll_trip_relay_device(
    license_code text,
    requested_device_type text,
    requested_fingerprint text,
    new_credential_hash bytea
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, groundtruth
AS $$
DECLARE
    settings groundtruth.trip_relay_config%ROWTYPE;
    paid_license groundtruth.licenses%ROWTYPE;
    paid_payment groundtruth.payments%ROWTYPE;
    resolved_trial_id uuid;
    matching_trials bigint;
    resolved_account_id uuid;
    resolved_activation_id uuid;
    enrolled_device_id uuid;
    existing_device groundtruth.trip_relay_devices%ROWTYPE;
BEGIN
    SELECT * INTO settings FROM groundtruth.trip_relay_config WHERE singleton FOR UPDATE;
    IF NOT FOUND OR NOT settings.enabled THEN
        RAISE EXCEPTION 'relay_disabled';
    END IF;

    IF license_code IS NULL OR length(license_code) < 20 OR length(license_code) > 512
       OR requested_device_type IS NULL OR requested_device_type NOT IN ('pc', 'android')
       OR requested_fingerprint IS NULL OR length(requested_fingerprint) < 1
       OR length(requested_fingerprint) > 256
       OR new_credential_hash IS NULL OR octet_length(new_credential_hash) <> 32 THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    SELECT * INTO paid_license FROM groundtruth.licenses
    WHERE code = license_code AND plan IN ('30D', '1Y') AND NOT revoked
      AND expiry_date >= (now() AT TIME ZONE 'UTC')::date;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    SELECT * INTO paid_payment FROM groundtruth.payments
    WHERE id = paid_license.payment_id AND status = 'success'
      AND plan = paid_license.plan AND email IS NOT NULL AND length(trim(email)) > 0;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    SELECT count(*), min(payment.id::text)::uuid INTO matching_trials, resolved_trial_id
    FROM groundtruth.payments AS payment
    WHERE payment.plan = 'TRIAL' AND payment.status = 'trial'
      AND lower(trim(payment.email)) = lower(trim(paid_payment.email));
    IF matching_trials <> 1 THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    SELECT id INTO resolved_activation_id FROM groundtruth.license_activations
    WHERE license_id = paid_license.id AND device_type = requested_device_type
      AND device_fingerprint = requested_fingerprint;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM groundtruth.license_activations AS paid_pc
        JOIN groundtruth.licenses AS trial_license ON trial_license.payment_id = resolved_trial_id
            AND trial_license.plan = 'TRIAL'
        JOIN groundtruth.license_activations AS trial_pc ON trial_pc.license_id = trial_license.id
            AND trial_pc.device_type = 'pc'
            AND trial_pc.device_fingerprint = paid_pc.device_fingerprint
        WHERE paid_pc.license_id = paid_license.id AND paid_pc.device_type = 'pc'
    ) THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    INSERT INTO groundtruth.trip_relay_accounts (trial_payment_id)
    VALUES (resolved_trial_id)
    ON CONFLICT (trial_payment_id) DO NOTHING;
    SELECT id INTO resolved_account_id FROM groundtruth.trip_relay_accounts
    WHERE trip_relay_accounts.trial_payment_id = resolved_trial_id;

    INSERT INTO groundtruth.trip_relay_account_licenses (license_id, account_id)
    VALUES (paid_license.id, resolved_account_id)
    ON CONFLICT (license_id) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM groundtruth.trip_relay_account_licenses AS account_license
        WHERE account_license.license_id = paid_license.id AND account_license.account_id = resolved_account_id
    ) THEN
        RAISE EXCEPTION 'unauthorized';
    END IF;

    INSERT INTO groundtruth.trip_relay_devices (account_id, license_id, device_type, credential_hash, activation_id)
    VALUES (resolved_account_id, paid_license.id, requested_device_type, new_credential_hash, resolved_activation_id)
    ON CONFLICT (credential_hash) DO NOTHING
    RETURNING id INTO enrolled_device_id;

    IF enrolled_device_id IS NULL THEN
        SELECT * INTO existing_device FROM groundtruth.trip_relay_devices
        WHERE credential_hash = new_credential_hash;
        IF NOT FOUND OR existing_device.account_id <> resolved_account_id
           OR existing_device.license_id <> paid_license.id
           OR existing_device.activation_id <> resolved_activation_id
           OR existing_device.device_type <> requested_device_type OR existing_device.revoked THEN
            RAISE EXCEPTION 'unauthorized';
        END IF;
        enrolled_device_id := existing_device.id;
    END IF;

    RETURN enrolled_device_id;
END;
$$;

REVOKE ALL ON FUNCTION groundtruth.enroll_trip_relay_device(text, text, text, bytea)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION groundtruth.enroll_trip_relay_device(text, text, text, bytea)
    TO service_role;
