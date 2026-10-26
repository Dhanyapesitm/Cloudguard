import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote, urlencode, urlsplit

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import bcrypt
import jwt
import resend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values, load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, StrictInt
from resend.exceptions import ResendError
from verification import VerificationUnavailable, providers_available, verify_recording

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
os.environ.setdefault("DEEPFACE_BACKEND_ENGINE", os.getenv("CLOUDGUARD_DEEPFACE_ENGINE", "tensorflow"))
_db_env = os.getenv("CLOUDGUARD_DATABASE")
if _db_env:
    _db_path = Path(_db_env)
    DATABASE = _db_path if _db_path.is_absolute() else (BASE_DIR / _db_path).resolve()
else:
    DATABASE = (BASE_DIR / "cloudguard.db").resolve()
JWT_SECRET = os.getenv("CLOUDGUARD_JWT_SECRET")
FILE_KEY_B64 = os.getenv("CLOUDGUARD_FILE_KEY")
ENVIRONMENT = os.getenv("CLOUDGUARD_ENV", "development")
REVIEWER_EMAIL = os.getenv("CLOUDGUARD_REVIEWER_EMAIL", "").strip().lower()
REVIEWER_PASSWORD_HASH = os.getenv("CLOUDGUARD_REVIEWER_PASSWORD_HASH", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_CHECKIN_BYTES = 20 * 1024 * 1024
MAX_DEATH_EVIDENCE_BYTES = 10 * 1024 * 1024
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
    if bool(REVIEWER_EMAIL) != bool(REVIEWER_PASSWORD_HASH):
        raise RuntimeError(
            "Configure both CLOUDGUARD_REVIEWER_EMAIL and CLOUDGUARD_REVIEWER_PASSWORD_HASH."
        )
    if REVIEWER_PASSWORD_HASH:
        try:
            bcrypt.checkpw(b"configuration-check", REVIEWER_PASSWORD_HASH.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise RuntimeError("CLOUDGUARD_REVIEWER_PASSWORD_HASH must be a bcrypt hash.") from error
    init_database()
    yield


app = FastAPI(title="CloudGuard API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CLOUDGUARD_FRONTEND_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


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
                created_at TEXT NOT NULL,
                supabase_uid TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                email TEXT NOT NULL COLLATE NOCASE,
                role TEXT NOT NULL CHECK(role IN ('nominee', 'recipient')),
                target_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('pending', 'sent', 'claimed', 'revoked', 'failed')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                sent_at TEXT,
                claimed_at TEXT,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_invitations_owner_target
                ON invitations(owner_id, role, target_id, created_at);
            CREATE TABLE IF NOT EXISTS guest_sessions (
                id TEXT PRIMARY KEY,
                invitation_id INTEGER NOT NULL REFERENCES invitations(id) ON DELETE CASCADE,
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
            CREATE TABLE IF NOT EXISTS document_shares (
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (document_id, recipient_id)
            );
            CREATE TABLE IF NOT EXISTS recipient_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL UNIQUE REFERENCES recipients(id) ON DELETE CASCADE,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recipient_sessions (
                id TEXT PRIMARY KEY,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS nominee_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nominee_id INTEGER NOT NULL UNIQUE REFERENCES nominees(id) ON DELETE CASCADE,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nominee_sessions (
                id TEXT PRIMARY KEY,
                nominee_id INTEGER NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS reviewer_sessions (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS death_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                nominee_id INTEGER NOT NULL UNIQUE REFERENCES nominees(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('under_review','more_information_required','approved','rejected')),
                note TEXT NOT NULL DEFAULT '',
                evidence_ciphertext BLOB NOT NULL,
                evidence_name TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                evidence_size INTEGER NOT NULL,
                reviewer_email TEXT,
                reviewer_message TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL,
                reviewed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nominee_case_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nominee_id INTEGER NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                case_id INTEGER REFERENCES death_cases(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS medical_holds (
                owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('active','awaiting_checkin','inactive')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS document_releases (
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('available', 'revoked')),
                released_at TEXT,
                revoked_at TEXT,
                PRIMARY KEY (document_id, recipient_id)
            );
            CREATE TABLE IF NOT EXISTS recipient_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                detail TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                token_id TEXT PRIMARY KEY,
                user_id INTEGER,
                revoked_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        try:
            connection.execute("ALTER TABLE users ADD COLUMN supabase_uid TEXT")
        except sqlite3.OperationalError:
            pass
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_supabase_uid ON users(supabase_uid)")


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


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def get_jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    url = supabase_url.rstrip("/")
    jwks_uri = f"{url}/auth/v1/.well-known/jwks.json"
    if jwks_uri not in _jwks_clients:
        _jwks_clients[jwks_uri] = jwt.PyJWKClient(
            jwks_uri,
            cache_jwk_set=True,
            lifespan=600,
            headers={"User-Agent": "CloudGuard/1.0"},
            timeout=10,
        )
    return _jwks_clients[jwks_uri]


def verify_supabase_jwt(raw_token: str) -> Optional[dict]:
    """
    Verifies a Supabase-issued JWT access token.
    Supports both:
    1. Modern asymmetric signing (ES256, RS256) via the Supabase JWKS endpoint.
    2. Legacy symmetric signing (HS256, HS384, HS512) via SUPABASE_JWT_SECRET.
    Returns the verified token payload if valid, None if not a Supabase token or not configured.
    Raises HTTPException 401 if it is a Supabase token with invalid/expired signature.
    """
    if not raw_token:
        return None
    try:
        unverified_header = jwt.get_unverified_header(raw_token)
        unverified = jwt.decode(raw_token, options={"verify_signature": False})
        aud = unverified.get("aud")
        iss = unverified.get("iss", "")
        is_supabase = (
            aud == "authenticated"
            or (isinstance(aud, list) and "authenticated" in aud)
            or ("auth/v1" in iss)
        )
        if not is_supabase:
            return None
    except Exception:
        return None

    alg = unverified_header.get("alg", "HS256")

    # 1. Asymmetric algorithms (ES256, RS256, etc.) - modern Supabase default
    if alg in ("ES256", "RS256", "ES384", "ES512", "RS384", "RS512"):
        if not SUPABASE_URL:
            return None
        try:
            jwks_client = get_jwks_client(SUPABASE_URL)
            signing_key = jwks_client.get_signing_key_from_jwt(raw_token)
            payload = jwt.decode(
                raw_token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
                options={"verify_exp": True, "verify_aud": True},
                leeway=60,
            )
            return payload
        except (jwt.InvalidTokenError, ValueError, Exception) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Supabase authentication token is invalid or expired ({alg}): {str(error)}",
            ) from error

    # 2. Symmetric algorithms (HS256, HS384, HS512) - legacy Supabase default
    if not SUPABASE_JWT_SECRET:
        return None

    try:
        payload = jwt.decode(
            raw_token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256", "HS384", "HS512"],
            audience="authenticated",
            options={"verify_exp": True, "verify_aud": True},
            leeway=60,
        )
        return payload
    except (jwt.InvalidTokenError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Supabase authentication token is invalid or expired ({alg}): {str(error)}",
        ) from error


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "checkin_interval": row["checkin_interval"],
        "created_at": row["created_at"],
        "supabase_uid": row["supabase_uid"] if "supabase_uid" in row.keys() else None,
    }


class RegisterPayload(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=32)
    password: Optional[str] = Field(default=None, max_length=72)
    face_enrollment_samples_b64: list[str] = Field(min_length=5, max_length=5)
    supabase_uid: Optional[str] = None


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


class FaceEnrollmentPayload(BaseModel):
    face_enrollment_samples_b64: list[str] = Field(min_length=5, max_length=5)


class CreateInvitationPayload(BaseModel):
    role: Literal["nominee", "recipient"]
    target_id: StrictInt = Field(gt=0)


class ClaimInvitationPayload(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    password: Optional[str] = Field(default=None, min_length=10, max_length=72)
    email: Optional[str] = None
    role: Optional[str] = None


class ReleasePayload(BaseModel):
    recipient_id: StrictInt = Field(gt=0)

class MedicalHoldPayload(BaseModel):
    action: Literal["start", "end"]

class DeathCaseReviewPayload(BaseModel):
    decision: Literal["more_information_required", "approved", "rejected"]
    message: str = Field(min_length=5, max_length=2000)


def invitation_target(
    connection: sqlite3.Connection, owner_id: int, role: str, target_id: int
) -> sqlite3.Row:
    if role == "nominee":
        target = connection.execute(
            "SELECT id,name,email FROM nominees WHERE id=? AND owner_id=?",
            (target_id, owner_id),
        ).fetchone()
    elif role == "recipient":
        target = connection.execute(
            "SELECT id,name,email FROM recipients WHERE id=? AND owner_id=?",
            (target_id, owner_id),
        ).fetchone()
    else:
        raise HTTPException(status_code=422, detail="Invitation role is invalid.")
    if target is None:
        raise HTTPException(status_code=404, detail="The selected contact was not found.")
    return target


def revoke_target_invitations(
    connection: sqlite3.Connection,
    owner_id: int,
    role: str,
    target_id: int,
    exclude_invitation_id: Optional[int] = None,
) -> None:
    now = utc_now().isoformat()
    exclude_clause = " AND id<>?" if exclude_invitation_id is not None else ""
    parameters = [now, owner_id, role, target_id]
    if exclude_invitation_id is not None:
        parameters.append(exclude_invitation_id)
    updated = connection.execute(
        """UPDATE invitations SET status='revoked',revoked_at=?
           WHERE owner_id=? AND role=? AND target_id=?
             AND status IN ('pending','sent','claimed')""" + exclude_clause,
        parameters,
    )
    if updated.rowcount:
        audit(connection, owner_id, f"invitation.access_revoked:{role}:{target_id}")
    connection.execute(
        """UPDATE guest_sessions SET revoked_at=?
           WHERE revoked_at IS NULL AND invitation_id IN (
               SELECT id FROM invitations
               WHERE owner_id=? AND role=? AND target_id=? AND status='revoked'
           )""",
        (now, owner_id, role, target_id),
    )
    if role == "recipient":
        connection.execute(
            "UPDATE recipient_sessions SET revoked_at=? WHERE recipient_id=? AND revoked_at IS NULL",
            (now, target_id),
        )
    elif role == "nominee":
        connection.execute(
            "UPDATE nominee_sessions SET revoked_at=? WHERE nominee_id=? AND revoked_at IS NULL",
            (now, target_id),
        )


def _password_hash(password: str) -> bytes:
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be 72 bytes or fewer.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())


def _log_recipient_event(
    connection: sqlite3.Connection,
    recipient_id: int,
    owner_id: int,
    event_type: str,
    document_id: Optional[int] = None,
    detail: str = "",
) -> None:
    connection.execute(
        """INSERT INTO recipient_events(recipient_id,owner_id,event_type,document_id,detail,created_at)
           VALUES(?,?,?,?,?,?)""",
        (recipient_id, owner_id, event_type, document_id, detail, utc_now().isoformat()),
    )


def _medical_hold_state(connection: sqlite3.Connection, owner_id: int) -> dict:
    row = connection.execute(
        "SELECT status,started_at,ended_at,updated_at FROM medical_holds WHERE owner_id=?",
        (owner_id,),
    ).fetchone()
    return dict(row) if row else {
        "status": "inactive",
        "started_at": None,
        "ended_at": None,
        "updated_at": None,
    }


def _set_document_release(
    connection: sqlite3.Connection, document_id: int, recipient_id: int, status: str
) -> None:
    now = utc_now().isoformat()
    existing = connection.execute(
        "SELECT status FROM document_releases WHERE document_id=? AND recipient_id=?",
        (document_id, recipient_id),
    ).fetchone()
    if existing:
        if status == "available":
            connection.execute(
                """UPDATE document_releases SET status='available', released_at=?, revoked_at=NULL
                   WHERE document_id=? AND recipient_id=?""",
                (now, document_id, recipient_id),
            )
        else:
            connection.execute(
                """UPDATE document_releases SET status='revoked', revoked_at=?
                   WHERE document_id=? AND recipient_id=?""",
                (now, document_id, recipient_id),
            )
        return
    connection.execute(
        """INSERT INTO document_releases(document_id,recipient_id,status,released_at,revoked_at)
           VALUES(?,?,?,?,?)""",
        (
            document_id,
            recipient_id,
            status,
            now if status == "available" else None,
            now if status == "revoked" else None,
        ),
    )


def _replace_document_shares(
    connection: sqlite3.Connection,
    owner_id: int,
    document_id: int,
    recipient_ids: list[int],
) -> None:
    current = {
        row["recipient_id"]
        for row in connection.execute(
            "SELECT recipient_id FROM document_shares WHERE document_id=?",
            (document_id,),
        )
    }
    new_ids = list(dict.fromkeys(recipient_ids))
    new_set = set(new_ids)
    for recipient_id in current - new_set:
        _set_document_release(connection, document_id, recipient_id, "revoked")
        _log_recipient_event(
            connection,
            recipient_id,
            owner_id,
            "access_revoked",
            document_id,
            "The owner removed this document assignment.",
        )
    connection.execute("DELETE FROM document_shares WHERE document_id=?", (document_id,))
    now = utc_now().isoformat()
    for recipient_id in new_ids:
        connection.execute(
            "INSERT INTO document_shares(document_id,recipient_id,created_at) VALUES(?,?,?)",
            (document_id, recipient_id, now),
        )
        if recipient_id not in current:
            connection.execute(
                "DELETE FROM document_releases WHERE document_id=? AND recipient_id=?",
                (document_id, recipient_id),
            )
            _log_recipient_event(
                connection,
                recipient_id,
                owner_id,
                "assignment_pending",
                document_id,
                "A document was assigned. Access stays locked until the owner approves release.",
            )


def _recipient_documents(connection: sqlite3.Connection, recipient_id: int) -> list[dict]:
    owner = connection.execute(
        "SELECT owner_id FROM recipients WHERE id=?", (recipient_id,)
    ).fetchone()
    if owner is None:
        return []
    owner_id = owner["owner_id"]
    hold = _medical_hold_state(connection, owner_id)
    has_approved_case = connection.execute(
        "SELECT 1 FROM death_cases WHERE owner_id=? AND status='approved'",
        (owner_id,),
    ).fetchone() is not None
    shares = connection.execute(
        """SELECT documents.id, documents.name, documents.category, documents.file_name,
                  documents.content_type, documents.file_size, documents.created_at,
                  document_shares.created_at AS assigned_at
           FROM document_shares
           JOIN documents ON documents.id = document_shares.document_id
           WHERE document_shares.recipient_id=?""",
        (recipient_id,),
    ).fetchall()
    releases = {
        row["document_id"]: dict(row)
        for row in connection.execute(
            """SELECT document_id,status,released_at,revoked_at
               FROM document_releases WHERE recipient_id=?""",
            (recipient_id,),
        )
    }
    documents = {}
    for row in shares:
        release = releases.get(row["id"])
        if release and release["status"] == "revoked":
            access_status = "revoked"
        elif (
            release
            and release["status"] == "available"
            and hold["status"] == "inactive"
            and has_approved_case
        ):
            access_status = "available"
        else:
            access_status = "pending"
        documents[row["id"]] = {
            **dict(row),
            "access_status": access_status,
            "released_at": release["released_at"] if release else None,
            "revoked_at": release["revoked_at"] if release else None,
            "release_hold": hold["status"] != "inactive",
            "case_approved": has_approved_case,
        }
    for document_id, release in releases.items():
        if document_id in documents or release["status"] != "revoked":
            continue
        document = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE id=?""",
            (document_id,),
        ).fetchone()
        if document is None:
            continue
        documents[document_id] = {
            **dict(document),
            "assigned_at": None,
            "access_status": "revoked",
            "released_at": release["released_at"],
            "revoked_at": release["revoked_at"],
            "release_hold": hold["status"] != "inactive",
            "case_approved": has_approved_case,
        }
    return sorted(documents.values(), key=lambda item: item["id"], reverse=True)


def _issue_recipient_session(recipient_id: int, email: str) -> tuple[str, datetime]:
    now = utc_now()
    expires_at = now + timedelta(hours=12)
    session_id = secrets.token_urlsafe(24)
    with db() as connection:
        connection.execute(
            "INSERT INTO recipient_sessions(id,recipient_id,expires_at) VALUES(?,?,?)",
            (session_id, recipient_id, expires_at.isoformat()),
        )
    token = jwt.encode(
        {
            "sub": email,
            "jti": session_id,
            "iat": now,
            "exp": expires_at,
            "principal_type": "recipient",
            "recipient_id": recipient_id,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, expires_at


def _issue_nominee_session(nominee_id: int, email: str) -> tuple[str, datetime]:
    now = utc_now()
    expires_at = now + timedelta(hours=12)
    session_id = secrets.token_urlsafe(24)
    with db() as connection:
        connection.execute(
            "INSERT INTO nominee_sessions(id,nominee_id,expires_at) VALUES(?,?,?)",
            (session_id, nominee_id, expires_at.isoformat()),
        )
    token = jwt.encode(
        {
            "sub": email,
            "jti": session_id,
            "iat": now,
            "exp": expires_at,
            "principal_type": "nominee",
            "nominee_id": nominee_id,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, expires_at


def _issue_reviewer_session(email: str) -> tuple[str, datetime]:
    now = utc_now()
    expires_at = now + timedelta(hours=8)
    session_id = secrets.token_urlsafe(24)
    with db() as connection:
        connection.execute(
            "INSERT INTO reviewer_sessions(id,email,expires_at) VALUES(?,?,?)",
            (session_id, email, expires_at.isoformat()),
        )
    token = jwt.encode(
        {
            "sub": email,
            "jti": session_id,
            "iat": now,
            "exp": expires_at,
            "principal_type": "reviewer",
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, expires_at


def recipient_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="A valid recipient sign-in is required.")
    supabase_payload = verify_supabase_jwt(credentials.credentials)
    if supabase_payload:
        email = str(supabase_payload.get("email", "")).strip().lower()
        sub = str(supabase_payload.get("sub", "")).strip()
        with db() as connection:
            row = connection.execute(
                """SELECT recipients.id AS recipient_id, recipients.name, recipients.email, recipients.owner_id,
                          users.name AS owner_name
                   FROM recipients
                   JOIN users ON users.id = recipients.owner_id
                   WHERE LOWER(recipients.email) = ?""",
                (email,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=403, detail="This account is not authorized as a recipient.")
        return {
            "email": email,
            "name": row["name"],
            "role": "recipient",
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "recipient_id": row["recipient_id"],
            "session_id": f"supabase-{sub}",
        }

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("principal_type") != "recipient":
            raise HTTPException(status_code=401, detail="A valid recipient sign-in is required.")
        session_id = payload["jti"]
        recipient_id = int(payload["recipient_id"])
        email = str(payload["sub"]).strip().lower()
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="The recipient sign-in has expired.") from error

    with db() as connection:
        row = connection.execute(
            """SELECT recipient_sessions.id, recipient_sessions.expires_at, recipient_sessions.revoked_at,
                      recipients.id AS recipient_id, recipients.name, recipients.email, recipients.owner_id,
                      users.name AS owner_name
               FROM recipient_sessions
               JOIN recipients ON recipients.id = recipient_sessions.recipient_id
               JOIN users ON users.id = recipients.owner_id
               WHERE recipient_sessions.id=? AND recipients.id=?""",
            (session_id, recipient_id),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"] is not None
            or row["email"].strip().lower() != email
            or _parse_iso_utc(row["expires_at"]) <= utc_now()
        ):
            raise HTTPException(status_code=401, detail="The recipient sign-in is no longer active.")
        return {
            "email": email,
            "name": row["name"],
            "role": "recipient",
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "recipient_id": row["recipient_id"],
            "session_id": row["id"],
        }


def nominee_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="A valid nominee sign-in is required.")
    supabase_payload = verify_supabase_jwt(credentials.credentials)
    if supabase_payload:
        email = str(supabase_payload.get("email", "")).strip().lower()
        sub = str(supabase_payload.get("sub", "")).strip()
        with db() as connection:
            row = connection.execute(
                """SELECT nominees.id AS nominee_id, nominees.name, nominees.email, nominees.owner_id,
                          users.name AS owner_name
                   FROM nominees
                   JOIN users ON users.id = nominees.owner_id
                   WHERE LOWER(nominees.email) = ?""",
                (email,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=403, detail="This account is not authorized as a nominee.")
        return {
            "email": email,
            "name": row["name"],
            "role": "nominee",
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "nominee_id": row["nominee_id"],
            "session_id": f"supabase-{sub}",
        }

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("principal_type") != "nominee":
            raise HTTPException(status_code=401, detail="A valid nominee sign-in is required.")
        session_id = payload["jti"]
        nominee_id = int(payload["nominee_id"])
        email = str(payload["sub"]).strip().lower()
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="The nominee sign-in has expired.") from error

    with db() as connection:
        row = connection.execute(
            """SELECT nominee_sessions.id,nominee_sessions.expires_at,nominee_sessions.revoked_at,
                      nominees.id AS nominee_id,nominees.name,nominees.email,nominees.owner_id,
                      users.name AS owner_name
               FROM nominee_sessions
               JOIN nominees ON nominees.id=nominee_sessions.nominee_id
               JOIN users ON users.id=nominees.owner_id
               WHERE nominee_sessions.id=? AND nominees.id=?""",
            (session_id, nominee_id),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"] is not None
            or row["email"].strip().lower() != email
            or _parse_iso_utc(row["expires_at"]) <= utc_now()
        ):
            raise HTTPException(status_code=401, detail="The nominee sign-in is no longer active.")
        return {
            "email": email,
            "name": row["name"],
            "role": "nominee",
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "nominee_id": row["nominee_id"],
            "session_id": row["id"],
        }


def reviewer_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="A valid reviewer sign-in is required.")
    supabase_payload = verify_supabase_jwt(credentials.credentials)
    if supabase_payload:
        email = str(supabase_payload.get("email", "")).strip().lower()
        sub = str(supabase_payload.get("sub", "")).strip()
        if not REVIEWER_EMAIL or email != REVIEWER_EMAIL:
            raise HTTPException(status_code=403, detail="This account is not authorized as a reviewer.")
        return {"email": email, "session_id": f"supabase-{sub}", "role": "reviewer"}

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("principal_type") != "reviewer":
            raise HTTPException(status_code=401, detail="A valid reviewer sign-in is required.")
        session_id = payload["jti"]
        email = str(payload["sub"]).strip().lower()
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="The reviewer sign-in has expired.") from error
    with db() as connection:
        row = connection.execute(
            "SELECT id,expires_at,revoked_at,email FROM reviewer_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    if (
        row is None
        or row["revoked_at"] is not None
        or row["email"].strip().lower() != email
        or email != REVIEWER_EMAIL
        or _parse_iso_utc(row["expires_at"]) <= utc_now()
    ):
        raise HTTPException(status_code=401, detail="The reviewer sign-in is no longer active.")
    return {"email": email, "session_id": row["id"], "role": "reviewer"}


def guest_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="A valid invitation sign-in is required.")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("principal_type") != "guest":
            raise HTTPException(status_code=401, detail="A valid invitation sign-in is required.")
        session_id = payload["jti"]
        invitation_id = int(payload["invitation_id"])
        email = str(payload["sub"]).strip().lower()
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="The invitation sign-in has expired.") from error

    with db() as connection:
        row = connection.execute(
            """SELECT guest_sessions.id,guest_sessions.expires_at AS session_expires_at,
                      guest_sessions.revoked_at AS session_revoked_at,
                      invitations.id AS invitation_id,invitations.owner_id,
                      invitations.email,invitations.role,invitations.target_id,
                      invitations.status,invitations.revoked_at AS invitation_revoked_at,
                      users.name AS owner_name
               FROM guest_sessions
               JOIN invitations ON invitations.id=guest_sessions.invitation_id
               JOIN users ON users.id=invitations.owner_id
               WHERE guest_sessions.id=? AND invitations.id=?""",
            (session_id, invitation_id),
        ).fetchone()
        if (
            row is None
            or row["session_revoked_at"] is not None
            or row["invitation_revoked_at"] is not None
            or row["status"] != "claimed"
            or row["email"].strip().lower() != email
            or _parse_iso_utc(row["session_expires_at"]) <= utc_now()
        ):
            raise HTTPException(status_code=401, detail="The invitation sign-in is no longer active.")
        try:
            target = invitation_target(connection, row["owner_id"], row["role"], row["target_id"])
        except HTTPException as error:
            raise HTTPException(status_code=401, detail="The invitation sign-in is no longer active.") from error
        if target["email"].strip().lower() != email:
            raise HTTPException(status_code=401, detail="The invitation sign-in is no longer active.")
        return {
            "email": email,
            "role": row["role"],
            "owner_id": row["owner_id"],
            "owner_name": row["owner_name"],
            "target_id": row["target_id"],
            "invitation_id": row["invitation_id"],
            "session_id": row["id"],
        }


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
        {
            "sub": str(user_id),
            "jti": session_id,
            "iat": now,
            "exp": expires_at,
            "principal_type": "owner",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> sqlite3.Row:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please sign in to continue.")

    supabase_payload = verify_supabase_jwt(credentials.credentials)
    if supabase_payload:
        email = str(supabase_payload.get("email", "")).strip().lower()
        sub = str(supabase_payload.get("sub", "")).strip()
        token_id = str(supabase_payload.get("jti", sub))
        with db() as connection:
            revoked = connection.execute(
                "SELECT 1 FROM revoked_tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
            if revoked:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Your session is no longer active. Please sign in again.",
                )
            user = connection.execute(
                "SELECT * FROM users WHERE (supabase_uid = ? AND supabase_uid IS NOT NULL) OR LOWER(email) = ?",
                (sub, email),
            ).fetchone()
            if user and ("supabase_uid" in user.keys() and not user["supabase_uid"]):
                connection.execute(
                    "UPDATE users SET supabase_uid = ? WHERE id = ?",
                    (sub, user["id"]),
                )
                user = connection.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No CloudGuard vault profile found for this account. Please complete registration.",
            )
        return user

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("principal_type", "owner") != "owner":
            raise HTTPException(status_code=401, detail="Owner sign-in is required.")
        user_id = int(payload["sub"])
        session_id = payload["jti"]
    except HTTPException:
        raise
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


def send_invitation_email(email: str, role: str, token: str) -> str:
    config, missing = invitation_email_configuration()
    if missing:
        raise RuntimeError(
            f"Invitation email configuration is missing: {', '.join(missing)}."
        )
    api_key = config["RESEND_API_KEY"]
    sender = config["RESEND_FROM_EMAIL"]
    app_url = config["CLOUDGUARD_APP_URL"].rstrip("/")
    parsed_url = urlsplit(app_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.query or parsed_url.fragment:
        raise RuntimeError("Invitation app URL configuration is invalid.")

    resend.api_key = api_key
    claim_url = f"{app_url}/#guest-invitation={token}"
    role_label = "Trusted Nominee" if role == "nominee" else "Authorized Recipient"
    article = "an" if role_label.startswith("A") else "a"
    text_content = (
        f"You have been invited to CloudGuard as {article} {role_label}.\n\n"
        f"CloudGuard is a digital-legacy document vault for securing important personal and legal records.\n\n"
        f"CloudGuard Application: {app_url}\n\n"
        "Open this secure, single-use link within 7 days to verify this email address"
        + (
            f" and create a password for your {role} account:\n"
            if role in {"recipient", "nominee"}
            else ":\n"
        )
        + f"{claim_url}\n\n"
        "Security & Access Boundary Notice:\n"
        "- Accepting this invitation verifies your email address and creates your account.\n"
        "- Accepting this invitation does NOT grant access to documents by itself.\n"
        "- Assigned files stay locked until the owner completes and approves release.\n"
        "- Missed owner check-ins never release documents automatically.\n\n"
        "If you did not expect this invitation, you can safely ignore this email."
    )
    html_content = (
        f"<div style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 24px; color: #252b3a; line-height: 1.6;\">"
        f"<div style=\"background: #272550; padding: 22px; border-radius: 12px; margin-bottom: 24px; text-align: center;\">"
        f"<h1 style=\"color: #ffffff; margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;\">CloudGuard<span style=\"color: #928bf5;\">.</span></h1>"
        f"<p style=\"color: #c1c0d7; margin: 6px 0 0; font-size: 13px;\">Digital-Legacy Document Vault</p>"
        f"</div>"
        f"<h2 style=\"color: #252b3a; font-size: 18px; margin-top: 0;\">You have been invited as {article} {role_label}</h2>"
        f"<p>You have been invited to join CloudGuard as {article} <strong>{role_label}</strong>. "
        f"CloudGuard provides secure, encrypted digital-legacy vault storage for essential records and documents.</p>"
        f"<div style=\"text-align: center; margin: 28px 0;\">"
        f"<a href=\"{claim_url}\" style=\"background: #5c57d9; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 600; font-size: 14px; display: inline-block;\">Accept Invitation & Set Up Account</a>"
        f"</div>"
        f"<p style=\"font-size: 12px; color: #7f8799; word-break: break-all;\">If the button above does not work, open this single-use link in your browser:<br /><a href=\"{claim_url}\" style=\"color: #5c57d9;\">{claim_url}</a></p>"
        f"<p style=\"font-size: 12px; color: #7f8799;\">This invitation is valid for 7 days and can only be used once.</p>"
        f"<div style=\"background: #f7f8fc; border-left: 4px solid #5c57d9; padding: 12px 16px; margin: 24px 0; border-radius: 4px;\">"
        f"<strong style=\"font-size: 12px; color: #252b3a;\">Security & Access Notice:</strong>"
        f"<ul style=\"font-size: 12px; color: #7f8799; margin: 6px 0 0; padding-left: 20px;\">"
        f"<li>Accepting this invitation verifies your email address and creates your account.</li>"
        f"<li>Accepting does <strong>not</strong> grant immediate access to documents.</li>"
        f"<li>Assigned files remain encrypted and locked until the account owner explicitly approves release.</li>"
        f"<li>Missed owner check-ins never release documents automatically.</li>"
        f"</ul>"
        f"</div>"
        f"<hr style=\"border: none; border-top: 1px solid #eceef4; margin: 24px 0;\" />"
        f"<p style=\"font-size: 11px; color: #a1a7b5; text-align: center;\">CloudGuard Vault: <a href=\"{app_url}\" style=\"color: #5c57d9;\">{app_url}</a><br />If you were not expecting this invitation, you can safely ignore this email.</p>"
        f"</div>"
    )
    response = resend.Emails.send(
        {
            "from": sender,
            "to": [email],
            "subject": f"You have been invited to CloudGuard as {article} {role_label}",
            "text": text_content,
            "html": html_content,
        }
    )
    provider_id = (
        response.get("id")
        if isinstance(response, dict)
        else getattr(response, "id", None)
    )
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise RuntimeError("Resend did not confirm acceptance of the invitation email.")
    return provider_id.strip()


def invitation_email_configuration() -> tuple[dict[str, str], list[str]]:
    file_values = dotenv_values(BASE_DIR / ".env")
    config = {}
    missing = []
    for name in ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "CLOUDGUARD_APP_URL"):
        value = os.getenv(name)
        if not value or not value.strip():
            value = file_values.get(name)
        if not isinstance(value, str) or not value.strip():
            missing.append(name)
        else:
            config[name] = value.strip()
    return config, missing


def sanitized_resend_error(error: ResendError, secrets_to_redact: list[str]) -> str:
    message = str(getattr(error, "message", "")) or "No provider message was supplied."
    for secret in secrets_to_redact:
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(
        r"(?i)authorization\s*[:=]\s*(?:Bearer\s+)?\S+",
        "Authorization: [redacted]",
        message,
    )
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)\b(?:re|rk|sk)_[A-Za-z0-9_-]+\b", "[redacted]", message)
    message = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[email redacted]",
        message,
        flags=re.IGNORECASE,
    )
    message = " ".join(message.split())[:300]
    error_type = re.sub(
        r"[^A-Za-z0-9_.-]", "", str(getattr(error, "error_type", "ResendError"))
    )[:80] or "ResendError"
    status_code = re.sub(r"[^0-9]", "", str(getattr(error, "code", "")))[:3]
    status_text = f"HTTP {status_code}" if status_code else "status unavailable"
    return f"Resend rejected the invitation ({status_text}, {error_type}): {message}"


@app.post("/api/invitations", status_code=201)
def create_invitation(
    payload: CreateInvitationPayload,
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    """Create a single-use invitation only when the email provider accepts it."""
    config, missing = invitation_email_configuration()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Invitation email configuration is missing: {', '.join(missing)}.",
        )
    invitation_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(invitation_token.encode("ascii")).hexdigest()
    now = utc_now()
    expires_at = now + timedelta(days=7)
    app_url = config["CLOUDGUARD_APP_URL"].rstrip("/")
    claim_url = f"{app_url}/#guest-invitation={invitation_token}"

    with db() as connection:
        target = invitation_target(connection, user["id"], payload.role, payload.target_id)
        email = target["email"].strip().lower()
        cursor = connection.execute(
            """INSERT INTO invitations(owner_id,email,role,target_id,token_hash,status,created_at,expires_at)
               VALUES(?,?,?,?,?,'pending',?,?)""",
            (
                user["id"],
                email,
                payload.role,
                payload.target_id,
                token_hash,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        invitation_id = cursor.lastrowid
        audit(connection, user["id"], f"invitation.created:{invitation_id}:{payload.role}")

    try:
        provider_id = send_invitation_email(email, payload.role, invitation_token)
    except ResendError as error:
        safe_detail = sanitized_resend_error(
            error,
            [invitation_token, email, config["RESEND_API_KEY"]],
        )
        with db() as connection:
            connection.execute(
                "UPDATE invitations SET status='failed' WHERE id=? AND status='pending'",
                (invitation_id,),
            )
            audit(connection, user["id"], f"invitation.email_failed:{invitation_id}:{payload.role}")
        logger.warning("Invitation %s email failed: %s", invitation_id, safe_detail)
        raise HTTPException(status_code=502, detail=safe_detail) from error
    except Exception as error:
        with db() as connection:
            connection.execute(
                "UPDATE invitations SET status='failed' WHERE id=? AND status='pending'",
                (invitation_id,),
            )
            audit(connection, user["id"], f"invitation.email_failed:{invitation_id}:{payload.role}")
        logger.exception("Invitation %s email delivery failed.", invitation_id)
        raise HTTPException(
            status_code=502,
            detail="The invitation email could not be sent. Check the API log and email configuration before retrying.",
        ) from error

    with db() as connection:
        revoke_target_invitations(
            connection,
            user["id"],
            payload.role,
            payload.target_id,
            exclude_invitation_id=invitation_id,
        )
        cursor = connection.execute(
            """UPDATE invitations SET status='sent',sent_at=?
               WHERE id=? AND status='pending' AND expires_at>?""",
            (utc_now().isoformat(), invitation_id, utc_now().isoformat()),
        )
        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=503,
                detail="The invitation could not be activated. Please try again.",
            )
        audit(
            connection,
            user["id"],
            f"invitation.sent:{invitation_id}:{payload.role}",
        )

    return {
        "invitation": {
            "id": invitation_id,
            "email": email,
            "role": payload.role,
            "status": "sent",
            "expires_at": expires_at.isoformat(),
            "claim_url": claim_url,
            "provider_id": provider_id,
        },
        "claim_url": claim_url,
        "message": (
            "Invitation email accepted by Resend for delivery. "
            "Note: Provider acceptance confirms the send request was received, "
            "but final delivery depends on the recipient's mail provider."
        ),
    }

@app.get("/api/invitations")
def list_invitations(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,email,role,target_id,status,created_at,expires_at,sent_at,claimed_at,revoked_at
               FROM invitations WHERE owner_id=? ORDER BY id DESC LIMIT 100""",
            (user["id"],),
        ).fetchall()
    invitations = []
    for row in rows:
        item = dict(row)
        if item["status"] == "sent" and _parse_iso_utc(item["expires_at"]) <= utc_now():
            item["status"] = "expired"
        invitations.append(item)
    return {"invitations": invitations}


@app.delete("/api/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: int, user: sqlite3.Row = Depends(current_user)) -> None:
    now = utc_now().isoformat()
    with db() as connection:
        invitation = connection.execute(
            "SELECT id,role,target_id FROM invitations WHERE id=? AND owner_id=?",
            (invitation_id, user["id"]),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=404, detail="Invitation was not found.")
        connection.execute(
            """UPDATE invitations SET status='revoked',revoked_at=?
               WHERE id=? AND status IN ('pending','sent','claimed')""",
            (now, invitation_id),
        )
        connection.execute(
            "UPDATE guest_sessions SET revoked_at=? WHERE invitation_id=? AND revoked_at IS NULL",
            (now, invitation_id),
        )
        if invitation["role"] == "recipient":
            connection.execute(
                "UPDATE recipient_sessions SET revoked_at=? WHERE recipient_id=? AND revoked_at IS NULL",
                (now, invitation["target_id"]),
            )
        elif invitation["role"] == "nominee":
            connection.execute(
                "UPDATE nominee_sessions SET revoked_at=? WHERE nominee_id=? AND revoked_at IS NULL",
                (now, invitation["target_id"]),
            )
        audit(connection, user["id"], f"invitation.revoked:{invitation_id}:{invitation['role']}")


@app.get("/api/guest/invitations/preview")
def preview_invitation(token: str) -> dict:
    if not token or len(token) < 43:
        raise HTTPException(status_code=400, detail="This invitation link is invalid.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    with db() as connection:
        invitation = connection.execute(
            """SELECT owner_id,email,role,target_id,expires_at,status,revoked_at
               FROM invitations WHERE token_hash=?""",
            (token_hash,),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=400, detail="This invitation link is invalid.")
        if invitation["revoked_at"] is not None or invitation["status"] == "revoked":
            raise HTTPException(status_code=400, detail="This invitation has been revoked by the vault owner.")
        if _parse_iso_utc(invitation["expires_at"]) <= now:
            raise HTTPException(status_code=400, detail="This invitation has expired. Contact the vault owner for a new invitation.")
        if invitation["status"] == "claimed":
            raise HTTPException(status_code=400, detail="This invitation has already been accepted. Sign in with your email and password.")
        if invitation["status"] != "sent":
            raise HTTPException(status_code=400, detail="This invitation is no longer active.")
        try:
            invitation_target(connection, invitation["owner_id"], invitation["role"], invitation["target_id"])
        except HTTPException as error:
            raise HTTPException(status_code=400, detail="This invitation is no longer valid.") from error
        owner = connection.execute("SELECT name FROM users WHERE id=?", (invitation["owner_id"],)).fetchone()
        existing_account = False
        if invitation["role"] == "recipient":
            acct = connection.execute(
                "SELECT id FROM recipient_accounts WHERE recipient_id=?", (invitation["target_id"],)
            ).fetchone()
            existing_account = acct is not None
        elif invitation["role"] == "nominee":
            acct = connection.execute(
                "SELECT id FROM nominee_accounts WHERE nominee_id=?", (invitation["target_id"],)
            ).fetchone()
            existing_account = acct is not None

    email = invitation["email"].strip().lower()
    local, _, domain = email.partition("@")
    hint = f"{local[:1]}***@{domain}" if domain else "***"
    return {
        "role": invitation["role"],
        "owner_name": owner["name"] if owner else "CloudGuard owner",
        "email_hint": hint,
        "requires_password": invitation["role"] in {"recipient", "nominee"},
        "existing_account": existing_account,
    }


@app.post("/api/guest/invitations/claim")
def claim_invitation(payload: ClaimInvitationPayload) -> dict:
    if not payload.token or len(payload.token) < 43:
        raise HTTPException(status_code=400, detail="This invitation link is invalid.")
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    now = utc_now()
    session_id = secrets.token_urlsafe(24)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        invitation = connection.execute(
            """SELECT id,owner_id,email,role,target_id,expires_at,status,revoked_at
               FROM invitations WHERE token_hash=?""",
            (token_hash,),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=400, detail="This invitation link is invalid.")
        if invitation["revoked_at"] is not None or invitation["status"] == "revoked":
            raise HTTPException(status_code=400, detail="This invitation has been revoked by the vault owner.")
        if _parse_iso_utc(invitation["expires_at"]) <= now:
            raise HTTPException(status_code=400, detail="This invitation has expired. Contact the vault owner for a new invitation.")
        if invitation["status"] == "claimed":
            raise HTTPException(status_code=400, detail="This invitation has already been accepted. Sign in with your email and password.")
        if invitation["status"] != "sent":
            raise HTTPException(status_code=400, detail="This invitation is no longer active.")
        try:
            target = invitation_target(
                connection,
                invitation["owner_id"],
                invitation["role"],
                invitation["target_id"],
            )
        except HTTPException as error:
            raise HTTPException(status_code=400, detail="This invitation is no longer valid.") from error
        email = invitation["email"].strip().lower()
        if target["email"].strip().lower() != email:
            raise HTTPException(status_code=400, detail="This invitation is no longer valid.")

        if payload.email is not None and payload.email.strip().lower() != email:
            raise HTTPException(
                status_code=403,
                detail="The invitation email address does not match the provided email.",
            )
        if payload.role is not None and payload.role.strip().lower() != invitation["role"]:
            raise HTTPException(
                status_code=403,
                detail="The invitation role does not match the provided role.",
            )

        if invitation["role"] in {"recipient", "nominee"} and not payload.password:
            raise HTTPException(
                status_code=422,
                detail=f"Create a password of at least 10 characters to finish setting up your {invitation['role']} account.",
            )

        session_expires_at = min(now + timedelta(hours=12), _parse_iso_utc(invitation["expires_at"]))
        updated = connection.execute(
            """UPDATE invitations SET status='claimed',claimed_at=?
               WHERE id=? AND status='sent' AND revoked_at IS NULL""",
            (now.isoformat(), invitation["id"]),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=400, detail="This invitation has already been accepted. Sign in with your email and password.")
        owner = connection.execute(
            "SELECT name FROM users WHERE id=?", (invitation["owner_id"],)
        ).fetchone()
        audit(connection, invitation["owner_id"], f"invitation.claimed:{invitation['id']}:{invitation['role']}")


        if invitation["role"] == "recipient":
            password_hash = _password_hash(payload.password)
            connection.execute(
                """INSERT INTO recipient_accounts(recipient_id,password_hash,created_at)
                   VALUES(?,?,?)
                   ON CONFLICT(recipient_id) DO UPDATE SET password_hash=excluded.password_hash""",
                (invitation["target_id"], password_hash, now.isoformat()),
            )
            connection.execute(
                "INSERT INTO recipient_sessions(id,recipient_id,expires_at) VALUES(?,?,?)",
                (session_id, invitation["target_id"], session_expires_at.isoformat()),
            )
            _log_recipient_event(
                connection,
                invitation["target_id"],
                invitation["owner_id"],
                "invitation_confirmed",
                None,
                "Email verified and recipient account created. Document access remains locked until the owner approves release.",
            )
            recipient_payload = {
                "email": email,
                "name": target["name"],
                "role": "recipient",
                "owner_name": owner["name"],
            }
        elif invitation["role"] == "nominee":
            password_hash = _password_hash(payload.password)
            connection.execute(
                """INSERT INTO nominee_accounts(nominee_id,password_hash,created_at)
                   VALUES(?,?,?)
                   ON CONFLICT(nominee_id) DO UPDATE SET password_hash=excluded.password_hash""",
                (invitation["target_id"], password_hash, now.isoformat()),
            )
            connection.execute(
                "INSERT INTO nominee_sessions(id,nominee_id,expires_at) VALUES(?,?,?)",
                (session_id, invitation["target_id"], session_expires_at.isoformat()),
            )
            connection.execute(
                """INSERT INTO nominee_case_events(nominee_id,owner_id,event_type,detail,created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    invitation["target_id"],
                    invitation["owner_id"],
                    "invitation_confirmed",
                    "Invitation verified and nominee account created.",
                    now.isoformat(),
                ),
            )
            nominee_payload = {
                "email": email,
                "name": target["name"],
                "role": "nominee",
                "owner_name": owner["name"],
            }
        else:
            connection.execute(
                "INSERT INTO guest_sessions(id,invitation_id,expires_at) VALUES(?,?,?)",
                (session_id, invitation["id"], session_expires_at.isoformat()),
            )
            recipient_payload = None

    if invitation["role"] == "recipient":
        access_token = jwt.encode(
            {
                "sub": email,
                "jti": session_id,
                "iat": now,
                "exp": session_expires_at,
                "principal_type": "recipient",
                "recipient_id": invitation["target_id"],
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "principal_type": "recipient",
            "recipient": recipient_payload,
        }

    if invitation["role"] == "nominee":
        access_token = jwt.encode(
            {
                "sub": email,
                "jti": session_id,
                "iat": now,
                "exp": session_expires_at,
                "principal_type": "nominee",
                "nominee_id": invitation["target_id"],
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "principal_type": "nominee",
            "nominee": nominee_payload,
        }

    access_token = jwt.encode(
        {
            "sub": email,
            "jti": session_id,
            "iat": now,
            "exp": session_expires_at,
            "principal_type": "guest",
            "invitation_id": invitation["id"],
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "principal_type": "guest",
        "guest": {
            "email": email,
            "role": invitation["role"],
            "owner_name": owner["name"],
        },
    }


@app.get("/api/guest/me")
def guest_me(guest: dict = Depends(guest_principal)) -> dict:
    return {"guest": guest}


@app.post("/api/guest/logout", status_code=204)
def guest_logout(guest: dict = Depends(guest_principal)) -> None:
    with db() as connection:
        connection.execute(
            "UPDATE guest_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utc_now().isoformat(), guest["session_id"]),
        )
        audit(connection, guest["owner_id"], f"invitation.guest_signed_out:{guest['invitation_id']}")


@app.post("/api/recipient/login")
def recipient_login(payload: LoginPayload) -> dict:
    email = payload.email.strip().lower()
    with db() as connection:
        rows = connection.execute(
            """SELECT recipient_accounts.password_hash, recipients.id, recipients.name,
                      recipients.email, users.name AS owner_name
               FROM recipient_accounts
               JOIN recipients ON recipients.id = recipient_accounts.recipient_id
               JOIN users ON users.id = recipients.owner_id
               WHERE recipients.email=?""",
            (email,),
        ).fetchall()
    matched = None
    for row in rows:
        if bcrypt.checkpw(payload.password.encode("utf-8"), row["password_hash"]):
            matched = row
            break
    if matched is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    access_token, _ = _issue_recipient_session(matched["id"], email)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "principal_type": "recipient",
        "recipient": {
            "email": email,
            "name": matched["name"],
            "role": "recipient",
            "owner_name": matched["owner_name"],
        },
    }


@app.get("/api/recipient/me")
def recipient_me(recipient: dict = Depends(recipient_principal)) -> dict:
    return {"recipient": recipient}


@app.post("/api/recipient/logout", status_code=204)
def recipient_logout(recipient: dict = Depends(recipient_principal)) -> None:
    with db() as connection:
        connection.execute(
            "UPDATE recipient_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utc_now().isoformat(), recipient["session_id"]),
        )
        audit(connection, recipient["owner_id"], f"recipient.signed_out:{recipient['recipient_id']}")


@app.post("/api/nominee/login")
def nominee_login(payload: LoginPayload) -> dict:
    email = payload.email.strip().lower()
    with db() as connection:
        rows = connection.execute(
            """SELECT nominee_accounts.password_hash,nominees.id,nominees.name,nominees.email,
                      users.name AS owner_name
               FROM nominee_accounts
               JOIN nominees ON nominees.id=nominee_accounts.nominee_id
               JOIN users ON users.id=nominees.owner_id
               WHERE nominees.email=?""",
            (email,),
        ).fetchall()
    matched = next(
        (row for row in rows if bcrypt.checkpw(payload.password.encode("utf-8"), row["password_hash"])),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    access_token, _ = _issue_nominee_session(matched["id"], email)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "principal_type": "nominee",
        "nominee": {
            "email": email,
            "name": matched["name"],
            "role": "nominee",
            "owner_name": matched["owner_name"],
        },
    }


@app.get("/api/nominee/me")
def nominee_me(nominee: dict = Depends(nominee_principal)) -> dict:
    return {"nominee": nominee}


@app.post("/api/nominee/logout", status_code=204)
def nominee_logout(nominee: dict = Depends(nominee_principal)) -> None:
    with db() as connection:
        connection.execute(
            "UPDATE nominee_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utc_now().isoformat(), nominee["session_id"]),
        )
        audit(connection, nominee["owner_id"], f"nominee.signed_out:{nominee['nominee_id']}")


def _nominee_case_summary(connection: sqlite3.Connection, nominee_id: int) -> Optional[dict]:
    row = connection.execute(
        """SELECT id,status,note,evidence_name,evidence_type,evidence_size,
                  reviewer_message,submitted_at,reviewed_at,updated_at
           FROM death_cases WHERE nominee_id=?""",
        (nominee_id,),
    ).fetchone()
    return dict(row) if row else None


@app.get("/api/nominee/dashboard")
def nominee_dashboard(nominee: dict = Depends(nominee_principal)) -> dict:
    with db() as connection:
        case = _nominee_case_summary(connection, nominee["nominee_id"])
        events = connection.execute(
            """SELECT id,event_type,detail,created_at
               FROM nominee_case_events WHERE nominee_id=? ORDER BY id DESC LIMIT 6""",
            (nominee["nominee_id"],),
        ).fetchall()
        hold = _medical_hold_state(connection, nominee["owner_id"])
    return {
        "nominee": {
            "name": nominee["name"],
            "email": nominee["email"],
            "owner_name": nominee["owner_name"],
            "role": "nominee",
        },
        "case": case,
        "case_status": case["status"] if case else "not_submitted",
        "medical_hold_status": hold["status"],
        "events": [dict(row) for row in events],
        "notice": (
            "A Medical Hold is active. New releases are paused."
            if hold["status"] != "inactive"
            else "Submitting evidence does not approve a case or grant document access."
        ),
    }


@app.get("/api/nominee/case")
def nominee_case(nominee: dict = Depends(nominee_principal)) -> dict:
    with db() as connection:
        case = _nominee_case_summary(connection, nominee["nominee_id"])
    return {"case": case}


@app.post("/api/nominee/case", status_code=201)
async def submit_nominee_case(
    note: str = Form(default="", max_length=2000),
    evidence: UploadFile = File(...),
    nominee: dict = Depends(nominee_principal),
) -> dict:
    filename = Path(evidence.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=415, detail="Upload a death certificate as a PDF, JPG, or PNG file.")
    data = await evidence.read(MAX_DEATH_EVIDENCE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The evidence file is empty.")
    if len(data) > MAX_DEATH_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="Evidence files must be 10 MB or smaller.")
    signatures = {
        ".pdf": data.startswith(b"%PDF-"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
    }
    if not signatures[suffix]:
        raise HTTPException(status_code=415, detail="The uploaded file does not match its PDF, JPG, or PNG type.")

    now = utc_now().isoformat()
    with db() as connection:
        existing = connection.execute(
            "SELECT id,status FROM death_cases WHERE nominee_id=?",
            (nominee["nominee_id"],),
        ).fetchone()
        if existing and existing["status"] != "more_information_required":
            raise HTTPException(
                status_code=409,
                detail="A submitted case cannot be replaced unless the reviewer requests more information.",
            )
        if existing:
            case_id = existing["id"]
            connection.execute(
                """UPDATE death_cases SET status='under_review',note=?,evidence_ciphertext=?,
                          evidence_name=?,evidence_type=?,evidence_size=?,reviewer_email=NULL,
                          reviewer_message='',reviewed_at=NULL,submitted_at=?,updated_at=?
                   WHERE id=?""",
                (
                    note.strip(),
                    encrypt_bytes(data),
                    filename,
                    evidence.content_type or "application/octet-stream",
                    len(data),
                    now,
                    now,
                    case_id,
                ),
            )
            event_type = "evidence_resubmitted"
            detail = "Updated evidence was submitted for review."
        else:
            cursor = connection.execute(
                """INSERT INTO death_cases(owner_id,nominee_id,status,note,evidence_ciphertext,
                          evidence_name,evidence_type,evidence_size,submitted_at,updated_at)
                   VALUES(?,?,'under_review',?,?,?,?,?,?,?)""",
                (
                    nominee["owner_id"],
                    nominee["nominee_id"],
                    note.strip(),
                    encrypt_bytes(data),
                    filename,
                    evidence.content_type or "application/octet-stream",
                    len(data),
                    now,
                    now,
                ),
            )
            case_id = cursor.lastrowid
            event_type = "evidence_submitted"
            detail = "Death notification and supporting evidence were submitted for review."
        connection.execute(
            """INSERT INTO nominee_case_events(nominee_id,owner_id,case_id,event_type,detail,created_at)
               VALUES(?,?,?,?,?,?)""",
            (nominee["nominee_id"], nominee["owner_id"], case_id, event_type, detail, now),
        )
        audit(connection, nominee["owner_id"], f"nominee.case_evidence_submitted:{case_id}")
        case = _nominee_case_summary(connection, nominee["nominee_id"])
    return {"case": case, "message": "Your case is submitted for review. This does not release documents."}


@app.post("/api/reviewer/login")
def reviewer_login(payload: LoginPayload) -> dict:
    email = payload.email.strip().lower()
    if not REVIEWER_EMAIL or not REVIEWER_PASSWORD_HASH:
        raise HTTPException(status_code=503, detail="Reviewer sign-in is not configured.")
    try:
        password_matches = bcrypt.checkpw(
            payload.password.encode("utf-8"),
            REVIEWER_PASSWORD_HASH.encode("ascii"),
        )
    except (ValueError, UnicodeEncodeError) as error:
        logger.exception("Configured reviewer password hash is invalid.")
        raise HTTPException(status_code=503, detail="Reviewer sign-in is not configured correctly.") from error
    if email != REVIEWER_EMAIL or not password_matches:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    access_token, _ = _issue_reviewer_session(email)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "principal_type": "reviewer",
        "reviewer": {"email": email, "role": "reviewer"},
    }


@app.get("/api/reviewer/me")
def reviewer_me(reviewer: dict = Depends(reviewer_principal)) -> dict:
    return {"reviewer": reviewer}


@app.post("/api/reviewer/logout", status_code=204)
def reviewer_logout(reviewer: dict = Depends(reviewer_principal)) -> None:
    with db() as connection:
        connection.execute(
            "UPDATE reviewer_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utc_now().isoformat(), reviewer["session_id"]),
        )
        audit(connection, None, "reviewer.signed_out")


@app.get("/api/reviewer/cases")
def reviewer_cases(reviewer: dict = Depends(reviewer_principal)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT death_cases.id,death_cases.status,death_cases.note,death_cases.evidence_name,
                      death_cases.evidence_type,death_cases.evidence_size,death_cases.reviewer_message,
                      death_cases.submitted_at,death_cases.reviewed_at,death_cases.updated_at,
                      nominees.name AS nominee_name,nominees.email AS nominee_email,
                      users.name AS owner_name,users.email AS owner_email
               FROM death_cases
               JOIN nominees ON nominees.id=death_cases.nominee_id
               JOIN users ON users.id=death_cases.owner_id
               ORDER BY CASE WHEN death_cases.status='under_review' THEN 0
                             WHEN death_cases.status='more_information_required' THEN 1
                             ELSE 2 END, death_cases.id DESC"""
        ).fetchall()
    return {"cases": [dict(row) for row in rows]}


@app.get("/api/reviewer/cases/{case_id}/evidence")
def reviewer_case_evidence(
    case_id: int, reviewer: dict = Depends(reviewer_principal)
) -> Response:
    with db() as connection:
        case = connection.execute(
            """SELECT death_cases.owner_id,death_cases.nominee_id,death_cases.evidence_ciphertext,
                      death_cases.evidence_name,death_cases.evidence_type,users.email AS owner_email,
                      nominees.email AS nominee_email
               FROM death_cases
               JOIN users ON users.id=death_cases.owner_id
               JOIN nominees ON nominees.id=death_cases.nominee_id
               WHERE death_cases.id=?""",
            (case_id,),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Case was not found.")
        if reviewer["email"] in {
            case["owner_email"].strip().lower(),
            case["nominee_email"].strip().lower(),
        }:
            raise HTTPException(status_code=403, detail="A case participant cannot review this evidence.")
        try:
            evidence = decrypt_bytes(case["evidence_ciphertext"])
        except Exception as error:
            logger.exception("Death-case evidence could not be decrypted.")
            raise HTTPException(status_code=503, detail="The evidence could not be opened.") from error
        audit(connection, case["owner_id"], f"nominee.case_evidence_viewed:{case_id}:{reviewer['email']}")
    filename = Path(case["evidence_name"]).name.replace('"', "")
    return Response(
        content=evidence,
        media_type=case["evidence_type"],
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.post("/api/reviewer/cases/{case_id}/decision")
def decide_nominee_case(
    case_id: int,
    payload: DeathCaseReviewPayload,
    reviewer: dict = Depends(reviewer_principal),
) -> dict:
    now = utc_now().isoformat()
    with db() as connection:
        case = connection.execute(
            """SELECT death_cases.owner_id,death_cases.nominee_id,death_cases.status,
                      users.email AS owner_email,nominees.email AS nominee_email
               FROM death_cases
               JOIN users ON users.id=death_cases.owner_id
               JOIN nominees ON nominees.id=death_cases.nominee_id
               WHERE death_cases.id=?""",
            (case_id,),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Case was not found.")
        if reviewer["email"] in {
            case["owner_email"].strip().lower(),
            case["nominee_email"].strip().lower(),
        }:
            raise HTTPException(status_code=403, detail="A case participant cannot review their own case.")
        if case["status"] != "under_review":
            raise HTTPException(status_code=409, detail="Only cases under review can receive a decision.")
        connection.execute(
            """UPDATE death_cases SET status=?,reviewer_email=?,reviewer_message=?,
                      reviewed_at=?,updated_at=? WHERE id=?""",
            (payload.decision, reviewer["email"], payload.message.strip(), now, now, case_id),
        )
        detail = payload.message.strip()
        connection.execute(
            """INSERT INTO nominee_case_events(nominee_id,owner_id,case_id,event_type,detail,created_at)
               VALUES(?,?,?,?,?,?)""",
            (
                case["nominee_id"],
                case["owner_id"],
                case_id,
                f"case_{payload.decision}",
                detail,
                now,
            ),
        )
        audit(connection, case["owner_id"], f"nominee.case_reviewed:{case_id}:{payload.decision}:{reviewer['email']}")
        updated_case = _nominee_case_summary(connection, case["nominee_id"])
    return {"case": updated_case, "message": "Case decision recorded. Document access has not been released."}


@app.get("/api/nominee/history")
def nominee_history(nominee: dict = Depends(nominee_principal)) -> dict:
    with db() as connection:
        events = connection.execute(
            """SELECT id,event_type,detail,created_at
               FROM nominee_case_events WHERE nominee_id=? ORDER BY id DESC LIMIT 100""",
            (nominee["nominee_id"],),
        ).fetchall()
    return {"events": [dict(row) for row in events]}


@app.get("/api/recipient/dashboard")
def recipient_dashboard(recipient: dict = Depends(recipient_principal)) -> dict:
    with db() as connection:
        documents = _recipient_documents(connection, recipient["recipient_id"])
        hold = _medical_hold_state(connection, recipient["owner_id"])
        events = connection.execute(
            """SELECT id,event_type,document_id,detail,created_at
               FROM recipient_events WHERE recipient_id=? ORDER BY id DESC LIMIT 8""",
            (recipient["recipient_id"],),
        ).fetchall()
    available = sum(1 for item in documents if item["access_status"] == "available")
    pending = sum(1 for item in documents if item["access_status"] == "pending")
    revoked = sum(1 for item in documents if item["access_status"] == "revoked")
    if hold["status"] != "inactive":
        availability = (
            "A Medical Hold is active or awaiting a fresh successful owner check-in. "
            "Document access is paused."
        )
    elif available:
        availability = (
            f"{available} assigned document{'s' if available != 1 else ''} available to open or download."
        )
    elif pending:
        availability = (
            "Assigned documents are not yet available. Access stays locked until the owner "
            "completes and approves release."
        )
    else:
        availability = (
            "No documents are currently available to you. An invitation does not grant document access."
        )
    return {
        "recipient": {
            "email": recipient["email"],
            "name": recipient["name"],
            "owner_name": recipient["owner_name"],
            "role": "recipient",
        },
        "availability": availability,
        "medical_hold_status": hold["status"],
        "counts": {"available": available, "pending": pending, "revoked": revoked, "assigned": len(documents)},
        "events": [dict(row) for row in events],
    }


@app.get("/api/recipient/documents")
def recipient_list_documents(recipient: dict = Depends(recipient_principal)) -> dict:
    with db() as connection:
        documents = _recipient_documents(connection, recipient["recipient_id"])
    return {"documents": documents}


@app.get("/api/recipient/documents/{document_id}")
def recipient_document_details(
    document_id: int, recipient: dict = Depends(recipient_principal)
) -> dict:
    with db() as connection:
        documents = _recipient_documents(connection, recipient["recipient_id"])
    document = next((item for item in documents if item["id"] == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Document was not found.")
    return {"document": document}


@app.get("/api/recipient/documents/{document_id}/download")
def recipient_download_document(
    document_id: int, recipient: dict = Depends(recipient_principal)
) -> Response:
    with db() as connection:
        share = connection.execute(
            """SELECT documents.id, documents.file_name, documents.content_type, documents.encrypted_data,
                      document_releases.status AS release_status,
                      COALESCE(medical_holds.status,'inactive') AS hold_status,
                      EXISTS(SELECT 1 FROM death_cases
                             WHERE death_cases.owner_id=documents.owner_id
                               AND death_cases.status='approved') AS case_approved
               FROM document_shares
               JOIN documents ON documents.id = document_shares.document_id
               LEFT JOIN document_releases
                 ON document_releases.document_id = documents.id
                AND document_releases.recipient_id = document_shares.recipient_id
               LEFT JOIN medical_holds ON medical_holds.owner_id=documents.owner_id
               WHERE document_shares.recipient_id=? AND documents.id=?""",
            (recipient["recipient_id"], document_id),
        ).fetchone()
        if share is None:
            raise HTTPException(status_code=404, detail="Document was not found.")
        if share["hold_status"] != "inactive":
            raise HTTPException(
                status_code=403,
                detail="Document access is paused by the owner's Medical Hold.",
            )
        if not share["case_approved"]:
            raise HTTPException(
                status_code=403,
                detail="No death-notification case has been approved. Document access remains locked.",
            )
        if share["release_status"] != "available":
            raise HTTPException(
                status_code=403,
                detail="This document is not available. Access stays locked until the owner approves release.",
            )
        try:
            plaintext = decrypt_bytes(share["encrypted_data"])
        except Exception as error:
            raise HTTPException(status_code=503, detail="The document could not be decrypted.") from error
        _log_recipient_event(
            connection,
            recipient["recipient_id"],
            recipient["owner_id"],
            "document_downloaded",
            document_id,
            "The recipient downloaded an approved document.",
        )
        audit(connection, recipient["owner_id"], f"recipient.document_downloaded:{document_id}:{recipient['recipient_id']}")
    filename = Path(share["file_name"] or "document").name.replace('"', "")
    return Response(
        content=plaintext,
        media_type=share["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/api/recipient/history")
def recipient_history(recipient: dict = Depends(recipient_principal)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,event_type,document_id,detail,created_at
               FROM recipient_events WHERE recipient_id=? ORDER BY id DESC LIMIT 100""",
            (recipient["recipient_id"],),
        ).fetchall()
    return {"events": [dict(row) for row in rows]}


def validate_and_encrypt_face_enrollment(samples: list[str]) -> bytes:
    decoded_samples = []
    for encoded_sample in samples:
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
    return encrypt_bytes(json.dumps(decoded_samples, separators=(",", ":")).encode("ascii"))


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterPayload) -> dict:
    face_blob = validate_and_encrypt_face_enrollment(payload.face_enrollment_samples_b64)
    email = payload.email.strip().lower()

    if payload.supabase_uid:
        pwd_hash = b"*SUPABASE_AUTH*"
    else:
        if not payload.password or len(payload.password) < 10:
            raise HTTPException(status_code=422, detail="Password must be at least 10 characters.")
        if len(payload.password.encode("utf-8")) > 72:
            raise HTTPException(status_code=422, detail="Password must be 72 bytes or fewer.")
        pwd_hash = bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt())

    try:
        with db() as connection:
            cursor = connection.execute(
                """INSERT INTO users(name,email,phone,password_hash,face_enrollment,created_at,supabase_uid)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    payload.name.strip(),
                    email,
                    payload.phone.strip(),
                    pwd_hash,
                    face_blob,
                    utc_now().isoformat(),
                    payload.supabase_uid.strip() if payload.supabase_uid else None,
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
    supabase_payload = verify_supabase_jwt(credentials.credentials)
    if supabase_payload:
        token_id = str(supabase_payload.get("jti", supabase_payload.get("sub", "")))
        exp = supabase_payload.get("exp")
        exp_iso = (
            datetime.fromtimestamp(exp, timezone.utc).isoformat()
            if exp
            else (utc_now() + timedelta(days=1)).isoformat()
        )
        with db() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO revoked_tokens(token_id, user_id, revoked_at, expires_at) VALUES(?,?,?,?)",
                (token_id, user["id"], utc_now().isoformat(), exp_iso),
            )
            audit(connection, user["id"], "account.signed_out")
        return

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


@app.get("/api/auth/status")
def auth_status(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    if not credentials:
        return {"authenticated": False, "error": "No credentials provided."}
    raw_token = credentials.credentials
    if not SUPABASE_JWT_SECRET:
        return {
            "authenticated": False,
            "has_vault_profile": False,
            "error": "SUPABASE_JWT_SECRET is not configured in backend/.env. Please configure the JWT Secret from your Supabase Dashboard.",
        }
    try:
        supabase_payload = verify_supabase_jwt(raw_token)
    except HTTPException as exc:
        return {
            "authenticated": False,
            "has_vault_profile": False,
            "error": f"Supabase token verification failed: {exc.detail}",
        }
    except Exception as exc:
        return {
            "authenticated": False,
            "has_vault_profile": False,
            "error": f"Token verification error: {str(exc)}",
        }

    if supabase_payload:
        email = str(supabase_payload.get("email", "")).strip().lower()
        sub = str(supabase_payload.get("sub", "")).strip()
        with db() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE (supabase_uid = ? AND supabase_uid IS NOT NULL) OR LOWER(email) = ?",
                (sub, email),
            ).fetchone()
            if user and not user["supabase_uid"]:
                connection.execute("UPDATE users SET supabase_uid = ? WHERE id = ?", (sub, user["id"]))
                user = connection.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

        return {
            "authenticated": True,
            "provider": "supabase",
            "email": email,
            "supabase_uid": sub,
            "has_vault_profile": user is not None,
            "user": public_user(user) if user else None,
        }
    try:
        user = current_user(credentials)
        return {
            "authenticated": True,
            "provider": "local",
            "has_vault_profile": True,
            "user": public_user(user),
        }
    except Exception:
        return {"authenticated": False, "error": "Invalid or expired token."}


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
        hold = _medical_hold_state(connection, user["id"])
        case = connection.execute(
            """SELECT id,status,reviewer_message,submitted_at,reviewed_at
               FROM death_cases WHERE owner_id=?""",
            (user["id"],),
        ).fetchone()
    next_due = None
    if last and last["status"] == "success":
        next_due = (
            _parse_iso_utc(last["created_at"]) + timedelta(days=user["checkin_interval"])
        ).isoformat()
    return {
        "user": public_user(user),
        "account_status": "Active",
        "last_checkin": dict(last) if last else None,
        "next_checkin": next_due,
        "documents_count": docs,
        "recipients_count": recipients,
        "medical_hold": hold,
        "nominee_case": dict(case) if case else None,
    }


@app.get("/api/documents")
def list_documents(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE owner_id=? ORDER BY id DESC""",
            (user["id"],),
        ).fetchall()
        shares = connection.execute(
            """SELECT document_shares.document_id, document_shares.recipient_id
               FROM document_shares
               JOIN documents ON document_shares.document_id = documents.id
               WHERE documents.owner_id=?""",
            (user["id"],),
        ).fetchall()
        releases = connection.execute(
            """SELECT document_releases.document_id, document_releases.recipient_id, document_releases.status
               FROM document_releases
               JOIN documents ON document_releases.document_id = documents.id
               WHERE documents.owner_id=?""",
            (user["id"],),
        ).fetchall()
        hold_status = _medical_hold_state(connection, user["id"])["status"]
        case_approved = connection.execute(
            "SELECT 1 FROM death_cases WHERE owner_id=? AND status='approved'",
            (user["id"],),
        ).fetchone() is not None

    shares_by_doc = {}
    for row in shares:
        shares_by_doc.setdefault(row["document_id"], []).append(row["recipient_id"])
    release_by_doc = {}
    for row in releases:
        release_by_doc.setdefault(row["document_id"], {})[row["recipient_id"]] = row["status"]

    docs = []
    for row in rows:
        d = dict(row)
        recipient_ids = shares_by_doc.get(row["id"], [])
        d["recipient_ids"] = recipient_ids
        d["assignments"] = [
            {
                "recipient_id": recipient_id,
                "access_status": (
                    "revoked"
                    if release_by_doc.get(row["id"], {}).get(recipient_id) == "revoked"
                    else "on_hold"
                    if release_by_doc.get(row["id"], {}).get(recipient_id) == "available"
                    and hold_status != "inactive"
                    else "available"
                    if release_by_doc.get(row["id"], {}).get(recipient_id) == "available"
                    and case_approved
                    else "pending"
                ),
                "release_hold": hold_status != "inactive",
                "case_approved": case_approved,
            }
            for recipient_id in recipient_ids
        ]
        docs.append(d)

    return {"documents": docs}


@app.post("/api/documents", status_code=201)
async def upload_document(
    name: str = Form(..., min_length=2, max_length=120),
    category: str = Form(..., min_length=2, max_length=40),
    recipient_ids_json: str = Form(default="[]"),
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
    try:
        recipient_ids = json.loads(recipient_ids_json)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422, detail="Recipient assignments must be a JSON array of IDs."
        ) from error
    if not isinstance(recipient_ids, list) or any(
        type(recipient_id) is not int or recipient_id < 1
        for recipient_id in recipient_ids
    ):
        raise HTTPException(
            status_code=422, detail="Recipient assignments must be a JSON array of positive IDs."
        )
    recipient_ids = list(dict.fromkeys(recipient_ids))
    content_type = file.content_type or "application/octet-stream"
    with db() as connection:
        _validate_recipient_ownership(connection, user["id"], recipient_ids)
        cursor = connection.execute(
            """INSERT INTO documents(owner_id,name,category,file_name,content_type,file_size,encrypted_data,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], name.strip(), category, Path(file.filename or "document").name,
             content_type, len(data), encrypt_bytes(data), utc_now().isoformat()),
        )
        document_id = cursor.lastrowid
        if recipient_ids:
            _replace_document_shares(connection, user["id"], document_id, recipient_ids)
        audit(connection, user["id"], f"document.uploaded:{cursor.lastrowid}")
        if recipient_ids:
            audit(connection, user["id"], f"document.shares_updated:{document_id}")
        row = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE id=? AND owner_id=?""",
            (document_id, user["id"]),
        ).fetchone()
    return {"document": dict(row)}



class UpdateSharesPayload(BaseModel):
    recipient_ids: list[StrictInt]


def _validate_recipient_ownership(
    connection: sqlite3.Connection, owner_id: int, recipient_ids: list[int]
) -> None:
    if not recipient_ids:
        return
    placeholders = ",".join("?" for _ in recipient_ids)
    rows = connection.execute(
        f"SELECT id FROM recipients WHERE owner_id=? AND id IN ({placeholders})",
        (owner_id, *recipient_ids),
    ).fetchall()
    if {row["id"] for row in rows} != set(recipient_ids):
        raise HTTPException(status_code=400, detail="Invalid recipient selected.")

@app.put("/api/documents/{document_id}/shares")
def update_document_shares(
    document_id: int, payload: UpdateSharesPayload, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with db() as connection:
        doc = connection.execute("SELECT id FROM documents WHERE id=? AND owner_id=?", (document_id, user["id"])).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        recipient_ids = list(dict.fromkeys(payload.recipient_ids))
        _validate_recipient_ownership(connection, user["id"], recipient_ids)
        _replace_document_shares(connection, user["id"], document_id, recipient_ids)
        audit(connection, user["id"], f"document.shares_updated:{document_id}")

    return {"message": "Document assignments updated."}


@app.post("/api/documents/{document_id}/releases", status_code=200)
def approve_document_release(
    document_id: int, payload: ReleasePayload, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with db() as connection:
        document = connection.execute(
            "SELECT id FROM documents WHERE id=? AND owner_id=?",
            (document_id, user["id"]),
        ).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="Document was not found.")
        _validate_recipient_ownership(connection, user["id"], [payload.recipient_id])
        share = connection.execute(
            "SELECT recipient_id FROM document_shares WHERE document_id=? AND recipient_id=?",
            (document_id, payload.recipient_id),
        ).fetchone()
        if share is None:
            raise HTTPException(
                status_code=400,
                detail="Release can only be approved for a recipient currently assigned to this document.",
            )
        hold = _medical_hold_state(connection, user["id"])
        if hold["status"] != "inactive":
            raise HTTPException(
                status_code=409,
                detail="Document release is paused by the Medical Hold. End the hold and complete a fresh successful live check-in first.",
            )
        approved_case = connection.execute(
            "SELECT id FROM death_cases WHERE owner_id=? AND status='approved'",
            (user["id"],),
        ).fetchone()
        if approved_case is None:
            raise HTTPException(
                status_code=409,
                detail="A nominee case must be approved by an authorized reviewer before document release can be considered.",
            )
        _set_document_release(connection, document_id, payload.recipient_id, "available")
        _log_recipient_event(
            connection,
            payload.recipient_id,
            user["id"],
            "release_approved",
            document_id,
            "The owner approved release of this document.",
        )
        audit(connection, user["id"], f"document.release_approved:{document_id}:{payload.recipient_id}")
    return {"message": "Release approved. The assigned recipient can now open this document.", "access_status": "available"}


@app.delete("/api/documents/{document_id}/releases/{recipient_id}", status_code=200)
def revoke_document_release(
    document_id: int, recipient_id: int, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with db() as connection:
        document = connection.execute(
            "SELECT id FROM documents WHERE id=? AND owner_id=?",
            (document_id, user["id"]),
        ).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="Document was not found.")
        _validate_recipient_ownership(connection, user["id"], [recipient_id])
        _set_document_release(connection, document_id, recipient_id, "revoked")
        _log_recipient_event(
            connection,
            recipient_id,
            user["id"],
            "access_revoked",
            document_id,
            "The owner revoked access to this document.",
        )
        audit(connection, user["id"], f"document.release_revoked:{document_id}:{recipient_id}")
    return {"message": "Access revoked. The recipient can no longer open this document.", "access_status": "revoked"}


@app.get("/api/medical-hold")
def get_medical_hold(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        hold = _medical_hold_state(connection, user["id"])
    return {"medical_hold": hold}


@app.put("/api/medical-hold")
def update_medical_hold(
    payload: MedicalHoldPayload, user: sqlite3.Row = Depends(current_user)
) -> dict:
    now = utc_now().isoformat()
    with db() as connection:
        hold = _medical_hold_state(connection, user["id"])
        if payload.action == "start":
            connection.execute(
                """INSERT INTO medical_holds(owner_id,status,started_at,ended_at,updated_at)
                   VALUES(?,'active',?,NULL,?)
                   ON CONFLICT(owner_id) DO UPDATE SET status='active',started_at=excluded.started_at,
                     ended_at=NULL,updated_at=excluded.updated_at""",
                (user["id"], now, now),
            )
            audit(connection, user["id"], "medical_hold.started")
        else:
            if hold["status"] != "active":
                raise HTTPException(
                    status_code=409,
                    detail="Only an active Medical Hold can be ended.",
                )
            connection.execute(
                """UPDATE medical_holds SET status='awaiting_checkin',ended_at=?,updated_at=?
                   WHERE owner_id=?""",
                (now, now, user["id"]),
            )
            audit(connection, user["id"], "medical_hold.ended_awaiting_checkin")
        updated = _medical_hold_state(connection, user["id"])
    return {"medical_hold": updated}


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
        revoke_target_invitations(connection, user["id"], "recipient", recipient_id)
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
        revoke_target_invitations(connection, user["id"], "recipient", recipient_id)
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
        if existing:
            revoke_target_invitations(connection, user["id"], "nominee", existing["id"])
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
        existing = connection.execute(
            "SELECT id FROM nominees WHERE owner_id=?", (user["id"],)
        ).fetchone()
        if existing:
            revoke_target_invitations(connection, user["id"], "nominee", existing["id"])
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
    except Exception as error:
        logger.warning("Failed to decrypt or parse face enrollment for user %s: %s", user["id"], error)
        raise HTTPException(
            status_code=409,
            detail="Face enrollment data could not be decrypted. Re-enroll your face before checking in.",
        ) from error
    checkin_started_at = utc_now()
    with tempfile.TemporaryDirectory(prefix="cloudguard-checkin-") as directory:
        video_path = Path(directory) / f"recording{suffix}"
        video_path.write_bytes(data)
        try:
            statuses = verify_recording(video_path, enrollment_samples, sentence)
            required = {"face_status", "liveness_status", "speech_status"}
            if not isinstance(statuses, dict) or not required.issubset(statuses):
                raise VerificationUnavailable("Local verification produced an incomplete result.")
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
    face_detail = statuses.get("face_detail") or ("matched" if statuses.get("face_status") == "passed" else "different_person")
    if not passed and statuses.get("face_status") != "passed":
        if face_detail == "different_person":
            message = "Face verification failed: this person does not match the enrolled face."
        elif face_detail == "no_face":
            message = "Face verification failed: no usable face was found in the check-in recording."
        else:
            message = "Face verification did not pass."
    elif passed:
        message = "All verification checks passed."
    else:
        message = "One or more verification checks did not pass."
    with db() as connection:
        cursor = connection.execute(
            """INSERT INTO checkins(owner_id,mode,status,face_status,liveness_status,speech_status,sentence,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], "local_ai", checkin_status, statuses["face_status"], statuses["liveness_status"],
             statuses["speech_status"], sentence, utc_now().isoformat()),
        )
        audit(connection, user["id"], f"{event}:{cursor.lastrowid}")
        hold = _medical_hold_state(connection, user["id"])
        if (
            passed
            and hold["status"] == "awaiting_checkin"
            and hold["ended_at"]
            and checkin_started_at >= _parse_iso_utc(hold["ended_at"])
        ):
            connection.execute(
                "UPDATE medical_holds SET status='inactive',updated_at=? WHERE owner_id=?",
                (utc_now().isoformat(), user["id"]),
            )
            audit(connection, user["id"], "medical_hold.fresh_checkin_completed")
        row = connection.execute("SELECT * FROM checkins WHERE id=?", (cursor.lastrowid,)).fetchone()
    checkin = dict(row)
    checkin["face_detail"] = face_detail
    return {
        "checkin": checkin,
        "passed": passed,
        "message": message,
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


@app.put("/api/profile/face-enrollment")
def update_face_enrollment(
    payload: FaceEnrollmentPayload,
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    face_blob = validate_and_encrypt_face_enrollment(payload.face_enrollment_samples_b64)
    with db() as connection:
        connection.execute(
            "UPDATE users SET face_enrollment=? WHERE id=?",
            (face_blob, user["id"]),
        )
        audit(connection, user["id"], "profile.face_enrollment_updated")
    return {"message": "Face enrollment updated successfully."}

