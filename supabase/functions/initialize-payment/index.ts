// Initialize a paid GroundTruth checkout only after the full 15-day trial.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";
import { getExpiryDate } from "../_shared/license.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DEVICE_ID_RE = /^GT-[A-Z2-7]{4}(?:-[A-Z2-7]{4}){2}$/;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 405,
    });
  }

  try {
    const secret = Deno.env.get("PAYSTACK_SECRET_KEY");
    if (!secret) throw new Error("Payment service is not configured");

    const { email, plan, country, purpose, device_id } = await req.json();
    if (typeof email !== "string" || !EMAIL_RE.test(email.trim())) {
      return new Response(JSON.stringify({ error: "A valid email is required" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 400,
      });
    }
    if (!country || !purpose || !["30D", "1Y"].includes(plan) || !DEVICE_ID_RE.test(String(device_id || ""))) {
      return new Response(JSON.stringify({ error: "A valid Windows Device ID and questionnaire details are required" }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 400,
      });
    }

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SERVICE_ROLE_KEY") ?? "",
    );
    const emailNorm = email.trim().toLowerCase();
    const { data: trial } = await supabase
      .schema("groundtruth")
      .from("payments")
      .select("id, created_at")
      .eq("email", emailNorm)
      .eq("plan", "TRIAL")
      .eq("status", "trial")
      .limit(1)
      .maybeSingle();

    if (!trial) {
      return new Response(JSON.stringify({ error: "Start your free 15-day trial before subscribing." }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 403,
      });
    }
    const { data: trialLicense } = await supabase
      .schema("groundtruth")
      .from("licenses")
      .select("id")
      .eq("payment_id", trial.id)
      .maybeSingle();
    if (!trialLicense) {
      return new Response(JSON.stringify({ error: "Activate the free trial in GroundTruth Explorer before subscribing." }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 403,
      });
    }

    const { data: trialPc } = await supabase
      .schema("groundtruth")
      .from("license_activations")
      .select("device_fingerprint")
      .eq("license_id", trialLicense.id)
      .eq("device_type", "pc")
      .eq("device_fingerprint", device_id)
      .maybeSingle();
    if (!trialPc) {
      return new Response(JSON.stringify({ error: "The Device ID must match the Windows PC that used your free trial." }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 403,
      });
    }

    const trialEndsAt = getExpiryDate("TRIAL", new Date(trial.created_at)).getTime();
    if (Date.now() < trialEndsAt) {
      return new Response(JSON.stringify({
        error: "Your 15-day free trial is still active.",
        eligible_at: new Date(trialEndsAt).toISOString(),
      }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 403,
      });
    }

    const { data: price, error: priceError } = await supabase
      .schema("groundtruth")
      .from("prices")
      .select("amount_cents, currency")
      .eq("plan", plan)
      .single();
    if (priceError || !price) throw new Error("Selected plan is unavailable");

    const callbackUrl = Deno.env.get("GROUNDTRUTH_SUCCESS_URL") || "https://website-six-pi-71.vercel.app/success.html";
    const ref = `GT-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
    const paystackResponse = await fetch("https://api.paystack.co/transaction/initialize", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${secret}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email: emailNorm,
        amount: price.amount_cents,
        currency: price.currency,
        reference: ref,
        callback_url: callbackUrl,
        metadata: { product: "groundtruth", country, purpose, plan, device_id },
      }),
    });
    const result = await paystackResponse.json();
    if (!paystackResponse.ok || !result.status || !result.data?.authorization_url) {
      throw new Error(result.message || "Could not start Paystack checkout");
    }

    return new Response(JSON.stringify({ authorization_url: result.data.authorization_url }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 200,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("initialize-payment error:", message);
    return new Response(JSON.stringify({ error: message }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 500,
    });
  }
});
