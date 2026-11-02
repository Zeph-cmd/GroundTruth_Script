// GroundTruth license activation endpoint.
// GET  ?ref=paystack_reference   -> retrieve license code for success page
// POST {code, device_type, device_fingerprint} -> activate / check seat

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";
import { verifySignature, PLAN_DAYS } from "../_shared/license.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const PUBLIC_KEY_PEM = Deno.env.get("LICENSE_PUBLIC_KEY") || "";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL") ?? "",
    Deno.env.get("SERVICE_ROLE_KEY") ?? ""
  );

  try {
    // GET lookup by payment reference (success page polling)
    if (req.method === "GET") {
      const url = new URL(req.url);
      const ref = url.searchParams.get("ref");
      if (!ref) {
        return new Response(JSON.stringify({ error: "Missing ref" }), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 400,
        });
      }

      const { data: license } = await supabase
        .schema("groundtruth")
        .from("licenses")
        .select("code, plan, expiry_date, revoked")
        .eq("payment_id", (
          await supabase.schema("groundtruth").from("payments").select("id").eq("paystack_ref", ref).single()
        ).data?.id)
        .maybeSingle();

      if (!license) {
        return new Response(JSON.stringify({ found: false }), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 200,
        });
      }

      return new Response(JSON.stringify({
        found: true,
        code: license.code,
        plan: license.plan,
        expiry_date: license.expiry_date,
        revoked: license.revoked,
      }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    // POST activation
    if (req.method !== "POST") {
      return new Response(JSON.stringify({ error: "Method not allowed" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 405,
      });
    }

    const { code, device_type, device_fingerprint } = await req.json();

    if (!code || !device_type || !device_fingerprint) {
      return new Response(JSON.stringify({ error: "Missing code, device_type or device_fingerprint" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    if (!["pc", "android"].includes(device_type)) {
      return new Response(JSON.stringify({ error: "Invalid device_type" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    // Lookup license
    const { data: license } = await supabase
      .schema("groundtruth")
      .from("licenses")
      .select("id, code, serial, plan, expiry_date, revoked, pc_seats_allowed, android_seats_allowed")
      .eq("code", code)
      .single();

    if (!license) {
      return new Response(JSON.stringify({ status: "invalid" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    if (license.revoked) {
      return new Response(JSON.stringify({ status: "revoked", expiry_date: license.expiry_date }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const expiry = new Date(license.expiry_date);
    if (expiry < today) {
      return new Response(JSON.stringify({ status: "expired", expiry_date: license.expiry_date }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    // Seat check
    const allowedSeats = device_type === "pc" ? license.pc_seats_allowed : license.android_seats_allowed;
    const isUnlimited = allowedSeats === -1;

    if (!isUnlimited) {
      const { data: existingActivations } = await supabase
        .schema("groundtruth")
        .from("license_activations")
        .select("device_fingerprint")
        .eq("license_id", license.id)
        .eq("device_type", device_type);

      const alreadyActivated = existingActivations?.some(
        (a) => a.device_fingerprint === device_fingerprint
      );

      if (!alreadyActivated && (existingActivations?.length || 0) >= allowedSeats) {
        return new Response(JSON.stringify({ status: "seat_taken" }), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 200,
        });
      }
    }

    // Record / update activation
    await supabase
      .schema("groundtruth")
      .from("license_activations")
      .upsert({
        license_id: license.id,
        device_type,
        device_fingerprint,
        last_seen: new Date().toISOString(),
      }, { onConflict: "license_id, device_type, device_fingerprint" });

    return new Response(JSON.stringify({
      status: "active",
      plan: license.plan,
      expiry_date: license.expiry_date,
      pc_seats: license.pc_seats_allowed,
      android_seats: license.android_seats_allowed,
    }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 200,
    });
  } catch (err) {
    console.error("Activation error:", err);
    return new Response(JSON.stringify({ error: err.message }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 500,
    });
  }
});
