-- GroundTruth licensing + feedback schema for the kumtik Supabase project
-- Run this in the Supabase SQL Editor

CREATE SCHEMA IF NOT EXISTS groundtruth;

-- Plan pricing config
CREATE TABLE IF NOT EXISTS groundtruth.prices (
    plan text PRIMARY KEY,
    amount_cents integer NOT NULL,
    currency text NOT NULL DEFAULT 'USD',
    updated_at timestamptz DEFAULT now()
);

INSERT INTO groundtruth.prices (plan, amount_cents, currency)
VALUES
    ('30D', 3000, 'USD'),
    ('1Y', 35600, 'USD')
ON CONFLICT (plan) DO UPDATE SET
    amount_cents = EXCLUDED.amount_cents,
    currency = EXCLUDED.currency,
    updated_at = now();

-- Paystack payments
CREATE TABLE IF NOT EXISTS groundtruth.payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    paystack_ref text UNIQUE NOT NULL,
    email text,
    amount_cents integer NOT NULL,
    plan text NOT NULL,
    country text,
    purpose text,
    status text NOT NULL DEFAULT 'pending',
    paystack_data jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_ref ON groundtruth.payments(paystack_ref);
CREATE INDEX IF NOT EXISTS idx_payments_status ON groundtruth.payments(status);

-- Issued license codes
CREATE TABLE IF NOT EXISTS groundtruth.licenses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text UNIQUE NOT NULL,
    serial text UNIQUE NOT NULL,
    plan text NOT NULL,
    expiry_date date NOT NULL,
    payment_id uuid REFERENCES groundtruth.payments(id) ON DELETE SET NULL,
    pc_seats_allowed integer NOT NULL DEFAULT 1,
    android_seats_allowed integer NOT NULL DEFAULT -1, -- -1 means unlimited
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_licenses_code ON groundtruth.licenses(code);
CREATE INDEX IF NOT EXISTS idx_licenses_serial ON groundtruth.licenses(serial);

-- Seat activations per license
CREATE TABLE IF NOT EXISTS groundtruth.license_activations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id uuid NOT NULL REFERENCES groundtruth.licenses(id) ON DELETE CASCADE,
    device_type text NOT NULL CHECK (device_type IN ('pc', 'android')),
    device_fingerprint text NOT NULL,
    activated_at timestamptz DEFAULT now(),
    last_seen timestamptz DEFAULT now(),
    UNIQUE (license_id, device_type, device_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_activations_license ON groundtruth.license_activations(license_id);
CREATE INDEX IF NOT EXISTS idx_activations_fingerprint ON groundtruth.license_activations(device_fingerprint);

-- Public feedback
CREATE TABLE IF NOT EXISTS groundtruth.feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type text NOT NULL CHECK (type IN ('praise', 'criticism', 'suggestion', 'other')),
    country text,
    email text,
    message text NOT NULL,
    created_at timestamptz DEFAULT now()
);

-- RLS: disable public direct access. Edge Functions use service_role.
ALTER TABLE groundtruth.payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.license_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE groundtruth.feedback ENABLE ROW LEVEL SECURITY;

CREATE POLICY deny_all_payments ON groundtruth.payments FOR ALL TO public USING (false) WITH CHECK (false);
CREATE POLICY deny_all_licenses ON groundtruth.licenses FOR ALL TO public USING (false) WITH CHECK (false);
CREATE POLICY deny_all_activations ON groundtruth.license_activations FOR ALL TO public USING (false) WITH CHECK (false);
CREATE POLICY allow_feedback_insert ON groundtruth.feedback FOR INSERT TO public WITH CHECK (true);
CREATE POLICY deny_feedback_select ON groundtruth.feedback FOR SELECT TO public USING (false);

COMMENT ON SCHEMA groundtruth IS 'GroundTruth licensing, payments, and feedback data';
