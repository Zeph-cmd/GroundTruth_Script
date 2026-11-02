// GroundTruth Paystack webhook: verifies payment and issues a signed license code.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";
import {
  codePayloadToBytes,
  bytesToHex,
  generateSerial,
  formatCode,
  getExpiryDate,
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
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
    );

    const paystackSecret = Deno.env.get("PAYSTACK_SECRET_KEY");
    const privateKeyPem = Deno.env.get("LICENSE_PRIVATE_KEY");
    if (!paystackSecret || !privateKeyPem) {
      throw new Error("Missing environment secrets");
    }

    // Optional: verify Paystack signature header
    // const signature = req.headers.get("x-paystack-signature");
    // const body = await req.text();
    // TODO: implement HMAC-SHA512 verification if header present

    const event = await req.json();

    if (event.event !== "charge.success") {
      return new Response(JSON.stringify({ received: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 200,
      });
    }

    const data = event.data;
    const ref = data.reference as string;
    const amount = data.amount as number; // already in cents
    const email = data.customer?.email as string | undefined;
    const metadata = data.metadata || {};
    const plan = (metadata.plan as string)?.toUpperCase();
    const country = (metadata.country as string) || "";
    const purpose = (metadata.purpose as string) || "";

    if (!ref || !plan) {
      return new Response(JSON.stringify({ error: "Missing reference or plan" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 400,
      });
    }

    // Verify plan amount
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

    // Record payment
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

    // Check if license already issued for this payment
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

    // Generate and sign license
    const serial = generateSerial();
    const expiryDate = getExpiryDate(plan);
    const expiryDays = Math.floor((expiryDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24)) + 1;
    const payload = codePayloadToBytes(serial, plan, expiryDays, 1, -1);
    const signature = await signBytes(privateKeyPem, payload);

    // Code is human-friendly base32 of payload + signature
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
    console.error("Webhook error:", err);
    return new Response(JSON.stringify({ error: err.message }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      status: 500,
    });
  }
});
