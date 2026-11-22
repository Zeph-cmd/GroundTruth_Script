# GroundTruth Vision: Android Studio Handoff

## Prompt 1: Optional photo optimization for existing sharing

Inspect the existing trip export, `.rvtrip` packaging, integrity verification, photo and metadata code before making changes. Implement the following in GroundTruth Vision without changing the existing collection behavior:

- Add an explicit transfer-quality choice before exporting: **Original** (default), **Mild** (longest dimension no more than 2400 pixels), and **Strong** (longest dimension no more than 1600 pixels). Do not enlarge photos that are already smaller.
- Both paid users and 15-day trial users can choose any of these options when sharing through the existing Android share sheet or other existing export methods. Do not require Supabase, an internet connection, or a paid plan for photo optimization.
- Leave the source trip, original photos, and existing original-quality export untouched. Create optimized photos only in a temporary derivative package. Clean up temporary files safely when they are no longer needed; never remove originals.
- Preserve aspect ratio, correct orientation, photo ordering, filename references, GPS coordinates, timestamps, and other fields Explorer depends on. Inspect the existing EXIF and manifest handling rather than assuming a particular metadata format. Do not change video files, GPS tracks, or other trip data.
- Make it clear that Mild and Strong reduce detail and may affect zoom or detection accuracy, and that compressed size and quality vary. Do not promise that detection is unaffected or that a package will fit within any size limit.
- Generate package checksums from the actual derivative contents. Mark the package as an optimized derivative in a compatible, non-disruptive way. Do not claim the optimized photos are byte-identical to the originals, and do not regenerate or forge any original signature or other authenticity evidence. Explain in the UI and export metadata what was modified. If the existing cryptographic design cannot truthfully represent a derivative, report that blocker rather than bypassing verification.
- Keep existing `.rvtrip` sharing, local transfers, and Original quality working as before. Show users the estimated or final package size when practical.
- Test original vs. optimized exports using real trips, including GPS, timestamps, EXIF orientation, empty trips, small images, and large photos. Verify that all three packages open in the existing GroundTruth Explorer and that their manifest results and authenticity labels are truthful. Compare image quality and detection on representative samples; report measurements rather than assuming results.

## Prompt 2: Cloud relay upload (backend contract now defined; still disabled server-side)

The Explorer/backend relay is now implemented but the server-side flag stays off until the owner enables it after live testing. Implement the upload side in Vision against this exact contract, and keep every cloud element hidden until the status probe reports enabled.

### API contract (Supabase Edge Function `trip-relay`)

Base URL: `https://tmzimdybfwbdwkjlvekx.supabase.co/functions/v1/trip-relay`. All calls send `Content-Type: application/json` and, except status, the `x-relay-credential` header with the device's 64-hex-char secret credential. All errors return `{"error": "<code>"}` with HTTP 403 (or 400/429/503 as noted); map every code to a friendly message, never show raw codes.

- `GET` — no credential. Returns `{"enabled": false}` while the feature is off. Hide all cloud UI unless `enabled` is true; the server rejects everything anyway.
- `POST {"action":"enroll","license_code":"...","device_type":"android","device_fingerprint":"..."}` — first connection only. The app generates a 32-byte secret (`crypto` SecureRandom), sends its 64-char lowercase hex in the header, and persists the secret in Android Keystore-backed storage (never plain SharedPreferences, never logs). Success returns `{"device_id":"<uuid>"}`. Error `unauthorized` means the license is not a paid, activated, non-revoked license on this device.
- `POST {"action":"reserve","size_bytes":<file size>,"sha256_hex":"<64-char sha-256 of the .rvtrip file>"}` — before each upload. Success returns `{"trip_id":"<uuid>","upload_url":"<signed upload URL>","upload_token":"...","object_key":"<trip_id>.rvtrip"}`. Errors: `trip_limit_reached` (five active/reserved trips per customer), `storage_full`, `unauthorized`, `invalid_request`.
- Upload: `PUT` the exact `.rvtrip` bytes to `upload_url` (no auth header on that request). Retry the PUT on network failure; the reservation stays valid until the sweep cleans abandoned ones.
- `POST {"action":"complete","trip_id":"...","sha256_hex":"..."}` — after a successful PUT. The sha must match the reservation; success starts the 6-hour window and returns `{"expires_at":"..."}`. Error `not_reservable` means the upload cannot be confirmed (e.g. hash mismatch); the trip stays reserved until cleanup, so re-reserve and re-upload.
- If the user cancels after reserving but before `complete`, simply stop; nothing else to call.

### Rules

- Cloud sharing is paid-subscriber-only and hidden until the server confirms the feature is enabled. Both the server and the app must enforce the disabled state; hiding a button alone is insufficient.
- Use a simple upload button: no per-trip code or share link. Once securely connected to the same paying customer, authorized Vision devices upload to that customer's inbox and the authorized Explorer shows available trips when online.
- A paying customer may have up to five active or reserved trips across all their devices, including across subscription renewals (enforced atomically by the server; the app should surface the limit, not re-implement it).
- A trip expires six hours after the server confirms its upload has completed. It is also consumed after the first confirmed successful download (the tied Explorer acknowledges; the server then deletes the object). A failed transfer must not silently destroy the only copy. An expired trip must be uploaded again.
- Obtain the file-size and capacity limits from the server (the `reserve` response rejects oversize). Show progress and meaningful offline, quota, full-storage, failed-upload and expired-trip messages. Keep originals and all existing sharing methods available.
- Protect trip data and credentials. Never embed a Supabase service-role key or a signing private key in the app, URL, or logs. Do not infer paid status from a local UI flag. Do not log credentials, license codes or trip contents.
- Support Original, Mild, and Strong transfer copies. The optimization choices themselves must remain available without the cloud service and to trial users.
- Include tests for authorization, upload interruption, concurrent uploads, expiration, retry, device ownership, subscription renewal, and Explorer import of a cloud-downloaded package.

## Prompt 3: Full 30-second recordings in packages + human-readable trip names

The Explorer now plays videos: frame clips from `videos/` and full recordings from `videos_full/`. It reads `manifest.json` and `metadata.json` as before — send this prompt to Android Studio.

> Extend the GroundTruth Vision trip package so the full 30-second recordings are preserved and trips are easy to identify, without breaking the existing import contract:
> 1. When building any `.rvtrip` package (Export & Share AND cloud upload — same packaging code path), include the full 30-second recording for each captured frame in a separate `videos_full/` folder inside the archive. Keep the existing short frame clips in `videos/` unchanged. If a recording is missing for a frame (low storage, interrupted capture), simply omit it — do not fail the export.
> 2. Add a `video_full_hashes` array to `manifest.json` with the same `{"filename": ..., "hash": ...}` SHA-256 entries format used by `video_hashes`. The Explorer already verifies this key when present and ignores its absence.
> 3. In `metadata.json`, add an optional `video_full` field to each photos entry mapping that frame to its full recording file name (e.g. `"video_full": "video0001_full.mp4"`), alongside the existing `video` field. Also add a top-level `tripName` string (e.g. the trip's date and start location, or a user-entered name) so the Explorer can display it.
> 4. Do not rename or restructure existing folders (`frames/`, `videos/`, `metadata.json`, `manifest.json`), change hash format, or remove any existing fields. Older Explorer builds and existing sharing must keep working.
> 5. Verify by exporting a trip and checking the archive contains `videos_full/` with the recordings, `manifest.json` includes `video_full_hashes`, and `metadata.json` includes `tripName`.

## Prompt 4: Manual photo mode GPS and far-apart locations

The Explorer's map renders every photo's own GPS point, so manual photos taken far apart each appear at their true location. This prompt keeps the data correct.

> Update GroundTruth Vision's manual photo mode (single photos, not trips):
> 1. Capture GPS at the exact moment of each shutter. Every photo entry must carry its own latitude, longitude, timestamp and accuracy in `metadata.json` — never reuse the previous photo's coordinates, because manual photos may be taken in far-apart places.
> 2. If GPS is unavailable at shutter time, record the entry without coordinates rather than guessing, and surface a short "No GPS fix yet" notice in the UI. The Explorer handles missing coordinates gracefully.
> 3. Manual batch exports must include EVERY photo in the batch with its individual GPS data — verify the count of photos entries in metadata.json equals the number of photos in the batch before upload/export.
> 4. Optionally, if storage allows, record a short 3-5 second clip alongside each manual photo (same `video`/`video_full` fields as trips). If not feasible, photo-only entries remain fully supported.
> 5. Verify by taking two photos far apart, exporting the batch, and confirming `metadata.json` lists both with distinct, correct coordinates.
