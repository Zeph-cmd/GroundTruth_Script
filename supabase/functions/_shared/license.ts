// Shared signing utilities for GroundTruth license codes.
// Format matches the desktop/Android payload:
// serial(2) | plan(1) | expiry_days(2 LE) | pc_seats(1) | android_seats(1)

import {
  encodeBase64,
  decodeBase64,
} from "https://deno.land/std@0.207.0/encoding/base64.ts";

function base32Encode(bytes: Uint8Array): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const b of bytes) {
    bits += b.toString(2).padStart(8, "0");
  }
  let out = "";
  for (let i = 0; i + 5 <= bits.length; i += 5) {
    out += alphabet[parseInt(bits.substring(i, i + 5), 2)];
  }
  const rem = bits.length % 5;
  if (rem > 0) {
    const last = bits.substring(bits.length - rem).padEnd(5, "0");
    out += alphabet[parseInt(last, 2)];
  }
  return out;
}

export const PLAN_BYTES: Record<string, number> = {
  "30D": 0x01,
  "1Y": 0x02,
  "TRIAL": 0x03,
};

export const PLAN_DAYS: Record<string, number> = {
  "30D": 30,
  "1Y": 365,
  "TRIAL": 15,
};

export function codePayloadToBytes(
  serialHex: string,
  plan: string,
  expiryDays: number,
  pcSeats = 1,
  androidSeats = -1,
): Uint8Array {
  const serial = parseInt(serialHex, 16);
  if (isNaN(serial) || serial < 0 || serial > 0xffff) {
    throw new Error("Invalid serial: " + serialHex);
  }
  const planByte = PLAN_BYTES[plan];
  if (planByte === undefined) throw new Error("Invalid plan: " + plan);
  if (expiryDays < 0 || expiryDays > 65535) throw new Error("Invalid expiry days");

  const buf = new Uint8Array(7);
  const view = new DataView(buf.buffer);
  view.setUint16(0, serial, false); // big-endian to match Python struct.pack(">H")
  buf[2] = planByte;
  view.setUint16(3, expiryDays, true); // little-endian to match Python int.to_bytes(2,'little')
  buf[5] = pcSeats & 0xff;
  buf[6] = androidSeats & 0xff; // -1 -> 255 means unlimited
  return buf;
}

export async function signBytes(privateKeyPem: string, payload: Uint8Array): Promise<string> {
  const base64 = privateKeyPem
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s/g, "");
  const keyBytes = decodeBase64(base64);
  const keyData = keyBytes.buffer.slice(
    keyBytes.byteOffset,
    keyBytes.byteOffset + keyBytes.byteLength,
  ) as ArrayBuffer;

  const cryptoKey = await crypto.subtle.importKey(
    "pkcs8",
    keyData,
    { name: "Ed25519" },
    false,
    ["sign"],
  );

  const signature = await crypto.subtle.sign(
    "Ed25519",
    cryptoKey,
    payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength) as ArrayBuffer,
  );
  return encodeBase64(new Uint8Array(signature));
}

export async function verifySignature(publicKeyPem: string, payload: Uint8Array, signatureB64: string): Promise<boolean> {
  try {
    const base64 = publicKeyPem
      .replace(/-----BEGIN PUBLIC KEY-----/g, "")
      .replace(/-----END PUBLIC KEY-----/g, "")
      .replace(/\s/g, "");
    const keyBytes = decodeBase64(base64);
    const keyData = keyBytes.buffer.slice(
      keyBytes.byteOffset,
      keyBytes.byteOffset + keyBytes.byteLength,
    ) as ArrayBuffer;
    const signature = decodeBase64(signatureB64);
    const signatureData = signature.buffer.slice(
      signature.byteOffset,
      signature.byteOffset + signature.byteLength,
    ) as ArrayBuffer;
    const payloadData = payload.buffer.slice(
      payload.byteOffset,
      payload.byteOffset + payload.byteLength,
    ) as ArrayBuffer;

    const cryptoKey = await crypto.subtle.importKey(
      "spki",
      keyData,
      { name: "Ed25519" },
      false,
      ["verify"],
    );

    return await crypto.subtle.verify("Ed25519", cryptoKey, signatureData, payloadData);
  } catch {
    return false;
  }
}

export function generateSerial(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(2));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

export function formatCode(serialHex: string, plan: string, signatureB64: string, payload: Uint8Array): string {
  const sigBytes = decodeBase64(signatureB64);
  const combined = new Uint8Array(payload.length + sigBytes.length);
  combined.set(payload, 0);
  combined.set(sigBytes, payload.length);
  const b32 = base32Encode(combined);
  // Human-friendly chunks: GT-30D-XXXX-...
  const chunks = b32.match(/.{1,4}/g) || [];
  return `GT-${plan}-${chunks.join("-")}`;
}

export function getExpiryDate(plan: string, startDate = new Date()): Date {
  const days = PLAN_DAYS[plan];
  if (!days) throw new Error("Invalid plan");
  const d = new Date(startDate);
  // Payload stores a UTC-day boundary. Add an extra calendar day so a trial
  // started mid-day receives at least 15 complete days before that boundary.
  d.setDate(d.getDate() + (plan === "TRIAL" ? days + 1 : days));
  d.setHours(0, 0, 0, 0);
  return d;
}

// Days are packed in the payload as days since this epoch (matches licensing.py _DATE_EPOCH).
export const DATE_EPOCH_MS = Date.UTC(2026, 0, 1);

export function daysSinceEpoch(d: Date): number {
  return Math.round((d.getTime() - DATE_EPOCH_MS) / (1000 * 60 * 60 * 24));
}
