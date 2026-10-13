# CloudGuard

A responsive digital-legacy vault prototype built with React + Vite, FastAPI, and SQLite. This first version focuses on the two agreed objectives: private encrypted document storage and a separate live check-in experience.

## Included

- Owner registration and email/password sign-in; bcrypt password hashes and 12-hour JWT sessions.
- Five camera-captured face enrollment samples, stored encrypted at rest and used for local FaceNet comparisons during check-in.
- User-owned files encrypted at rest using AES-256-GCM, with authenticated encrypted-export and delete routes. Supports PDF, TXT, common Office documents, images, audio, video, and archives (25 MB maximum).
- Owner-managed trusted-contact records with add, edit, and remove.
- A separate nominee account and portal for submitting an encrypted death certificate and tracking an independently reviewed case.
- Role-specific, email-verified invitations for nominees and recipients. Invitation claims create separate accounts and role-scoped sessions; nominees and recipients never receive owner routes.
- A separately authenticated reviewer portal for inspecting submitted evidence and recording case decisions.
- Owner-controlled Medical Hold and explicit release gates: an approved reviewer case, owner approval, and no active hold are required before recipient access; ending a hold requires a fresh successful owner check-in.
- Face match, anti-spoof liveness, and spoken-phrase checks using DeepFace and faster-whisper. The server deletes each uploaded recording after analysis.
- Private check-in history and an append-only, SHA-256 hash-chained activity table.
- Responsive dashboard, profile settings, password visibility controls, and a profile-menu sign-out action.

## Start locally (Windows PowerShell)

From the project folder, install the frontend dependencies once, then start both the website and API together:

```powershell
cd C:\Major_Project
npm install
npm run dev
```

The first start creates a Python virtual environment, installs the API and local AI requirements, and generates private local development secrets in `backend\.env`. Keep that file private; the generated encryption key must be backed up if you want to preserve access to stored documents. The API watches `backend` for changes and reloads automatically; the frontend proxies API requests to it. Open `http://localhost:5173` and press Ctrl+C to stop both.

If you prefer running the API separately:

```powershell
cd C:\Major_Project\backend
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set private secrets in `backend\.env`, then run `$env:PYTHONUTF8 = "1"` followed by `.\.venv\Scripts\python.exe -m uvicorn main:app --reload`. The API is available at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`.

For API-only development, run `npm run dev:api` from the project folder. To run the backend tests, install the development requirements and run `pytest` from `C:\Major_Project\backend`:

```powershell
pip install -r requirements-dev.txt
pytest
```

Open the Vite URL printed in the terminal (normally `http://localhost:5173`). Camera access is available on `localhost` or HTTPS.

## Live check-in verification

The phrase stays visible throughout the 10-second camera and microphone recording. On submission, the recording is sent to the configured CloudGuard API server, where DeepFace compares several frames with the five encrypted enrollment samples, its anti-spoofing check evaluates liveness, and faster-whisper checks the spoken phrase. The server processes the clip in a temporary directory and removes it after analysis; it stores only the phrase, timestamp, and individual check results. A check-in passes only if all three checks pass. Missing models and processing errors fail closed and do not create a check-in result.

The development launcher installs the AI packages along with the API dependencies, using DeepFace's TensorFlow CPU engine on Windows. The first check-in downloads model weights (including the `small.en` speech model) and may take several minutes and hundreds of megabytes; later checks reuse the cached models. CPU inference can take time. Model weights are downloaded by their respective packages, but check-in recordings are processed only by the configured API server. Set `CLOUDGUARD_WHISPER_MODEL` in `backend\.env` to choose another faster-whisper model. Anti-spoofing and face matching are probabilistic checks, not a guarantee of identity or proof against every attack.

## Invitation and recipient access boundary

Trusted recipients and the single nominee have separate invitation roles. Configure `RESEND_API_KEY`, `RESEND_FROM_EMAIL` (an address on a verified Resend domain), and `CLOUDGUARD_APP_URL` in `backend\.env`; never commit that file. Invitation links expire after seven days and can only be claimed once. Recipients and nominees each create a password from their invitation and sign in through their own portal. A person invited in both roles gets separate role-scoped sessions and permissions.

Recipients see only documents assigned to them. An invitation, nominee submission, reviewer decision, or missed owner check-in alone never releases a file. A reviewer must approve a submitted case, then the owner must explicitly approve each assigned document's release. The API blocks downloads if the case is not approved, the release is revoked, or a Medical Hold is active or awaiting a fresh check-in.

Nominees can submit a death certificate as a PDF, JPG, or PNG (10 MB maximum) with an optional note. Evidence is encrypted at rest. A reviewer can approve, reject, or request more information; a nominee cannot review their own case. Case approval is not document access and does not release any file.

The reviewer portal is disabled until a reviewer account is configured in `backend\.env` with `CLOUDGUARD_REVIEWER_EMAIL` and a bcrypt `CLOUDGUARD_REVIEWER_PASSWORD_HASH`. Generate a hash in PowerShell from the backend folder:

```powershell
.\.venv\Scripts\python.exe -c "import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass('Reviewer password: ').encode(), bcrypt.gensalt()).decode())"
```

Keep the hash private. The current prototype supports one separately configured reviewer account; it does not include reviewer enrollment or multi-reviewer administration.

Owners can start and end a Medical Hold from the dashboard. While active or awaiting a fresh check-in, the API pauses new releases and blocks recipient downloads. Ending a hold moves it to `awaiting_checkin`; only a successful live check-in started after the hold ended resumes release eligibility. No escalation scheduler is implemented, and missed check-ins never release files.

Resend must accept the message before CloudGuard reports an invitation as sent. Resend account and domain verification limits (including any trial-account recipient restrictions, sending quota, and rate limits) still apply; provider rejection leaves the invitation inactive and the owner must resolve the Resend configuration or quota issue before retrying.

## Encrypted document exports

Downloads are the original AES-256-GCM ciphertext, saved with a `.cgenc` extension; they are not plaintext documents and cannot be opened directly by normal PDF or office apps. The file is encrypted using the server's private `CLOUDGUARD_FILE_KEY`. Keep a secure backup of that key: without it, the exported ciphertext cannot be recovered. The key is never included in a download.

## Security notes

- The development launcher generates strong local secrets automatically. For any non-development deployment, configure independent secrets and never publish `.env`, reviewer credentials, or the encryption key.
- Configure a verified Resend sender and a publicly reachable frontend URL before inviting guests. Keep the Resend API key only in `backend\.env`; the guest email link is single-use and expires after seven days.
- AES-GCM encrypts each file with a unique random nonce. SQLite stores ciphertext and metadata; downloads return that ciphertext only after checking the authenticated owner.
- This is a student prototype, not a production security or legal-legacy service. Use HTTPS, rate limiting, key management/rotation, backups, account recovery, and a reviewed release workflow before real-world deployment.
- Missed-check-in scheduling, warning escalation, reviewer enrollment, multi-reviewer administration, nominee identity validation, death-certificate OCR, and automatic release are intentionally out of scope. The app does not make a legal determination of death; reviewers must follow an appropriate external process.
