/* global SITE_CONFIG, PaystackPop */

const isTestMode = () => SITE_CONFIG.PAYSTACK_PUBLIC_KEY.startsWith("pk_test_");

function showError(elementId, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message;
  el.classList.remove("hidden");
}

function hideError(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.classList.add("hidden");
}

function getPlanAmount(plan) {
  return SITE_CONFIG.PRICES[plan];
}

async function startCheckout(formData) {
  const btn = document.getElementById("pay-btn");
  btn.disabled = true;
  btn.textContent = "Opening secure checkout...";

  try {
    const res = await fetch(SITE_CONFIG.INITIALIZE_PAYMENT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SITE_CONFIG.SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({
        email: formData.email.trim(),
        plan: formData.plan,
        country: formData.country,
        purpose: formData.purpose,
        device_id: formData.deviceId,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.authorization_url) {
      throw new Error(data.error || "Could not start payment. Please try again.");
    }
    window.location.href = data.authorization_url;
  } catch (err) {
    showError("checkout-error", err.message || "Could not start payment. Please try again.");
    btn.disabled = false;
    btn.textContent = "Pay & get activation code";
  }
}

async function checkTrialUsed(email) {
  const res = await fetch(SITE_CONFIG.START_TRIAL_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: SITE_CONFIG.SUPABASE_ANON_KEY,
    },
    body: JSON.stringify({ email, check_only: true }),
  });
  if (!res.ok) throw new Error("Server error " + res.status);
  return await res.json();
}

async function startFreeTrial(formData) {
  const btn = document.getElementById("pay-btn");
  btn.disabled = true;
  btn.textContent = "Creating your trial...";

  try {
    const res = await fetch(SITE_CONFIG.START_TRIAL_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SITE_CONFIG.SUPABASE_ANON_KEY,
      },
      body: JSON.stringify({
        email: formData.email.trim(),
        country: formData.country,
        purpose: formData.purpose,
      }),
    });

    const data = await res.json();

    if (!res.ok || !data.ref) {
      throw new Error(data.error || "Could not start your trial. Please try again.");
    }

    // Trial ref works like a payment ref: success page polls for the license.
    window.location.href = `success.html?ref=${encodeURIComponent(data.ref)}&trial=1`;
  } catch (err) {
    showError("checkout-error", err.message || "Could not start your trial. Please try again.");
    btn.disabled = false;
    btn.textContent = "Start free trial";
  }
}

function initCheckoutForm() {
  const form = document.getElementById("checkout-form");
  if (!form) return;

  const payBtn = document.getElementById("pay-btn");

  const updatePayButton = () => {
    const plan = form.querySelector('input[name="plan"]:checked')?.value;
    if (plan === "TRIAL") {
      payBtn.textContent = `Start ${SITE_CONFIG.TRIAL_DAYS}-day free trial`;
    } else {
      payBtn.textContent = "Pay & get activation code";
    }
  };

  form.querySelectorAll('input[name="plan"]').forEach((input) => {
    input.addEventListener("change", updatePayButton);
  });
  updatePayButton();

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    hideError("checkout-error");

    const formData = {
      country: form.country.value.trim(),
      purpose: form.purpose.value.trim(),
      email: form.email.value.trim(),
      deviceId: form.device_id.value.trim().toUpperCase(),
      plan: form.querySelector('input[name="plan"]:checked')?.value,
    };

    if (!formData.country || !formData.purpose || !formData.email || !formData.plan) {
      showError("checkout-error", "Please fill in all required fields and choose a plan.");
      return;
    }

    if (formData.plan === "TRIAL") {
      startFreeTrial(formData);
      return;
    }

    if (!/^GT-[A-Z2-7]{4}(?:-[A-Z2-7]{4}){2}$/.test(formData.deviceId)) {
      showError("checkout-error", "Enter the Windows Device ID shown in GroundTruth Explorer to subscribe.");
      return;
    }

    if (SITE_CONFIG.PAYSTACK_PUBLIC_KEY.includes("REPLACE_ME")) {
      showError(
        "checkout-error",
        "Payment is not configured yet. Please contact the developer to set up Paystack."
      );
      return;
    }

    // Only the server can authorize paid checkout after the full trial.
    payBtn.disabled = true;
    payBtn.textContent = "Checking trial status...";
    checkTrialUsed(formData.email)
      .then((trialStatus) => {
        if (!trialStatus.can_subscribe) {
          const message = trialStatus.has_trial
            ? `Your 15-day free trial is still active. You can subscribe after ${new Date(trialStatus.trial_ends_at).toLocaleString()}.`
            : `Start your ${SITE_CONFIG.TRIAL_DAYS}-day free trial first. You can subscribe after you have completed it.`;
          showError("checkout-error", message);
          payBtn.disabled = false;
          payBtn.textContent = "Pay & get activation code";
          return;
        }
        startCheckout(formData);
      })
      .catch(() => {
        showError(
          "checkout-error",
          "Could not verify your trial status. Check your connection and try again."
        );
        payBtn.disabled = false;
        payBtn.textContent = "Pay & get activation code";
      });
  });
}

function initFeedbackForm() {
  const form = document.getElementById("feedback-form");
  if (!form) return;

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    hideError("feedback-error");

    const btn = document.getElementById("feedback-btn");
    btn.disabled = true;
    btn.textContent = "Sending...";

    const payload = {
      type: form.type.value,
      country: form.country.value.trim(),
      email: form.email.value.trim(),
      message: form.message.value.trim(),
    };

    if (!payload.type || !payload.message) {
      showError("feedback-error", "Please select a type and write a message.");
      btn.disabled = false;
      btn.textContent = "Send feedback";
      return;
    }

    try {
      const res = await fetch(SITE_CONFIG.SUBMIT_FEEDBACK_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: SITE_CONFIG.SUPABASE_ANON_KEY,
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error("Server error " + res.status);

      form.reset();
      document.getElementById("feedback-success").classList.remove("hidden");
    } catch (err) {
      showError("feedback-error", "Could not send feedback. Please try again or contact support.");
    } finally {
      btn.disabled = false;
      btn.textContent = "Send feedback";
    }
  });
}

function initSuccessPage() {
  const licenseCodeEl = document.getElementById("license-code");
  if (!licenseCodeEl) return;

  const params = new URLSearchParams(window.location.search);
  const ref = params.get("ref") || params.get("reference");

  if (!ref) {
    document.getElementById("loading-state").classList.add("hidden");
    document.getElementById("error-state").classList.remove("hidden");
    document.getElementById("error-message").textContent = "No payment reference found.";
    return;
  }

  // Poll the success endpoint for license code.
  // In production this is generated by the paystack-webhook Edge Function.
  pollForLicense(ref);
}

async function pollForLicense(ref) {
  const maxAttempts = 20;
  let attempts = 0;

  const check = async () => {
    attempts++;
    try {
      const res = await fetch(
        `${SITE_CONFIG.ACTIVATE_LICENSE_URL}?ref=${encodeURIComponent(ref)}`,
        {
          headers: {
            apikey: SITE_CONFIG.SUPABASE_ANON_KEY,
          },
        }
      );

      if (res.ok) {
        const data = await res.json();
        if (data.code) {
          showLicense(data);
          return;
        }
      }

      if (attempts < maxAttempts) {
        setTimeout(check, 3000);
      } else {
        showLicenseError("We could not retrieve your license automatically. Please contact support with reference: " + ref);
      }
    } catch (err) {
      if (attempts < maxAttempts) {
        setTimeout(check, 3000);
      } else {
        showLicenseError("Network error while retrieving your license. Reference: " + ref);
      }
    }
  };

  check();
}

function showLicense(data) {
  document.getElementById("loading-state").classList.add("hidden");
  document.getElementById("success-state").classList.remove("hidden");

  if (data.plan === "TRIAL") {
    document.querySelector("#success-state h1").textContent = "Your free trial is ready!";
    document.querySelector("#success-state .checkout-sub").innerHTML =
      `Your ${SITE_CONFIG.TRIAL_DAYS}-day trial code works on <strong>1 Windows PC + unlimited Android devices</strong>. When it ends, come back and subscribe to keep using GroundTruth.`;
  }

  const codeEl = document.getElementById("license-code");
  codeEl.textContent = data.code;

  document.getElementById("download-exe").href = SITE_CONFIG.DESKTOP_DOWNLOAD_URL;
  document.getElementById("download-apk").href = SITE_CONFIG.ANDROID_DOWNLOAD_URL;

  document.getElementById("copy-code-btn").addEventListener("click", () => {
    navigator.clipboard.writeText(data.code).then(() => {
      const btn = document.getElementById("copy-code-btn");
      btn.textContent = "Copied!";
      setTimeout(() => (btn.textContent = "Copy code"), 2000);
    });
  });
}

function showLicenseError(message) {
  document.getElementById("loading-state").classList.add("hidden");
  document.getElementById("error-state").classList.remove("hidden");
  document.getElementById("error-message").textContent = message;
}

document.addEventListener("DOMContentLoaded", function () {
  initCheckoutForm();
  initFeedbackForm();
  initSuccessPage();
});
