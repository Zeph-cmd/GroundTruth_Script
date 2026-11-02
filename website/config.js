// Website configuration
// Replace these placeholders with your real keys before deploying.

const SITE_CONFIG = {
  // Paystack public key (test or live)
  PAYSTACK_PUBLIC_KEY: "pk_test_REPLACE_ME",

  // Supabase project URL and anon key
  SUPABASE_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co",
  SUPABASE_ANON_KEY: "REPLACE_ME",

  // Supabase Edge Function URLs
  ACTIVATE_LICENSE_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/activate-license",
  SUBMIT_FEEDBACK_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/submit-feedback",

  // GitHub Release download URLs (update when you publish a new release)
  DESKTOP_DOWNLOAD_URL: "https://github.com/Zeph-cmd/GroundTruth_Script/releases/latest/download/GroundTruth.exe",
  ANDROID_DOWNLOAD_URL: "https://github.com/Zeph-cmd/GroundTruth_Script/releases/latest/download/app-debug.apk",

  // Prices in cents (USD)
  PRICES: {
    "30D": 3000,
    "1Y": 35600,
  },
};
