import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from verification import VerificationUnavailable, providers_available, verify_recording

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
os.environ.setdefault("DEEPFACE_BACKEND_ENGINE", os.getenv("CLOUDGUARD_DEEPFACE_ENGINE", "tensorflow"))
DATABASE = Path(os.getenv("CLOUDGUARD_DATABASE", str(BASE_DIR / "cloudguard.db")))
JWT_SECRET = os.getenv("CLOUDGUARD_JWT_SECRET")
FILE_KEY_B64 = os.getenv("CLOUDGUARD_FILE_KEY")
ENVIRONMENT = os.getenv("CLOUDGUARD_ENV", "development")
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_CHECKIN_BYTES = 20 * 1024 * 1024
CHECKIN_SENTENCES = {
    "Today I am confirming my CloudGuard check-in.",
    "I am completing my secure digital check-in today.",
    "My important documents are safely stored with CloudGuard.",
    "I am here and completing my personal check-in.",
    "CloudGuard helps me keep the things that matter secure.",
}
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".rtf", ".doc", ".docx", ".odt", ".xls", ".xlsx",
    ".ods", ".csv", ".ppt", ".pptx", ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".heic", ".tif", ".tiff", ".bmp", ".mp3", ".wav", ".m4a",
    ".mp4", ".mov", ".avi", ".zip", ".7z", ".rar", ".json", ".xml",
}
security = HTTPBearer(auto_error=False)
@asynccontextmanager
async def lifespan(_: FastAPI):
    if not JWT_SECRET and ENVIRONMENT.lower() != "development":
        raise RuntimeError("CLOUDGUARD_JWT_SECRET must be set outside development.")
    if not JWT_SECRET:
        raise RuntimeError("Set CLOUDGUARD_JWT_SECRET using backend/.env.example before starting.")
    if len(JWT_SECRET.encode("utf-8")) < 32:
        raise RuntimeError("CLOUDGUARD_JWT_SECRET must contain at least 32 bytes.")
    encryption_key()
    init_database()
    yield


app = FastAPI(title="CloudGuard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CLOUDGUARD_FRONTEND_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def encryption_key() -> bytes:
    if not FILE_KEY_B64:
        raise RuntimeError("CLOUDGUARD_FILE_KEY must be configured before starting the API.")
    try:
        key = base64.b64decode(FILE_KEY_B64, validate=True)
    except (ValueError, TypeError) as error:
        raise RuntimeError("CLOUDGUARD_FILE_KEY must be valid base64.") from error
    if len(key) != 32:
        raise RuntimeError("CLOUDGUARD_FILE_KEY must decode to exactly 32 bytes.")
    return key


def encrypt_bytes(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(encryption_key()).encrypt(nonce, data, None)


def decrypt_bytes(data: bytes) -> bytes:
    return AESGCM(encryption_key()).decrypt(data[:12], data[12:], None)


def init_database() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                phone TEXT NOT NULL DEFAULT '',
                password_hash BLOB NOT NULL,
                face_enrollment BLOB,
                checkin_interval INTEGER NOT NULL DEFAULT 30,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                file_name TEXT NOT NULL,
                content_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                encrypted_data BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                relationship TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nominees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                relationship TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                face_status TEXT NOT NULL,
                liveness_status TEXT NOT NULL,
                speech_status TEXT NOT NULL,
                sentence TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event TEXT NOT NULL,
                created_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            );
            """
        )


def audit(connection: sqlite3.Connection, owner_id: Optional[int], event: str) -> None:
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    previous = connection.execute(
        "SELECT event_hash FROM audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous["event_hash"] if previous else "0" * 64
    created_at = utc_now().isoformat()
    content = json.dumps(
        {"owner_id": owner_id, "event": event, "created_at": created_at, "previous_hash": previous_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    event_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO audit_logs(owner_id,event,created_at,previous_hash,event_hash) VALUES(?,?,?,?,?)",
        (owner_id, event, created_at, previous_hash, event_hash),
    )


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "checkin_interval": row["checkin_interval"],
        "created_at": row["created_at"],
    }


class RegisterPayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=10, max_length=72)
    face_enrollment_samples_b64: list[str] = Field(min_length=5, max_length=5)


class LoginPayload(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RecipientPayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    relationship: str = Field(min_length=2, max_length=60)


class NomineePayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    relationship: str = Field(min_length=2, max_length=60)
    phone: str = Field(default="", max_length=32)


class ProfilePayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=5, max_length=32)
    checkin_interval: int = Field(ge=7, le=365)


def issue_token(user_id: int) -> str:
    now = utc_now()
    expires_at = now + timedelta(hours=12)
    session_id = secrets.token_urlsafe(24)
    with db() as connection:
        connection.execute(
            "INSERT INTO sessions(id,user_id,expires_at) VALUES(?,?,?)",
            (session_id, user_id, expires_at.isoformat()),
        )
    return jwt.encode(
        {"sub": str(user_id), "jti": session_id, "iat": now, "exp": expires_at},
        JWT_SECRET,
        algorithm="HS256",
    )


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> sqlite3.Row:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please sign in to continue.")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload["sub"])
        session_id = payload["jti"]
    except (jwt.InvalidTokenError, KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please sign in again.") from error
    with db() as connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        session = connection.execute(
            "SELECT id FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (session_id, user_id),
        ).fetchone()
    if user is None or session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is no longer active. Please sign in again.")
    return user


@app.get("/api/health")
def health() -> dict:
    configured = providers_available()
    return {
        "status": "ok",
        "verification_mode": "local" if configured else "unavailable",
        "ai_verification_configured": configured,
    }


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterPayload) -> dict:
    if len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be 72 bytes or fewer.")
    decoded_samples = []
    for encoded_sample in payload.face_enrollment_samples_b64:
        if len(encoded_sample) > 3 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each face sample must be 2 MB or smaller.")
        try:
            raw = base64.b64decode(encoded_sample, validate=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="A face enrollment sample is not valid base64.") from error
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each face sample must be 2 MB or smaller.")
        if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")):
            raise HTTPException(status_code=422, detail="Each face sample must be a JPEG or PNG image.")
        decoded_samples.append(encoded_sample)
    face_blob = encrypt_bytes(json.dumps(decoded_samples, separators=(",", ":")).encode("ascii"))
    try:
        with db() as connection:
            cursor = connection.execute(
                """INSERT INTO users(name,email,phone,password_hash,face_enrollment,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    payload.name.strip(),
                    payload.email.strip().lower(),
                    payload.phone.strip(),
                    bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()),
                    face_blob,
                    utc_now().isoformat(),
                ),
            )
            user_id = cursor.lastrowid
            audit(connection, user_id, "account.created")
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
    return {"user": public_user(user), "message": "Account created. You can now sign in."}


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> dict:
    if len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    with db() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE email = ?", (payload.email.strip().lower(),)
        ).fetchone()
        if user is None or not bcrypt.checkpw(payload.password.encode("utf-8"), user["password_hash"]):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        access_token = issue_token(user["id"])
        audit(connection, user["id"], "account.signed_in")
    return {"access_token": access_token, "token_type": "bearer", "user": public_user(user)}


@app.post("/api/auth/logout", status_code=204)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: sqlite3.Row = Depends(current_user),
) -> None:
    payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
    with db() as connection:
        connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (utc_now().isoformat(), payload["jti"], user["id"]),
        )
        audit(connection, user["id"], "account.signed_out")


@app.get("/api/auth/me")
def me(user: sqlite3.Row = Depends(current_user)) -> dict:
    return {"user": public_user(user)}


@app.get("/api/dashboard")
def dashboard(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        last = connection.execute(
            "SELECT created_at,status,mode FROM checkins WHERE owner_id=? ORDER BY id DESC LIMIT 1",
            (user["id"],),
        ).fetchone()
        docs = connection.execute(
            "SELECT COUNT(*) AS count FROM documents WHERE owner_id=?", (user["id"],)
        ).fetchone()["count"]
        recipients = connection.execute(
            "SELECT COUNT(*) AS count FROM recipients WHERE owner_id=?", (user["id"],)
        ).fetchone()["count"]
    next_due = None
    if last and last["status"] == "success":
        next_due = (
            datetime.fromisoformat(last["created_at"]) + timedelta(days=user["checkin_interval"])
        ).isoformat()
    return {
        "user": public_user(user),
        "account_status": "Active",
        "last_checkin": dict(last) if last else None,
        "next_checkin": next_due,
        "documents_count": docs,
        "recipients_count": recipients,
    }


@app.get("/api/documents")
def list_documents(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE owner_id=? ORDER BY id DESC""",
            (user["id"],),
        ).fetchall()
    return {"documents": [dict(row) for row in rows]}


@app.post("/api/documents", status_code=201)
async def upload_document(
    name: str = Form(..., min_length=2, max_length=120),
    category: str = Form(..., min_length=2, max_length=40),
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="This file type is not supported. Choose a document, image, audio, video, or archive file.")
    data = await file.read(MAX_FILE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The selected file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 25 MB or smaller.")
    content_type = file.content_type or "application/octet-stream"
    with db() as connection:
        cursor = connection.execute(
            """INSERT INTO documents(owner_id,name,category,file_name,content_type,file_size,encrypted_data,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], name.strip(), category, Path(file.filename or "document").name,
             content_type, len(data), encrypt_bytes(data), utc_now().isoformat()),
        )
        audit(connection, user["id"], f"document.uploaded:{cursor.lastrowid}")
        row = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE id=?""",
            (cursor.lastrowid,),
        ).fetchone()
    return {"document": dict(row)}


@app.get("/api/documents/{document_id}/download")
def download_document(document_id: int, user: sqlite3.Row = Depends(current_user)) -> Response:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id=? AND owner_id=?", (document_id, user["id"])
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Document was not found.")
        audit(connection, user["id"], f"document.downloaded:{document_id}")
    return Response(
        content=row["encrypted_data"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{row["id"]}.cgenc"',
            "X-Content-Type-Options": "nosniff",
            "X-CloudGuard-Encryption": "AES-256-GCM",
            "Cache-Control": "private, no-store",
        },
    )


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int, user: sqlite3.Row = Depends(current_user)) -> None:
    with db() as connection:
        cursor = connection.execute(
            "DELETE FROM documents WHERE id=? AND owner_id=?", (document_id, user["id"])
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Document was not found.")
        audit(connection, user["id"], f"document.deleted:{document_id}")


@app.get("/api/recipients")
def list_recipients(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            "SELECT id,name,email,relationship,created_at FROM recipients WHERE owner_id=? ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
    return {"recipients": [dict(row) for row in rows]}


@app.post("/api/recipients", status_code=201)
def create_recipient(payload: RecipientPayload, user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO recipients(owner_id,name,email,relationship,created_at) VALUES(?,?,?,?,?)",
            (user["id"], payload.name.strip(), payload.email.strip().lower(),
             payload.relationship.strip(), utc_now().isoformat()),
        )
        audit(connection, user["id"], f"recipient.added:{cursor.lastrowid}")
        row = connection.execute(
            "SELECT id,name,email,relationship,created_at FROM recipients WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    return {"recipient": dict(row)}


@app.patch("/api/recipients/{recipient_id}")
def update_recipient(
    recipient_id: int, payload: RecipientPayload, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with db() as connection:
        cursor = connection.execute(
            """UPDATE recipients SET name=?,email=?,relationship=?
               WHERE id=? AND owner_id=?""",
            (payload.name.strip(), payload.email.strip().lower(), payload.relationship.strip(),
             recipient_id, user["id"]),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipient was not found.")
        audit(connection, user["id"], f"recipient.updated:{recipient_id}")
        row = connection.execute(
            "SELECT id,name,email,relationship,created_at FROM recipients WHERE id=?",
            (recipient_id,),
        ).fetchone()
    return {"recipient": dict(row)}


@app.delete("/api/recipients/{recipient_id}", status_code=204)
def delete_recipient(recipient_id: int, user: sqlite3.Row = Depends(current_user)) -> None:
    with db() as connection:
        cursor = connection.execute(
            "DELETE FROM recipients WHERE id=? AND owner_id=?", (recipient_id, user["id"])
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipient was not found.")
        audit(connection, user["id"], f"recipient.removed:{recipient_id}")


@app.get("/api/nominee")
def get_nominee(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        row = connection.execute(
            """SELECT id,name,email,relationship,phone,created_at,updated_at
               FROM nominees WHERE owner_id=?""",
            (user["id"],),
        ).fetchone()
    return {"nominee": dict(row) if row else None}


@app.put("/api/nominee")
def save_nominee(payload: NomineePayload, user: sqlite3.Row = Depends(current_user)) -> dict:
    now = utc_now().isoformat()
    with db() as connection:
        existing = connection.execute(
            "SELECT id FROM nominees WHERE owner_id=?", (user["id"],)
        ).fetchone()
        connection.execute(
            """INSERT INTO nominees(owner_id,name,email,relationship,phone,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                   name=excluded.name,email=excluded.email,relationship=excluded.relationship,
                   phone=excluded.phone,updated_at=excluded.updated_at""",
            (user["id"], payload.name.strip(), payload.email.strip().lower(),
             payload.relationship.strip(), payload.phone.strip(), now, now),
        )
        nominee_id = existing["id"] if existing else connection.execute(
            "SELECT id FROM nominees WHERE owner_id=?", (user["id"],)
        ).fetchone()["id"]
        audit(connection, user["id"], f"nominee.{'updated' if existing else 'added'}:{nominee_id}")
        row = connection.execute(
            """SELECT id,name,email,relationship,phone,created_at,updated_at
               FROM nominees WHERE id=? AND owner_id=?""",
            (nominee_id, user["id"]),
        ).fetchone()
    return {"nominee": dict(row)}


@app.delete("/api/nominee", status_code=204)
def delete_nominee(user: sqlite3.Row = Depends(current_user)) -> None:
    with db() as connection:
        cursor = connection.execute("DELETE FROM nominees WHERE owner_id=?", (user["id"],))
        if cursor.rowcount:
            audit(connection, user["id"], "nominee.removed")


@app.post("/api/check-ins", status_code=201)
def create_checkin(
    sentence: str = Form(..., min_length=5, max_length=250),
    recording: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    if sentence not in CHECKIN_SENTENCES:
        raise HTTPException(status_code=422, detail="The check-in phrase is not valid. Start a new check-in and try again.")
    if not providers_available():
        raise HTTPException(
            status_code=503,
            detail="Local face, liveness, and speech verification dependencies are unavailable. No check-in was saved.",
        )
    suffix = Path(recording.filename or "").suffix.lower()
    if suffix not in {".webm", ".mp4", ".mov"}:
        raise HTTPException(status_code=415, detail="Record a WebM or MP4 check-in clip.")
    data = recording.file.read(MAX_CHECKIN_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The check-in recording is empty.")
    if len(data) > MAX_CHECKIN_BYTES:
        raise HTTPException(status_code=413, detail="Check-in recordings must be 20 MB or smaller.")
    with db() as connection:
        owner = connection.execute(
            "SELECT face_enrollment FROM users WHERE id=?", (user["id"],)
        ).fetchone()
    if owner is None or owner["face_enrollment"] is None:
        raise HTTPException(status_code=409, detail="Face enrollment is missing. Re-enroll your face before checking in.")
    try:
        enrollment_samples = json.loads(decrypt_bytes(owner["face_enrollment"]))
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=409, detail="Face enrollment data could not be read. Please contact support.") from error
    with tempfile.TemporaryDirectory(prefix="cloudguard-checkin-") as directory:
        video_path = Path(directory) / f"recording{suffix}"
        video_path.write_bytes(data)
        try:
            statuses = verify_recording(video_path, enrollment_samples, sentence)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except VerificationUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except Exception as error:
            logger.exception("Local check-in verification failed.")
            raise HTTPException(
                status_code=503,
                detail="Local verification could not complete. No result was saved; check the API log and try again.",
            ) from error
    passed = all(statuses.get(check) == "passed" for check in ("face_status", "liveness_status", "speech_status"))
    checkin_status = "success" if passed else "failed"
    event = "checkin.verified_passed" if passed else "checkin.verified_failed"
    with db() as connection:
        cursor = connection.execute(
            """INSERT INTO checkins(owner_id,mode,status,face_status,liveness_status,speech_status,sentence,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], "local_ai", checkin_status, statuses["face_status"], statuses["liveness_status"],
             statuses["speech_status"], sentence, utc_now().isoformat()),
        )
        audit(connection, user["id"], f"{event}:{cursor.lastrowid}")
        row = connection.execute("SELECT * FROM checkins WHERE id=?", (cursor.lastrowid,)).fetchone()
    return {
        "checkin": dict(row),
        "passed": passed,
        "message": "All verification checks passed." if passed else "One or more verification checks did not pass.",
    }


@app.get("/api/check-ins")
def list_checkins(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,mode,status,face_status,liveness_status,speech_status,sentence,created_at
               FROM checkins WHERE owner_id=? ORDER BY id DESC LIMIT 100""",
            (user["id"],),
        ).fetchall()
    return {"checkins": [dict(row) for row in rows]}


@app.patch("/api/profile")
def update_profile(payload: ProfilePayload, user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        connection.execute(
            "UPDATE users SET name=?,phone=?,checkin_interval=? WHERE id=?",
            (payload.name.strip(), payload.phone.strip(), payload.checkin_interval, user["id"]),
        )
        audit(connection, user["id"], "profile.updated")
        updated = connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return {"user": public_user(updated)}
