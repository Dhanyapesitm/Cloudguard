import base64
from datetime import datetime, timedelta, timezone
import secrets
import jwt
import pytest
from fastapi.testclient import TestClient

import main

TEST_SUPABASE_SECRET = "super-secret-supabase-jwt-signing-key-32chars"
TEST_SUPABASE_URL = "https://test-project.supabase.co"


def create_supabase_token(
    sub: str,
    email: str,
    secret: str = TEST_SUPABASE_SECRET,
    aud: str = "authenticated",
    exp_seconds: int = 3600,
    jti: str = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": f"{TEST_SUPABASE_URL}/auth/v1",
        "sub": sub,
        "aud": aud,
        "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
        "iat": int(now.timestamp()),
        "email": email,
        "role": "authenticated",
        "jti": jti or secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def fake_face_samples() -> list[str]:
    return [
        base64.b64encode(b"\xff\xd8\xffsample-" + bytes([i])).decode()
        for i in range(5)
    ]


@pytest.fixture(autouse=True)
def setup_supabase_env(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SUPABASE_JWT_SECRET", TEST_SUPABASE_SECRET)
    monkeypatch.setattr(main, "SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test_supabase.db")
    main.init_database()


def test_supabase_token_verification_success_and_failures():
    user_id = "user-uuid-12345"
    email = "test@example.com"
    token = create_supabase_token(user_id, email)

    # 1. Valid token
    payload = main.verify_supabase_jwt(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["email"] == email

    # 2. Token signed with wrong secret
    bad_token = create_supabase_token(user_id, email, secret="wrong-secret-key-that-does-not-match")
    with pytest.raises(main.HTTPException) as exc:
        main.verify_supabase_jwt(bad_token)
    assert exc.value.status_code == 401

    # 3. Expired token (outside leeway)
    expired_token = create_supabase_token(user_id, email, exp_seconds=-300)
    with pytest.raises(main.HTTPException) as exc:
        main.verify_supabase_jwt(expired_token)
    assert exc.value.status_code == 401

    # 4. Wrong audience
    wrong_aud_token = create_supabase_token(user_id, email, aud="public")
    with pytest.raises(main.HTTPException) as exc:
        main.verify_supabase_jwt(wrong_aud_token)
    assert exc.value.status_code == 401


def test_supabase_owner_registration_and_profile_access():
    with TestClient(main.app) as client:
        sub = "owner-uuid-001"
        email = "owner@supabase-example.com"
        token = create_supabase_token(sub, email)
        headers = {"Authorization": f"Bearer {token}"}

        # Status before registration: authenticated with Supabase, but no vault profile
        status_resp = client.get("/api/auth/status", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["authenticated"] is True
        assert status_resp.json()["provider"] == "supabase"
        assert status_resp.json()["has_vault_profile"] is False

        # Attempting /api/auth/me before registering vault returns 401
        me_resp = client.get("/api/auth/me", headers=headers)
        assert me_resp.status_code == 401
        assert "No CloudGuard vault profile found" in me_resp.json()["detail"]

        # Register vault profile with Supabase UID and 5 face enrollment samples (no local password needed)
        reg_resp = client.post(
            "/api/auth/register",
            json={
                "name": "Supabase Vault Owner",
                "email": email,
                "phone": "+1 555 0199",
                "face_enrollment_samples_b64": fake_face_samples(),
                "supabase_uid": sub,
            },
        )
        assert reg_resp.status_code == 201
        user_data = reg_resp.json()["user"]
        assert user_data["email"] == email
        assert user_data["name"] == "Supabase Vault Owner"
        assert user_data["supabase_uid"] == sub

        # Access /api/auth/me with Supabase token
        me_resp = client.get("/api/auth/me", headers=headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["user"]["email"] == email
        assert me_resp.json()["user"]["name"] == "Supabase Vault Owner"

        # Access /api/dashboard with Supabase token
        dash_resp = client.get("/api/dashboard", headers=headers)
        assert dash_resp.status_code == 200
        assert dash_resp.json()["account_status"] == "Active"


def test_supabase_owner_logout_revokes_token():
    with TestClient(main.app) as client:
        sub = "owner-uuid-logout"
        email = "logout@supabase-example.com"
        jti = "unique-session-jti-123"
        token = create_supabase_token(sub, email, jti=jti)
        headers = {"Authorization": f"Bearer {token}"}

        client.post(
            "/api/auth/register",
            json={
                "name": "Logout Tester",
                "email": email,
                "phone": "+1 555 0188",
                "face_enrollment_samples_b64": fake_face_samples(),
                "supabase_uid": sub,
            },
        )

        # Before logout: works
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        # Logout
        logout_resp = client.post("/api/auth/logout", headers=headers)
        assert logout_resp.status_code == 204

        # After logout: token is revoked
        after_resp = client.get("/api/auth/me", headers=headers)
        assert after_resp.status_code == 401
        assert "no longer active" in after_resp.json()["detail"].lower()


def test_supabase_role_boundaries_and_tenant_isolation():
    with TestClient(main.app) as client:
        # 1. Owner registers and uploads a document
        owner_sub = "owner-alpha"
        owner_email = "owner.alpha@example.com"
        owner_token = create_supabase_token(owner_sub, owner_email)
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        client.post(
            "/api/auth/register",
            json={
                "name": "Owner Alpha",
                "email": owner_email,
                "phone": "+1 555 0101",
                "face_enrollment_samples_b64": fake_face_samples(),
                "supabase_uid": owner_sub,
            },
        )

        # Owner adds a recipient
        recip_contact = client.post(
            "/api/recipients",
            headers=owner_headers,
            json={"name": "Recipient Bob", "email": "bob@recipient-example.com", "relationship": "Brother"},
        )
        assert recip_contact.status_code == 201

        # 2. Another owner (cross-tenant)
        other_sub = "owner-beta"
        other_email = "owner.beta@example.com"
        other_token = create_supabase_token(other_sub, other_email)
        other_headers = {"Authorization": f"Bearer {other_token}"}

        client.post(
            "/api/auth/register",
            json={
                "name": "Owner Beta",
                "email": other_email,
                "phone": "+1 555 0102",
                "face_enrollment_samples_b64": fake_face_samples(),
                "supabase_uid": other_sub,
            },
        )

        # Owner Beta sees zero recipients from Owner Alpha
        beta_recips = client.get("/api/recipients", headers=other_headers)
        assert beta_recips.status_code == 200
        assert len(beta_recips.json()["recipients"]) == 0

        # 3. Recipient identity with Supabase token
        recipient_sub = "recipient-bob-uuid"
        recipient_token = create_supabase_token(recipient_sub, "bob@recipient-example.com")
        recip_headers = {"Authorization": f"Bearer {recipient_token}"}

        # Recipient accessing recipient dashboard -> success
        recip_dash = client.get("/api/recipient/dashboard", headers=recip_headers)
        assert recip_dash.status_code == 200
        assert recip_dash.json()["recipient"]["email"] == "bob@recipient-example.com"

        # Recipient attempting to access owner vault routes -> rejected with 401 (not an owner)
        owner_route_resp = client.get("/api/documents", headers=recip_headers)
        assert owner_route_resp.status_code == 401

        # 4. Unauthorized user attempting to access recipient portal -> rejected with 403
        random_sub = "random-uuid"
        random_token = create_supabase_token(random_sub, "stranger@random-example.com")
        random_headers = {"Authorization": f"Bearer {random_token}"}

        unauthorized_recip = client.get("/api/recipient/dashboard", headers=random_headers)
        assert unauthorized_recip.status_code == 403

        # 5. Unauthorized user attempting to access reviewer portal -> rejected with 403
        unauthorized_reviewer = client.get("/api/reviewer/cases", headers=random_headers)
        assert unauthorized_reviewer.status_code == 403


def test_supabase_unconfigured_secret_does_not_accept_supabase_tokens(monkeypatch, tmp_path):
    """
    When SUPABASE_JWT_SECRET is not configured (empty string), verify_supabase_jwt
    must return None (not accept the token as valid). The /api/auth/status endpoint
    must then report authenticated=False — it must NOT claim provider='supabase'.
    """
    monkeypatch.setattr(main, "SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test_unconfigured.db")
    main.init_database()

    token = create_supabase_token("user-001", "user@example.com")

    # verify_supabase_jwt must return None when secret is not configured —
    # the token is neither verified nor silently accepted
    result = main.verify_supabase_jwt(token)
    assert result is None, (
        "verify_supabase_jwt must return None (not accept token) when SUPABASE_JWT_SECRET is empty"
    )

    # /api/auth/status must report unauthenticated — not grant supabase access
    with TestClient(main.app) as client:
        resp = client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("provider") != "supabase", (
            "Backend must not claim Supabase provider when SUPABASE_JWT_SECRET is empty"
        )
        assert body.get("authenticated") is False, (
            "Backend must report authenticated=False when SUPABASE_JWT_SECRET is empty"
        )


def test_supabase_cross_project_token_is_rejected():
    """
    A Supabase token signed with a different project's secret must be rejected.
    This confirms the cryptographic signature check is active.
    """
    different_project_secret = "completely-different-supabase-secret-key-xyz"
    token_from_other_project = create_supabase_token(
        "intruder-123", "intruder@other-project.com",
        secret=different_project_secret,
    )

    with pytest.raises(main.HTTPException) as exc:
        main.verify_supabase_jwt(token_from_other_project)
    assert exc.value.status_code == 401, (
        "Cross-project token must be rejected (signature mismatch)"
    )


def test_supabase_es256_asymmetric_token_verification(monkeypatch, tmp_path):
    """
    Validates that verify_supabase_jwt supports modern Supabase ES256 asymmetric signing.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    class MockJWK:
        def __init__(self, key):
            self.key = key

    class MockJWKClient:
        def get_signing_key_from_jwt(self, token):
            return MockJWK(public_key)

    monkeypatch.setattr(main, "get_jwks_client", lambda url: MockJWKClient())
    monkeypatch.setattr(main, "SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test_es256.db")
    main.init_database()

    now = datetime.now(timezone.utc)
    es256_payload = {
        "iss": "https://test-project.supabase.co/auth/v1",
        "sub": "user-es256-uuid",
        "aud": "authenticated",
        "exp": int((now + timedelta(seconds=3600)).timestamp()),
        "iat": int(now.timestamp()),
        "email": "es256@example.com",
        "role": "authenticated",
    }
    valid_es256_token = jwt.encode(es256_payload, private_key, algorithm="ES256")

    verified = main.verify_supabase_jwt(valid_es256_token)
    assert verified is not None
    assert verified["sub"] == "user-es256-uuid"
    assert verified["email"] == "es256@example.com"


def test_supabase_token_clock_skew_tolerance():
    """
    Validates that verify_supabase_jwt tolerates clock skew where the cloud provider's
    issued-at (iat) timestamp is slightly in the future relative to the local machine.
    """
    user_id = "user-uuid-clock-skew"
    email = "skew@example.com"
    now = datetime.now(timezone.utc)
    # Token issued 15 seconds in the future
    payload = {
        "iss": f"{TEST_SUPABASE_URL}/auth/v1",
        "sub": user_id,
        "aud": "authenticated",
        "exp": int((now + timedelta(seconds=3600)).timestamp()),
        "iat": int((now + timedelta(seconds=15)).timestamp()),
        "email": email,
        "role": "authenticated",
        "jti": secrets.token_urlsafe(16),
    }
    skewed_token = jwt.encode(payload, TEST_SUPABASE_SECRET, algorithm="HS256")
    verified = main.verify_supabase_jwt(skewed_token)
    assert verified is not None, "verify_supabase_jwt must accept tokens with minor clock skew (iat in future)"
    assert verified["sub"] == user_id
