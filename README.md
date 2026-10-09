# CloudGuard

A responsive digital-legacy vault prototype built with React + Vite, FastAPI, and SQLite. This first version focuses on the two agreed objectives: private encrypted document storage and a separate live check-in experience.

## Included

- Owner registration and email/password sign-in; bcrypt password hashes and 12-hour JWT sessions.
- Five camera-captured face enrollment samples, stored encrypted at rest and used for local FaceNet comparisons during check-in.
- User-owned files encrypted at rest using AES-256-GCM, with authenticated encrypted-export and delete routes. Supports PDF, TXT, common Office documents, images, audio, video, and archives (25 MB maximum).
- Owner-managed trusted-contact records with add, edit, and remove.
- A separate, single nominee contact record; nominee records do not create logins or grant document access.
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

## Recipient access boundary

Trusted recipients and the single nominee are separate owner-side contact records. There is no recipient/nominee account, invitation, portal, release review, or document-sharing path in this version. A later portal should use a separate role and sign-in tied to the invited email, with no access by default. Document access should only be granted after the future owner-defined release and verification workflow is implemented and approved. This prototype never releases files automatically.

## Encrypted document exports

Downloads are the original AES-256-GCM ciphertext, saved with a `.cgenc` extension; they are not plaintext documents and cannot be opened directly by normal PDF or office apps. The file is encrypted using the server's private `CLOUDGUARD_FILE_KEY`. Keep a secure backup of that key: without it, the exported ciphertext cannot be recovered. The key is never included in a download.

## Security notes

- The development launcher generates strong local secrets automatically. For any non-development deployment, configure independent secrets and never publish `.env` or the encryption key.
- AES-GCM encrypts each file with a unique random nonce. SQLite stores ciphertext and metadata; downloads return that ciphertext only after checking the authenticated owner.
- This is a student prototype, not a production security or legal-legacy service. Use HTTPS, rate limiting, key management/rotation, backups, account recovery, and a reviewed release workflow before real-world deployment.
- Missed-check-in scheduling, warning escalation, email notifications, nominee validation, death-certificate OCR, and automatic release are intentionally out of scope.
