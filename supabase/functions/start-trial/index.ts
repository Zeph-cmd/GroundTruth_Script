// GroundTruth free trial issuance.
// POST {email, country, purpose}          -> issue (or return existing) 15-day trial license
// POST {email, check_only: true}          -> report whether this email has started a trial

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";
import {
  codePayloadToBytes,
  generateSerial,
  formatCode,
  getExpiryDate,
  daysSinceEpoch,
  signBytes,
} from "../_shared/license.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 405,
    });
  }

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SERVICE_ROLE_KEY") ?? ""
    );

    const privateKeyPem = Deno.env.get("LICENSE_PRIVATE_KEY");
    if (!privateKeyPem) {
      throw new Error("Missing environment secrets");
    }

    const { email, country, purpose, check_only } = await req.json();

    if (!email || typeof email !== "string" || !EMAIL_RE.test(email.trim())) {
      return new Response(JSON.stringify({ error: "A valid email is required" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    const emailNorm = email.trim().toLowerCase();

    // Status check (used by checkout to gate paid plans on trial usage)
    if (check_only) {
      const { data: trialPayment } = await supabase
        .schema("groundtruth")
        .from("payments")
        .select("paystack_ref, plan, status")
        .ilike("email", emailNorm)
        .eq("plan", "TRIAL")
        .eq("status", "trial")
        .limit(1)
        .maybeSingle();

      return new Response(JSON.stringify({ has_trial: !!trialPayment }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    if (!country || !purpose) {
      return new Response(JSON.stringify({ error: "Country and purpose are required" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    // One trial per email: return the existing trial if already issued.
    const { data: existing } = await supabase
      .schema("groundtruth")
      .from("payments")
      .select("id, paystack_ref")
      .ilike("email", emailNorm)
      .eq("plan", "TRIAL")
      .eq("status", "trial")
      .limit(1)
      .maybeSingle();

    if (existing) {
      const { data: existingLicense } = await supabase
        .schema("groundtruth")
        .from("licenses")
        .select("code, expiry_date")
        .eq("payment_id", existing.id)
        .maybeSingle();

      if (existingLicense?.code) {
        return new Response(JSON.stringify({
          code: existingLicense.code,
          plan: "TRIAL",
          expiry_date: existingLicense.expiry_date,
          ref: existing.paystack_ref,
          already_issued: true,
        }), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 200,
        });
      }
    }

    // Record the trial as a zero-amount payment so the success page
    // lookup (activate-license?ref=...) works exactly like paid flow.
    const ref = "TRIAL-" + Date.now() + "-" + Math.floor(Math.random() * 10000);
    const { data: payment, error: payErr } = await supabase
      .schema("groundtruth")
      .from("payments")
      .insert({
        paystack_ref: ref,
        email: emailNorm,
        amount_cents: 0,
        plan: "TRIAL",
        country,
        purpose,
        status: "trial",
        paystack_data: { trial: true, issued_at: new Date().toISOString() },
      })
      .select()
      .single();

    if (payErr || !payment) {
      throw new Error("Failed to record trial: " + (payErr?.message || "unknown"));
    }

    const serial = generateSerial();
    const expiryDate = getExpiryDate("TRIAL");
    const expiryDays = daysSinceEpoch(expiryDate);
    const payload = codePayloadToBytes(serial, "TRIAL", expiryDays, 1, -1);
    const signature = await signBytes(privateKeyPem, payload);
    const code = formatCode(serial, "TRIAL", signature, payload);

    const { error: licErr } = await supabase
      .schema("groundtruth")
      .from("licenses")
      .insert({
        code,
        serial,
        plan: "TRIAL",
        expiry_date: expiryDate.toISOString().split("T")[0],
        payment_id: payment.id,
        pc_seats_allowed: 1,
        android_seats_allowed: -1,
      });

    if (licErr) {
      throw new Error("Failed to insert trial license: " + licErr.message);
    }

    return new Response(JSON.stringify({
      code,
      plan: "TRIAL",
      expiry_date: expiryDate.toISOString().split("T")[0],
      ref,
    }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 200,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("start-trial error:", msg);
    return new Response(JSON.stringify({ error: msg }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 500,
    });
  }
});
