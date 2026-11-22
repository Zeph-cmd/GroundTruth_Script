// GroundTruth trip relay (cloud trip sharing).
//
// SECURITY MODEL
// - The feature is disabled by default via groundtruth.trip_relay_config
//   (a check constraint prevents enabling it until the seller is ready).
// - Every device call carries a 32-byte secret credential in the
//   "x-relay-credential" header. Only its SHA-256 hash is stored server-side.
// - All database access goes through service-only RPCs (migrations 006-008);
//   the relay tables are never exposed to anon/authenticated roles.
// - Share tokens (claim tokens) are generated here, returned once over HTTPS,
//   and only their SHA-256 hash is persisted. They are never logged.
//
// ACTIONS (POST JSON body {action, ...}, credential header required except
// for "status"):
//   enroll   {license_code, device_type, device_fingerprint}
//   reserve  {size_bytes, sha256_hex}            -> {trip_id, upload_url, upload_token}
//   complete {trip_id, sha256_hex}               -> {expires_at}
//   list                                     -> {trips: [...]}
//   claim    {trip_id}                            -> {claim_token, claim_expires_at}
//   ack      {trip_id, claim_token}               -> {ok: true}
//   sweep    (housekeeping; no credential needed) -> {removed, kept}
// GET returns {enabled} so clients can hide the feature.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-relay-credential",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Cache-Control": "no-store",
  "Content-Type": "application/json",
};

const BUCKET = "trip-relay";
const HEX64 = /^[0-9a-f]{64}$/;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders });
}

function fail(code: string, status = 403) {
  return json({ error: code }, status);
}

async function sha256HexAsync(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", bytes as unknown as ArrayBuffer),
  );
  return Array.from(digest).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function bytesToHexParam(bytes: Uint8Array): string {
  return "\\x" + Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Naive per-instance rate limiting: 60 requests / 5 minutes per IP.
const rate = new Map<string, { count: number; reset: number }>();
function rateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = rate.get(ip);
  if (!entry || entry.reset < now) {
    rate.set(ip, { count: 1, reset: now + 5 * 60 * 1000 });
    return false;
  }
  entry.count += 1;
  return entry.count > 60;
}

// Never cache the credential across requests: edge isolates are reused and a
// cached value would leak one caller's secret to another.
function getCredential(request: Request): Uint8Array | null {
  const header = request.headers.get("x-relay-credential") || "";
  if (!HEX64.test(header)) return null;
  return hexToBytes(header);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type RelayClient = ReturnType<typeof createClient<any, any, any>>;

async function isRelayEnabled(supabase: RelayClient): Promise<boolean> {
  const { data, error } = await supabase
    .schema("groundtruth")
    .from("trip_relay_config")
    .select("enabled")
    .single();
  return !error && data?.enabled === true;
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  if (rateLimited(ip)) {
    return fail("rate_limited", 429);
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    // Supabase injects this automatically for deployed functions.
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SERVICE_ROLE_KEY") ?? "",
    // All relay RPCs and tables live in the "groundtruth" schema; PostgREST
    // only exposes "public" unless told otherwise.
    { db: { schema: "groundtruth" } },
  );

  try {
    // Status probe: always answers so clients can decide whether to show UI.
    if (request.method === "GET") {
      return json({ enabled: await isRelayEnabled(supabase) });
    }

    if (request.method !== "POST") {
      return fail("method_not_allowed", 405);
    }

    const body = await request.json().catch(() => null);
    const action = typeof body?.action === "string" ? body.action : "";

    // Sweep is housekeeping triggered by a cron/scheduled job. It only touches
    // expired or abandoned relay data.
    if (action === "sweep") {
      if (!(await isRelayEnabled(supabase))) {
        return fail("relay_disabled", 503);
      }
      const { data, error } = await supabase.rpc("sweep_trip_relay");
      if (error) return fail("sweep_failed", 500);
      let removed = 0;
      for (const row of data ?? []) {
        try {
          await supabase.storage.from(BUCKET).remove([row.object_key]);
        } catch {
          // Keep delete_pending; the next sweep retries the removal.
          continue;
        }
        await supabase.rpc("mark_trip_relay_deleted", { target_object_key: row.object_key });
        removed += 1;
      }
      return json({ removed, kept: (data ?? []).length - removed });
    }

    // Every other action requires the relay to be enabled server-side, even if
    // a client is modified.
    if (!(await isRelayEnabled(supabase))) {
      return fail("relay_disabled", 503);
    }

    const deviceCredential = getCredential(request);
    const credParam = deviceCredential ? bytesToHexParam(deviceCredential) : null;

    if (action === "enroll") {
      const { license_code, device_type, device_fingerprint } = body ?? {};
      if (!deviceCredential || !license_code || !device_type || !device_fingerprint) {
        return fail("invalid_request", 400);
      }
      const { data, error } = await supabase.rpc("enroll_trip_relay_device", {
        license_code: String(license_code),
        requested_device_type: String(device_type),
        requested_fingerprint: String(device_fingerprint),
        new_credential_hash: credParam,
      });
      if (error) return fail(mapDbError(error.message));
      return json({ device_id: data });
    }

    if (!deviceCredential) {
      return fail("unauthorized", 401);
    }

    if (action === "reserve") {
      const size = Number(body?.size_bytes);
      const sha = String(body?.sha256_hex || "");
      if (!Number.isFinite(size) || size <= 0 || !HEX64.test(sha)) {
        return fail("invalid_request", 400);
      }
      const { data: tripId, error } = await supabase.rpc("reserve_trip_relay_upload", {
        device_credential_hash: credParam,
        requested_size: size,
        requested_sha256: bytesToHexParam(hexToBytes(sha)),
      });
      if (error) return fail(mapDbError(error.message));
      const objectKey = `${tripId}.rvtrip`;
      const { data: upload, error: uploadError } = await supabase.storage
        .from(BUCKET)
        .createSignedUploadUrl(objectKey);
      if (uploadError || !upload) return fail("storage_unavailable", 503);
      return json({
        trip_id: tripId,
        upload_url: upload.signedUrl,
        upload_token: upload.token,
        object_key: objectKey,
      });
    }

    if (action === "complete") {
      const tripId = String(body?.trip_id || "");
      const sha = String(body?.sha256_hex || "");
      if (!tripId || !HEX64.test(sha)) return fail("invalid_request", 400);
      const { data, error } = await supabase.rpc("complete_trip_relay_upload", {
        device_credential_hash: credParam,
        target_trip_id: tripId,
        uploaded_sha256: bytesToHexParam(hexToBytes(sha)),
      });
      if (error) return fail(mapDbError(error.message));
      return json({ expires_at: data });
    }

    if (action === "list") {
      const { data, error } = await supabase.rpc("list_trip_relay_inbox", {
        device_credential_hash: credParam,
      });
      if (error) return fail(mapDbError(error.message));
      return json({ trips: data ?? [] });
    }

    if (action === "claim") {
      const tripId = String(body?.trip_id || "");
      if (!tripId) return fail("invalid_request", 400);
      const claimToken = crypto.getRandomValues(new Uint8Array(32));
      const tokenHash = await sha256HexAsync(claimToken);
      const { data, error } = await supabase.rpc("claim_trip_relay_download", {
        device_credential_hash: credParam,
        target_trip_id: tripId,
        new_claim_token_hash: bytesToHexParam(hexToBytes(tokenHash)),
      });
      if (error) return fail(mapDbError(error.message));
      const row = Array.isArray(data) ? data[0] : data;
      // The signed URL must not outlive the 6-hour trip window: bound its TTL
      // by the remaining window and the claim deadline. Honest limitation: a
      // download already in flight cannot be cut off at the exact deadline.
      const windowEnd = row?.window_ends ? new Date(row.window_ends).getTime() : Date.now();
      const ttlSeconds = Math.max(
        1,
        Math.min(15 * 60, Math.floor((windowEnd - Date.now()) / 1000)),
      );
      const { data: download, error: downloadError } = await supabase.storage
        .from(BUCKET)
        .createSignedUrl(row.object_key, ttlSeconds);
      if (downloadError || !download) return fail("storage_unavailable", 503);
      // The token is returned exactly once over HTTPS; only its hash is stored.
      return json({
        claim_token: Array.from(claimToken).map((b) => b.toString(16).padStart(2, "0")).join(""),
        claim_expires_at: row.claim_deadline,
        download_url: download.signedUrl,
      });
    }

    if (action === "ack") {
      const tripId = String(body?.trip_id || "");
      const token = String(body?.claim_token || "");
      if (!tripId || !HEX64.test(token)) return fail("invalid_request", 400);
      const tokenHash = await sha256HexAsync(hexToBytes(token));
      const { error } = await supabase.rpc("acknowledge_trip_relay_download", {
        device_credential_hash: credParam,
        target_trip_id: tripId,
        ack_token_hash: bytesToHexParam(hexToBytes(tokenHash)),
      });
      if (error) return fail(mapDbError(error.message));
      return json({ ok: true });
    }

    return fail("unknown_action", 400);
  } catch (err) {
    // Deliberately terse: never log credentials, tokens or license codes.
    console.error("trip-relay error:", err instanceof Error ? err.name : "unknown");
    return fail("internal_error", 500);
  }
});

function mapDbError(message: string): string {
  if (message.includes("relay_disabled")) return "relay_disabled";
  if (message.includes("trip_limit_reached")) return "trip_limit_reached";
  if (message.includes("storage_full")) return "storage_full";
  if (message.includes("unauthorized")) return "unauthorized";
  if (message.includes("not_reservable")) return "not_reservable";
  if (message.includes("not_available")) return "not_available";
  if (message.includes("download_in_progress")) return "download_in_progress";
  if (message.includes("not_acknowledgeable")) return "not_acknowledgeable";
  if (message.includes("invalid_request")) return "invalid_request";
  return "unauthorized";
}
