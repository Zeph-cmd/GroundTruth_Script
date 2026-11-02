// Test: full webhook without HMAC verification
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

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SERVICE_ROLE_KEY") ?? ""
    );

    const paystackSecret = Deno.env.get("PAYSTACK_SECRET_KEY");
    const privateKeyPem = Deno.env.get("LICENSE_PRIVATE_KEY");
    if (!paystackSecret || !privateKeyPem) {
      throw new Error("Missing environment secrets");
    }

    // Verify Paystack HMAC-SHA512 signature
    const sigHeader = req.headers.get("x-paystack-signature");
    if (!sigHeader || sigHeader.length !== 128 || !/^[0-9a-f]+$/i.test(sigHeader)) {
      return new Response(JSON.stringify({ error: "Missing or invalid signature" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 401,
      });
    }
    const rawBody = await req.text();
    {
      const encoder = new TextEncoder();
      const keyData = encoder.encode(paystackSecret);
      const bodyData = encoder.encode(rawBody);
      const key = await crypto.subtle.importKey(
        "raw",
        keyData,
        { name: "HMAC", hash: "SHA-512" },
        false,
        ["sign", "verify"]
      );
      const sigBytes = new Uint8Array(sigHeader.length / 2);
      for (let i = 0; i < sigHeader.length; i += 2) {
        sigBytes[i / 2] = parseInt(sigHeader.substring(i, i + 2), 16);
      }
      const valid = await crypto.subtle.verify("HMAC", key, sigBytes, bodyData);
      if (!valid) {
        return new Response(JSON.stringify({ error: "Invalid signature" }), {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 401,
        });
      }
    }

    const event = JSON.parse(rawBody);

    if (event.event !== "charge.success") {
      return new Response(JSON.stringify({ received: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    const data = event.data;
    const ref = data.reference as string;
    const amount = data.amount as number;
    const email = data.customer?.email as string | undefined;
    const metadata = data.metadata || {};
    const plan = (metadata.plan as string)?.toUpperCase();
    const country = (metadata.country as string) || "";
    const purpose = (metadata.purpose as string) || "";

    // Multiple apps share this Paystack account. Only process GroundTruth
    // payments (marked via metadata.product); silently ack everything else
    // so sibling webhooks (e.g. SmartJotter) never cause retries/failures.
    if (metadata.product !== "groundtruth") {
      return new Response(JSON.stringify({ received: true, ignored: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    if (!ref || !plan) {
      return new Response(JSON.stringify({ error: "Missing reference or plan" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    const { data: priceRow } = await supabase
      .schema("groundtruth")
      .from("prices")
      .select("amount_cents")
      .eq("plan", plan)
      .single();

    if (!priceRow || priceRow.amount_cents !== amount) {
      return new Response(JSON.stringify({ error: "Amount mismatch" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    // Defense in depth: never issue a paid license until the full trial has elapsed.
    const normalizedEmail = (email || "").trim().toLowerCase();
    const { data: trial } = await supabase
      .schema("groundtruth")
      .from("payments")
      .select("created_at")
      .ilike("email", normalizedEmail)
      .eq("plan", "TRIAL")
      .eq("status", "trial")
      .limit(1)
      .maybeSingle();
    const trialEndsAt = trial ? getExpiryDate("TRIAL", new Date(trial.created_at)).getTime() : 0;
    if (!trial || Date.now() < trialEndsAt) {
      const { error: rejectedErr } = await supabase
        .schema("groundtruth")
        .from("payments")
        .upsert({
          paystack_ref: ref,
          email: normalizedEmail || null,
          amount_cents: amount,
          plan,
          country,
          purpose,
          status: "trial_required",
          paystack_data: data,
        }, { onConflict: "paystack_ref" });
      if (rejectedErr) throw new Error("Failed to record blocked payment: " + rejectedErr.message);
      return new Response(JSON.stringify({ received: true, license_issued: false, reason: "trial_required" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    const { data: payment, error: payErr } = await supabase
      .schema("groundtruth")
      .from("payments")
      .upsert({
        paystack_ref: ref,
        email,
        amount_cents: amount,
        plan,
        country,
        purpose,
        status: "success",
        paystack_data: data,
      }, { onConflict: "paystack_ref" })
      .select()
      .single();

    if (payErr || !payment) {
      throw new Error("Failed to record payment: " + (payErr?.message || "unknown"));
    }

    const { data: existing } = await supabase
      .schema("groundtruth")
      .from("licenses")
      .select("code")
      .eq("payment_id", payment.id)
      .maybeSingle();

    if (existing?.code) {
      return new Response(JSON.stringify({ code: existing.code, already_issued: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    const serial = generateSerial();
    const expiryDate = getExpiryDate(plan);
    const expiryDays = daysSinceEpoch(expiryDate);
    const payload = codePayloadToBytes(serial, plan, expiryDays, 1, -1);
    const signature = await signBytes(privateKeyPem, payload);
    const code = formatCode(serial, plan, signature, payload);

    const { error: licErr } = await supabase
      .schema("groundtruth")
      .from("licenses")
      .insert({
        code,
        serial,
        plan,
        expiry_date: expiryDate.toISOString().split("T")[0],
        payment_id: payment.id,
        pc_seats_allowed: 1,
        android_seats_allowed: -1,
      });

    if (licErr) {
      throw new Error("Failed to insert license: " + licErr.message);
    }

    return new Response(JSON.stringify({ code, plan, expiry_date: expiryDate.toISOString().split("T")[0] }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 200,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("Webhook error:", msg);
    return new Response(JSON.stringify({ error: msg }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 500,
    });
  }
});
