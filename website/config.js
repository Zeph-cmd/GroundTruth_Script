// Website configuration
// Replace these placeholders with your real keys before deploying.

const SITE_CONFIG = {
  // Paystack public key (live)
  PAYSTACK_PUBLIC_KEY: "pk_live_f746fe0e2e9cc44867cae0dc59bd2ef6ee3bdb25",

  // Supabase project URL and anon key
  SUPABASE_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co",
  SUPABASE_ANON_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRtemltZHliZndiZHdramx2ZWt4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUwNjIwNDIsImV4cCI6MjA5MDYzODA0Mn0.PIDqi0nfXwZcSXp166hg3IO1Zle1JV__nAsbta_CTvU",

  // Supabase Edge Function URLs
  ACTIVATE_LICENSE_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/activate-license",
  SUBMIT_FEEDBACK_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/submit-feedback",
  START_TRIAL_URL: "https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/start-trial",

  // Free trial
  TRIAL_DAYS: 15,

  // GitHub Release download URLs (update when you publish a new release)
  DESKTOP_DOWNLOAD_URL: "https://github.com/Zeph-cmd/GroundTruth_Script/releases/latest/download/GroundTruth.exe",
  ANDROID_DOWNLOAD_URL: "https://github.com/Zeph-cmd/GroundTruth_Script/releases/latest/download/app-debug.apk",

  // Prices in cents (USD)
  PRICES: {
    "30D": 3000,
    "1Y": 35600,
  },
};
