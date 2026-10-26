import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import sqlite3

os.environ.setdefault("CLOUDGUARD_JWT_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("CLOUDGUARD_FILE_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("CLOUDGUARD_ENV", "development")

from fastapi.testclient import TestClient

import main
from resend.exceptions import ResendError
from verification import (
    VerificationUnavailable,
    _compare_face_to_enrollment,
    _decide_face_outcome,
    _identity_matches_enrollment,
    _phrase_matches,
    _region_is_usable,
)


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Test User",
            "email": email,
            "phone": "+1 555 0100",
            "password": "test-password-123",
            "face_enrollment_samples_b64": [
                base64.b64encode(b"\xff\xd8\xffsample-" + bytes([index])).decode()
                for index in range(5)
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def login(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "test-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_spoken_phrase_matching_tolerates_transcription_spacing():
    assert _phrase_matches(
        "Today I am confirming my CloudGuard check-in.",
        "Today I am confirming my Cloud Guard check in.",
    )
    assert not _phrase_matches(
        "Today I am confirming my CloudGuard check-in.",
        "The weather is nice outside today.",
    )


def test_face_identity_requires_majority_of_enrollment_samples():
    same = [1.0, 0.0, 0.0]
    other = [0.0, 1.0, 0.0]
    different_angle = [0.70, 0.70, 0.0]
    assert _identity_matches_enrollment(same, [same, same, same, same, same])
    assert _identity_matches_enrollment(same, [same, same, same, other, other])
    assert not _identity_matches_enrollment(other, [same, same, same, same, same])
    assert not _identity_matches_enrollment(same, [same, other, other, other, other])
    assert not _identity_matches_enrollment(same, [same])
    assert not _identity_matches_enrollment(different_angle, [same, same, same, same, same])


def test_face_outcome_fails_closed_for_a_different_person():
    assert _decide_face_outcome(0, 0) == ("failed", "no_face")
    assert _decide_face_outcome(1, 1) == ("failed", "no_face")
    assert _decide_face_outcome(0, 4) == ("failed", "different_person")
    assert _decide_face_outcome(1, 5) == ("failed", "different_person")
    assert _decide_face_outcome(3, 4) == ("passed", "matched")
    assert _region_is_usable(20, 20, (720, 1280)) is False
    assert _region_is_usable(640, 480, (480, 640)) is False
    assert _region_is_usable(180, 180, (720, 1280), confidence=0.9) is True


def test_face_comparison_checks_enrollment_samples_and_returns_only_verified_matches():
    class FakeCV2:
        @staticmethod
        def imwrite(path, _frame):
            with open(path, "wb") as image:
                image.write(b"fake-image")
            return True

    class MatchingFaceModel:
        @staticmethod
        def represent(**_kwargs):
            return [{"embedding": [1.0, 0.0, 0.0]}]

    assert _compare_face_to_enrollment(
        MatchingFaceModel, FakeCV2, object(), [object(), object()]
    )

    class NonMatchingFaceModel:
        calls = 0

        @classmethod
        def represent(cls, **_kwargs):
            cls.calls += 1
            if cls.calls == 1:
                return [{"embedding": [1.0, 0.0, 0.0]}]
            return [{"embedding": [0.0, 1.0, 0.0]}]

    assert not _compare_face_to_enrollment(
        NonMatchingFaceModel, FakeCV2, object(), [object(), object()]
    )

    class LiveOkEnrollmentBroken:
        calls = 0

        @classmethod
        def represent(cls, **_kwargs):
            cls.calls += 1
            if cls.calls == 1:
                return [{"embedding": [1.0, 0.0, 0.0]}]
            raise RuntimeError("model unavailable")

    try:
        _compare_face_to_enrollment(
            LiveOkEnrollmentBroken, FakeCV2, object(), [object(), object()]
        )
    except VerificationUnavailable:
        pass
    else:
        raise AssertionError("Face-model errors must not be reported as ordinary non-matches.")


def test_owner_documents_are_encrypted_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        response = client.post(
            "/api/documents",
            headers=owner_headers,
            data={"name": "Important note", "category": "Personal"},
            files={"file": ("note.txt", b"private document contents", "text/plain")},
        )
        assert response.status_code == 201
        document_id = response.json()["document"]["id"]

        download = client.get(f"/api/documents/{document_id}/download", headers=owner_headers)
        assert download.status_code == 200
        assert download.content != b"private document contents"
        assert download.headers["x-cloudguard-encryption"] == "AES-256-GCM"
        assert download.headers["content-disposition"] == f'attachment; filename="{document_id}.cgenc"'
        assert main.decrypt_bytes(download.content) == b"private document contents"

        with sqlite3.connect(main.DATABASE) as connection:
            ciphertext = connection.execute(
                "SELECT encrypted_data FROM documents WHERE id=?", (document_id,)
            ).fetchone()[0]
            face_enrollment = connection.execute(
                "SELECT face_enrollment FROM users WHERE email='owner@example.com'"
            ).fetchone()[0]
        assert b"private document contents" not in ciphertext
        decrypted_samples = json.loads(main.decrypt_bytes(face_enrollment))
        assert len(decrypted_samples) == 5
        assert all(base64.b64decode(sample).startswith(b"\xff\xd8\xff") for sample in decrypted_samples)

        register(client, "another@example.com")
        other_headers = login(client, "another@example.com")
        assert client.get(f"/api/documents/{document_id}/download", headers=other_headers).status_code == 404
        assert client.delete(f"/api/documents/{document_id}", headers=other_headers).status_code == 404


def test_document_assignments_are_saved_updated_and_revoked_on_recipient_removal(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")

        recipient_ids = []
        for email in ("first@example.com", "second@example.com"):
            response = client.post(
                "/api/recipients",
                headers=headers,
                json={"name": "Trusted Person", "email": email, "relationship": "Friend"},
            )
            assert response.status_code == 201
            recipient_ids.append(response.json()["recipient"]["id"])

        upload = client.post(
            "/api/documents",
            headers=headers,
            data={
                "name": "Assigned document",
                "category": "Legal",
                "recipient_ids_json": json.dumps([recipient_ids[0]]),
            },
            files={"file": ("assigned.txt", b"encrypted contents", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["document"]["id"]
        listed = client.get("/api/documents", headers=headers).json()["documents"]
        assert next(doc for doc in listed if doc["id"] == document_id)["recipient_ids"] == [
            recipient_ids[0]
        ]
        with sqlite3.connect(main.DATABASE) as connection:
            assert connection.execute(
                "SELECT document_id,recipient_id FROM document_shares"
            ).fetchall() == [(document_id, recipient_ids[0])]

        updated = client.put(
            f"/api/documents/{document_id}/shares",
            headers=headers,
            json={"recipient_ids": [recipient_ids[1]]},
        )
        assert updated.status_code == 200
        listed = client.get("/api/documents", headers=headers).json()["documents"]
        assert next(doc for doc in listed if doc["id"] == document_id)["recipient_ids"] == [
            recipient_ids[1]
        ]

        cleared = client.put(
            f"/api/documents/{document_id}/shares",
            headers=headers,
            json={"recipient_ids": []},
        )
        assert cleared.status_code == 200
        listed = client.get("/api/documents", headers=headers).json()["documents"]
        assert next(doc for doc in listed if doc["id"] == document_id)["recipient_ids"] == []

        reassigned = client.put(
            f"/api/documents/{document_id}/shares",
            headers=headers,
            json={"recipient_ids": [recipient_ids[0]]},
        )
        assert reassigned.status_code == 200
        removed = client.delete(f"/api/recipients/{recipient_ids[0]}", headers=headers)
        assert removed.status_code == 204
        listed = client.get("/api/documents", headers=headers).json()["documents"]
        assert next(doc for doc in listed if doc["id"] == document_id)["recipient_ids"] == []
        with sqlite3.connect(main.DATABASE) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM document_shares WHERE document_id=?",
                (document_id,),
            ).fetchone()[0] == 0


def test_document_assignments_reject_foreign_recipient_and_document_ids(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        own_recipient = client.post(
            "/api/recipients",
            headers=owner_headers,
            json={"name": "Owner Recipient", "email": "own@example.com", "relationship": "Friend"},
        ).json()["recipient"]["id"]

        register(client, "other@example.com")
        other_headers = login(client, "other@example.com")
        foreign_recipient = client.post(
            "/api/recipients",
            headers=other_headers,
            json={"name": "Other Recipient", "email": "other-recipient@example.com", "relationship": "Friend"},
        ).json()["recipient"]["id"]

        rejected_upload = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "Must not be saved",
                "category": "Legal",
                "recipient_ids_json": json.dumps([foreign_recipient]),
            },
            files={"file": ("rejected.txt", b"contents", "text/plain")},
        )
        assert rejected_upload.status_code == 400
        assert client.get("/api/documents", headers=owner_headers).json()["documents"] == []

        upload = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "Owner document",
                "category": "Legal",
                "recipient_ids_json": json.dumps([own_recipient]),
            },
            files={"file": ("owner.txt", b"contents", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["document"]["id"]

        rejected_update = client.put(
            f"/api/documents/{document_id}/shares",
            headers=owner_headers,
            json={"recipient_ids": [foreign_recipient]},
        )
        assert rejected_update.status_code == 400
        assert client.put(
            f"/api/documents/{document_id}/shares",
            headers=other_headers,
            json={"recipient_ids": [foreign_recipient]},
        ).status_code == 404
        assert client.get(f"/api/documents/{document_id}/download", headers=other_headers).status_code == 404
        assert client.get("/api/documents", headers=other_headers).json()["documents"] == []
        listed = client.get("/api/documents", headers=owner_headers).json()["documents"]
        assert next(doc for doc in listed if doc["id"] == document_id)["recipient_ids"] == [
            own_recipient
        ]
        with sqlite3.connect(main.DATABASE) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM documents WHERE owner_id=(SELECT id FROM users WHERE email=?)",
                ("owner@example.com",),
            ).fetchone()[0] == 1


def test_recipients_and_checkins_are_private_and_labeled(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "providers_available", lambda: True)
    provider_status = {
        "face_status": "passed",
        "liveness_status": "passed",
        "speech_status": "passed",
    }
    verified_paths = []
    expected_sentence = "Today I am confirming my CloudGuard check-in."

    def verify_recording(path, samples, sentence):
        assert len(samples) == 5
        assert sentence == expected_sentence
        assert path.read_bytes() == b"test-recording"
        verified_paths.append(path)
        return provider_status.copy()

    monkeypatch.setattr(main, "verify_recording", verify_recording)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")

        created = client.post(
            "/api/recipients",
            headers=headers,
            json={"name": "Trusted Friend", "email": "friend@example.com", "relationship": "Friend"},
        )
        assert created.status_code == 201
        recipient_id = created.json()["recipient"]["id"]
        assert client.patch(
            f"/api/recipients/{recipient_id}",
            headers=headers,
            json={"name": "Trusted Friend", "email": "friend@example.com", "relationship": "Family friend"},
        ).status_code == 200

        passed = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": expected_sentence},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert passed.status_code == 201, passed.text
        assert passed.json()["checkin"]["status"] == "success"
        assert passed.json()["passed"] is True
        assert not verified_paths[0].exists()

        provider_status["face_status"] = "failed"
        provider_status["face_detail"] = "different_person"
        face_mismatch = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": expected_sentence},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert face_mismatch.status_code == 201
        assert face_mismatch.json()["checkin"]["status"] == "failed"
        assert face_mismatch.json()["passed"] is False
        assert face_mismatch.json()["checkin"]["face_status"] == "failed"
        assert face_mismatch.json()["checkin"]["face_detail"] == "different_person"
        assert "does not match the enrolled face" in face_mismatch.json()["message"]

        provider_status["face_status"] = "passed"
        provider_status["face_detail"] = "matched"
        provider_status["speech_status"] = "failed"
        failed = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": expected_sentence},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert failed.status_code == 201, failed.text
        assert failed.json()["checkin"]["status"] == "failed"
        assert failed.json()["passed"] is False
        assert failed.json()["checkin"]["speech_status"] == "failed"

        invalid_phrase = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": "An arbitrary phrase."},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert invalid_phrase.status_code == 422
        history = client.get("/api/check-ins", headers=headers).json()["checkins"]
        assert history[0]["mode"] == "local_ai"
        assert history[0]["status"] == "failed"
        assert history[0]["speech_status"] == "failed"
        assert history[1]["status"] == "failed"
        assert history[1]["face_status"] == "failed"
        assert history[2]["status"] == "success"


def test_checkin_fails_closed_when_local_models_are_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "providers_available", lambda: False)
    sentence = "Today I am confirming my CloudGuard check-in."
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        response = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": sentence},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert response.status_code == 503
        assert client.get("/api/check-ins", headers=headers).json()["checkins"] == []


def test_checkin_returns_503_when_verification_result_is_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "providers_available", lambda: True)
    monkeypatch.setattr(
        main,
        "verify_recording",
        lambda *_args, **_kwargs: {"face_status": "passed"},
    )
    sentence = "Today I am confirming my CloudGuard check-in."
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        response = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": sentence},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert response.status_code == 503
        assert client.get("/api/check-ins", headers=headers).json()["checkins"] == []


def test_registration_validation_and_document_extension_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        unsupported = client.post(
            "/api/documents",
            headers=headers,
            data={"name": "Executable", "category": "Other"},
            files={"file": ("payload.exe", b"not allowed", "application/octet-stream")},
        )
        assert unsupported.status_code == 415


def test_office_document_upload_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        response = client.post(
            "/api/documents",
            headers=headers,
            data={"name": "Life Insurance Policy", "category": "Insurance"},
            files={
                "file": (
                    "question bank.docx",
                    b"docx-test-payload",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["document"]["file_name"] == "question bank.docx"
        assert response.json()["document"]["category"] == "Insurance"


def test_nominee_is_a_single_private_contact_separate_from_recipients(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        assert client.get("/api/nominee", headers=owner_headers).json()["nominee"] is None

        saved = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Trusted Nominee",
                "email": "nominee@example.com",
                "relationship": "Sibling",
                "phone": "555-0123",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["nominee"]["name"] == "Trusted Nominee"
        updated = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Updated Nominee",
                "email": "nominee@example.com",
                "relationship": "Sibling",
                "phone": "",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["nominee"]["name"] == "Updated Nominee"
        assert client.get("/api/recipients", headers=owner_headers).json()["recipients"] == []

        register(client, "another@example.com")
        other_headers = login(client, "another@example.com")
        assert client.get("/api/nominee", headers=other_headers).json()["nominee"] is None
        assert client.delete("/api/nominee", headers=other_headers).status_code == 204
        assert client.get("/api/nominee", headers=owner_headers).json()["nominee"]["name"] == "Updated Nominee"
        assert client.delete("/api/nominee", headers=owner_headers).status_code == 204
        assert client.get("/api/nominee", headers=owner_headers).json()["nominee"] is None


def test_registration_requires_five_valid_face_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        payload = {
            "name": "Test User",
            "email": "owner@example.com",
            "phone": "+1 555 0100",
            "password": "test-password-123",
            "face_enrollment_samples_b64": [
                base64.b64encode(b"\xff\xd8\xffsample").decode()
            ] * 4,
        }
        assert client.post("/api/auth/register", json=payload).status_code == 422
        payload["email"] = "owner@example.com"
        payload["face_enrollment_samples_b64"] = [
            base64.b64encode(b"not-an-image").decode()
        ] * 5
        assert client.post("/api/auth/register", json=payload).status_code == 422


def test_logout_revokes_the_server_session(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/auth/me", headers=headers).status_code == 401


def _configure_resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "CloudGuard <invites@example.com>")
    monkeypatch.setenv("CLOUDGUARD_APP_URL", "https://cloudguard.example.com")


def test_invitation_configuration_falls_back_to_backend_env(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "RESEND_API_KEY=file-test-key\n"
        "RESEND_FROM_EMAIL=CloudGuard <verified@example.com>\n"
        "CLOUDGUARD_APP_URL=https://cloudguard.example.com\n",
        encoding="utf-8",
    )
    for name in ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "CLOUDGUARD_APP_URL"):
        monkeypatch.setenv(name, "")

    config, missing = main.invitation_email_configuration()

    assert config == {
        "RESEND_API_KEY": "file-test-key",
        "RESEND_FROM_EMAIL": "CloudGuard <verified@example.com>",
        "CLOUDGUARD_APP_URL": "https://cloudguard.example.com",
    }
    assert missing == []


def test_invitation_requires_email_configuration_without_creating_invitation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    for name in ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "CLOUDGUARD_APP_URL"):
        monkeypatch.delenv(name, raising=False)

    def unexpected_email(_message):
        raise AssertionError("Resend must not be called with incomplete configuration.")

    monkeypatch.setattr(main.resend.Emails, "send", unexpected_email)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)
        response = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )

        assert response.status_code == 503
        assert "RESEND_API_KEY" in response.json()["detail"]
        assert "RESEND_FROM_EMAIL" in response.json()["detail"]
        assert "CLOUDGUARD_APP_URL" in response.json()["detail"]
        with sqlite3.connect(main.DATABASE) as connection:
            assert connection.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 0


def _create_recipient(client, headers, email="guest@example.com"):
    response = client.post(
        "/api/recipients",
        headers=headers,
        json={"name": "Invited Guest", "email": email, "relationship": "Friend"},
    )
    assert response.status_code == 201
    return response.json()["recipient"]["id"]


def test_recipient_invitation_creates_single_use_restricted_account(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []

    def accept_email(message):
        sent_messages.append(message)
        return {"id": "resend-test-id"}

    monkeypatch.setattr(main.resend.Emails, "send", accept_email)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)

        invited = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert invited.status_code == 201, invited.text
        assert invited.json()["invitation"]["status"] == "sent"
        message = sent_messages[0]
        assert message["from"] == "CloudGuard <invites@example.com>"
        assert message["to"] == ["guest@example.com"]
        assert "Authorized Recipient" in message["text"]
        assert "test-resend-key" not in json.dumps(message)
        token = message["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        with sqlite3.connect(main.DATABASE) as connection:
            stored_hash = connection.execute(
                "SELECT token_hash FROM invitations WHERE id=?",
                (invited.json()["invitation"]["id"],),
            ).fetchone()[0]
        assert stored_hash == hashlib.sha256(token.encode("ascii")).hexdigest()
        assert token not in stored_hash

        recipient_password = "recipient-password-123"
        claimed = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": recipient_password},
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["principal_type"] == "recipient"
        guest_headers = {"Authorization": f"Bearer {claimed.json()['access_token']}"}
        recipient_info = client.get("/api/recipient/me", headers=guest_headers)
        assert recipient_info.status_code == 200
        assert recipient_info.json()["recipient"]["role"] == "recipient"
        assert client.get("/api/recipient/documents", headers=guest_headers).json()["documents"] == []
        assert client.get("/api/dashboard", headers=guest_headers).status_code == 401
        assert client.get("/api/documents", headers=guest_headers).status_code == 401
        assert client.get("/api/guest/me", headers=guest_headers).status_code == 401
        assert client.get("/api/recipient/me", headers=owner_headers).status_code == 401
        assert client.post("/api/recipient/logout", headers=guest_headers).status_code == 204
        assert client.get("/api/recipient/me", headers=guest_headers).status_code == 401

        signed_in = client.post(
            "/api/recipient/login",
            json={"email": "guest@example.com", "password": recipient_password},
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["principal_type"] == "recipient"
        guest_headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        assert client.get("/api/recipient/me", headers=guest_headers).status_code == 200
        assert client.post("/api/recipient/logout", headers=guest_headers).status_code == 204
        assert client.post(
            "/api/guest/invitations/claim", json={"token": token}
        ).status_code == 400


def test_nominee_review_release_and_medical_hold_enforce_separate_permissions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "REVIEWER_EMAIL", "reviewer@example.com")
    reviewer_password = "reviewer-password-123"
    monkeypatch.setattr(
        main,
        "REVIEWER_PASSWORD_HASH",
        main.bcrypt.hashpw(reviewer_password.encode(), main.bcrypt.gensalt()).decode(),
    )
    monkeypatch.setattr(main, "providers_available", lambda: True)
    monkeypatch.setattr(
        main,
        "verify_recording",
        lambda *_args: {
            "face_status": "passed",
            "liveness_status": "passed",
            "speech_status": "passed",
        },
    )
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda message: (sent_messages.append(message) or {"id": "resend-test-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")
        nominee_response = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Trusted Nominee",
                "email": "nominee@example.com",
                "relationship": "Sibling",
                "phone": "",
            },
        )
        assert nominee_response.status_code == 200
        nominee_id = nominee_response.json()["nominee"]["id"]

        upload = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "Family policy",
                "category": "Legal",
                "recipient_ids_json": json.dumps([recipient_id]),
            },
            files={"file": ("policy.txt", b"secret policy", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["document"]["id"]
        assert client.post(
            f"/api/documents/{document_id}/releases",
            headers=owner_headers,
            json={"recipient_id": recipient_id},
        ).status_code == 409

        for role, target_id in (("recipient", recipient_id), ("nominee", nominee_id)):
            created = client.post(
                "/api/invitations",
                headers=owner_headers,
                json={"role": role, "target_id": target_id},
            )
            assert created.status_code == 201, created.text
            invitation_token = sent_messages[-1]["text"].split(
                "#guest-invitation=", 1
            )[1].splitlines()[0]
            preview = client.get(
                f"/api/guest/invitations/preview?token={invitation_token}"
            )
            assert preview.status_code == 200
            assert preview.json()["role"] == role
            assert preview.json()["requires_password"] is True
            claimed = client.post(
                "/api/guest/invitations/claim",
                json={"token": invitation_token, "password": "role-password-123"},
            )
            assert claimed.status_code == 200, claimed.text
            assert claimed.json()["principal_type"] == role
            if role == "recipient":
                recipient_headers = {
                    "Authorization": f"Bearer {claimed.json()['access_token']}"
                }
            else:
                nominee_headers = {
                    "Authorization": f"Bearer {claimed.json()['access_token']}"
                }

        assert client.get("/api/documents", headers=nominee_headers).status_code == 401
        assert client.get("/api/recipient/documents", headers=nominee_headers).status_code == 401
        assert client.get("/api/nominee/dashboard", headers=nominee_headers).json()["case_status"] == "not_submitted"
        submitted = client.post(
            "/api/nominee/case",
            headers=nominee_headers,
            data={"note": "Please review this certificate."},
            files={"evidence": ("certificate.pdf", b"%PDF-1.7 test evidence", "application/pdf")},
        )
        assert submitted.status_code == 201, submitted.text
        case_id = submitted.json()["case"]["id"]
        assert submitted.json()["case"]["status"] == "under_review"
        with sqlite3.connect(main.DATABASE) as connection:
            encrypted_evidence = connection.execute(
                "SELECT evidence_ciphertext FROM death_cases WHERE id=?", (case_id,)
            ).fetchone()[0]
        assert b"%PDF-1.7 test evidence" not in encrypted_evidence

        reviewer_login = client.post(
            "/api/reviewer/login",
            json={"email": "reviewer@example.com", "password": reviewer_password},
        )
        assert reviewer_login.status_code == 200, reviewer_login.text
        reviewer_headers = {
            "Authorization": f"Bearer {reviewer_login.json()['access_token']}"
        }
        assert client.get("/api/documents", headers=reviewer_headers).status_code == 401
        queue = client.get("/api/reviewer/cases", headers=reviewer_headers)
        assert queue.status_code == 200
        assert queue.json()["cases"][0]["id"] == case_id
        evidence = client.get(
            f"/api/reviewer/cases/{case_id}/evidence", headers=reviewer_headers
        )
        assert evidence.status_code == 200
        assert evidence.content == b"%PDF-1.7 test evidence"
        requested = client.post(
            f"/api/reviewer/cases/{case_id}/decision",
            headers=reviewer_headers,
            json={
                "decision": "more_information_required",
                "message": "Please submit a clearer copy of the certificate.",
            },
        )
        assert requested.status_code == 200
        assert requested.json()["case"]["status"] == "more_information_required"
        resubmitted = client.post(
            "/api/nominee/case",
            headers=nominee_headers,
            data={"note": "Here is the clearer copy."},
            files={"evidence": ("certificate.png", b"\x89PNG\r\n\x1a\nupdated", "image/png")},
        )
        assert resubmitted.status_code == 201, resubmitted.text
        assert resubmitted.json()["case"]["status"] == "under_review"
        decision = client.post(
            f"/api/reviewer/cases/{case_id}/decision",
            headers=reviewer_headers,
            json={"decision": "approved", "message": "Certificate reviewed and approved."},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["case"]["status"] == "approved"
        assert client.get("/api/nominee/dashboard", headers=nominee_headers).json()["case_status"] == "approved"

        released = client.post(
            f"/api/documents/{document_id}/releases",
            headers=owner_headers,
            json={"recipient_id": recipient_id},
        )
        assert released.status_code == 200, released.text
        assert client.get(
            "/api/recipient/documents", headers=recipient_headers
        ).json()["documents"][0]["access_status"] == "available"
        assert client.get(
            f"/api/recipient/documents/{document_id}/download", headers=recipient_headers
        ).content == b"secret policy"

        hold_started = client.put(
            "/api/medical-hold", headers=owner_headers, json={"action": "start"}
        )
        assert hold_started.status_code == 200
        assert hold_started.json()["medical_hold"]["status"] == "active"
        held_doc = client.get(
            "/api/recipient/documents", headers=recipient_headers
        ).json()["documents"][0]
        assert held_doc["access_status"] == "pending"
        assert held_doc["release_hold"] is True
        assert client.get(
            f"/api/recipient/documents/{document_id}/download", headers=recipient_headers
        ).status_code == 403
        assert client.post(
            f"/api/documents/{document_id}/releases",
            headers=owner_headers,
            json={"recipient_id": recipient_id},
        ).status_code == 409

        hold_ended = client.put(
            "/api/medical-hold", headers=owner_headers, json={"action": "end"}
        )
        assert hold_ended.status_code == 200
        assert hold_ended.json()["medical_hold"]["status"] == "awaiting_checkin"
        assert client.get(
            f"/api/recipient/documents/{document_id}/download", headers=recipient_headers
        ).status_code == 403
        checkin = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": "Today I am confirming my CloudGuard check-in."},
            files={"recording": ("check-in.webm", b"test-recording", "video/webm")},
        )
        assert checkin.status_code == 201, checkin.text
        assert checkin.json()["passed"] is True
        assert client.get("/api/medical-hold", headers=owner_headers).json()["medical_hold"]["status"] == "inactive"
        assert client.get(
            f"/api/recipient/documents/{document_id}/download", headers=recipient_headers
        ).status_code == 200
        revoked = client.delete(
            f"/api/documents/{document_id}/releases/{recipient_id}",
            headers=owner_headers,
        )
        assert revoked.status_code == 200
        assert client.get(
            f"/api/recipient/documents/{document_id}/download", headers=recipient_headers
        ).status_code == 403

        with sqlite3.connect(main.DATABASE) as connection:
            events = {
                row[0]
                for row in connection.execute("SELECT event FROM audit_logs")
            }
        assert f"nominee.case_evidence_submitted:{case_id}" in events
        assert f"nominee.case_reviewed:{case_id}:approved:reviewer@example.com" in events
        assert "medical_hold.started" in events
        assert "medical_hold.ended_awaiting_checkin" in events
        assert "medical_hold.fresh_checkin_completed" in events
        assert f"document.release_revoked:{document_id}:{recipient_id}" in events


def test_resend_failure_does_not_report_or_activate_invitation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)

    def reject_email(_message):
        raise RuntimeError("provider rejected the request")

    monkeypatch.setattr(main.resend.Emails, "send", reject_email)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)
        response = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert response.status_code == 502
        assert "sent" not in response.json().get("invitation", {})
        invitations = client.get("/api/invitations", headers=owner_headers).json()["invitations"]
        assert len(invitations) == 1
        assert invitations[0]["status"] == "failed"


def test_failed_resend_preserves_previous_invitation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []

    def send_or_reject(message):
        if sent_messages:
            raise RuntimeError("provider rejected the request")
        sent_messages.append(message)
        return {"id": "resend-test-id"}

    monkeypatch.setattr(main.resend.Emails, "send", send_or_reject)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)
        first = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert first.status_code == 201
        token = sent_messages[0]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        failed = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert failed.status_code == 502
        invitations = client.get("/api/invitations", headers=owner_headers).json()["invitations"]
        assert [item["status"] for item in invitations] == ["failed", "sent"]

        claimed = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recipient-password-123"},
        )
        assert claimed.status_code == 200


def test_resend_rejection_exposes_sanitized_provider_details(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)

    def reject_email(_message):
        raise ResendError(
            code=403,
            error_type="validation_error",
            message=(
                "Sender invites@example.com is not verified for guest@example.com. "
                "Authorization: Bearer test-resend-key"
            ),
            suggested_action="Verify the sender domain.",
        )

    monkeypatch.setattr(main.resend.Emails, "send", reject_email)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)
        response = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "HTTP 403" in detail
        assert "validation_error" in detail
        assert "not verified" in detail
        assert "guest@example.com" not in detail
        assert "invites@example.com" not in detail
        assert "test-resend-key" not in detail
        assert "Bearer" not in detail
        invitations = client.get("/api/invitations", headers=owner_headers).json()["invitations"]
        assert len(invitations) == 1
        assert invitations[0]["status"] == "failed"


def test_invitation_scope_role_expiry_and_contact_change_revocation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda message: (sent_messages.append(message) or {"id": "resend-test-id"}),
    )
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers)

        register(client, "another@example.com")
        other_headers = login(client, "another@example.com")
        foreign_recipient = _create_recipient(client, other_headers, "foreign@example.com")
        assert client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": foreign_recipient},
        ).status_code == 404
        assert client.post(
            "/api/invitations",
            headers=other_headers,
            json={"role": "recipient", "target_id": recipient_id},
        ).status_code == 404
        assert client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "nominee", "target_id": recipient_id},
        ).status_code == 404
        assert client.get("/api/invitations", headers=other_headers).json()["invitations"] == []

        created = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        invitation_id = created.json()["invitation"]["id"]
        assert client.delete(
            f"/api/invitations/{invitation_id}", headers=other_headers
        ).status_code == 404
        assert client.delete(
            f"/api/invitations/{invitation_id}", headers=owner_headers
        ).status_code == 204
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        assert client.post(
            "/api/guest/invitations/claim", json={"token": token}
        ).status_code == 400

        expired = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        expired_id = expired.json()["invitation"]["id"]
        expired_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        with sqlite3.connect(main.DATABASE) as connection:
            connection.execute(
                "UPDATE invitations SET expires_at=? WHERE id=?",
                ((main.utc_now() - main.timedelta(days=1)).isoformat(), expired_id),
            )
        assert client.post(
            "/api/guest/invitations/claim", json={"token": expired_token}
        ).status_code == 400
        assert client.post(
            "/api/guest/invitations/claim", json={"token": "é" * 43}
        ).status_code == 400

        active = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        active_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        claimed = client.post(
            "/api/guest/invitations/claim",
            json={"token": active_token, "password": "recipient-password-123"},
        )
        assert claimed.status_code == 200
        assert claimed.json()["principal_type"] == "recipient"
        recipient_headers = {"Authorization": f"Bearer {claimed.json()['access_token']}"}
        changed = client.patch(
            f"/api/recipients/{recipient_id}",
            headers=owner_headers,
            json={"name": "Invited Guest", "email": "changed@example.com", "relationship": "Friend"},
        )
        assert changed.status_code == 200
        assert client.get("/api/recipient/me", headers=recipient_headers).status_code == 401
        assert client.get("/api/invitations", headers=owner_headers).json()["invitations"][0]["status"] == "revoked"


def test_unauthenticated_user_cannot_create_or_list_invitations(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        assert client.post("/api/invitations", json={"role": "recipient", "target_id": 1}).status_code == 401
        assert client.get("/api/invitations").status_code == 401
        assert client.delete("/api/invitations/1").status_code == 401


def test_invitation_requires_valid_app_url(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    monkeypatch.setenv("CLOUDGUARD_APP_URL", "not-a-valid-http-url")

    def unexpected_send(_msg):
        raise AssertionError("Resend must not be called with an invalid application URL.")

    monkeypatch.setattr(main.resend.Emails, "send", unexpected_send)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, headers)
        response = client.post(
            "/api/invitations",
            headers=headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert response.status_code == 502
        invitations = client.get("/api/invitations", headers=headers).json()["invitations"]
        assert len(invitations) == 1
        assert invitations[0]["status"] == "failed"


def test_invitation_network_failure_marks_invitation_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)

    def network_error(_msg):
        raise ConnectionError("Connection reset by peer to api.resend.com")

    monkeypatch.setattr(main.resend.Emails, "send", network_error)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, headers)
        response = client.post(
            "/api/invitations",
            headers=headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert response.status_code == 502
        invitations = client.get("/api/invitations", headers=headers).json()["invitations"]
        assert len(invitations) == 1
        assert invitations[0]["status"] == "failed"


def test_invitation_empty_provider_response_id_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)

    def empty_response(_msg):
        return {"id": ""}

    monkeypatch.setattr(main.resend.Emails, "send", empty_response)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, headers)
        response = client.post(
            "/api/invitations",
            headers=headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert response.status_code == 502
        invitations = client.get("/api/invitations", headers=headers).json()["invitations"]
        assert len(invitations) == 1
        assert invitations[0]["status"] == "failed"


def test_invitation_email_includes_application_link_and_role_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []

    def capture_send(message):
        sent_messages.append(message)
        return {"id": "resend-msg-12345"}

    monkeypatch.setattr(main.resend.Emails, "send", capture_send)
    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, headers, "recipient-contact@example.com")

        response = client.post(
            "/api/invitations",
            headers=headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert response.status_code == 201
        assert response.json()["invitation"]["provider_id"] == "resend-msg-12345"

        msg = sent_messages[-1]
        assert msg["from"] == "CloudGuard <invites@example.com>"
        assert msg["to"] == ["recipient-contact@example.com"]
        assert "https://cloudguard.example.com" in msg["text"]
        assert "#guest-invitation=" in msg["text"]
        assert "Authorized Recipient" in msg["text"]
        assert "Security & Access" in msg["text"] or "Security & Access Notice" in msg["html"]
        assert "html" in msg
        assert "https://cloudguard.example.com" in msg["html"]
        assert "#guest-invitation=" in msg["html"]

        # Ensure no secrets, tokens, or encryption keys leaked into the email body
        raw_msg_str = json.dumps(msg)
        assert "test-resend-key" not in raw_msg_str
        assert os.environ["CLOUDGUARD_JWT_SECRET"] not in raw_msg_str
        assert os.environ["CLOUDGUARD_FILE_KEY"] not in raw_msg_str


def test_stage2_successful_invitation_acceptance(tmp_path, monkeypatch):
    """1. Successful invitation acceptance for both recipient and nominee."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-test-123"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")

        # 1a. Recipient invitation acceptance
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")
        create_resp = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        assert create_resp.status_code == 201
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Preview check
        preview = client.get(f"/api/guest/invitations/preview?token={token}")
        assert preview.status_code == 200
        data = preview.json()
        assert data["role"] == "recipient"
        assert data["requires_password"] is True
        assert data["existing_account"] is False
        assert data["owner_name"] == "Test User"
        assert "r***@example.com" in data["email_hint"]

        # Claim with secure password
        claimed = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recipient-password-123"},
        )
        assert claimed.status_code == 200
        claim_data = claimed.json()
        assert claim_data["principal_type"] == "recipient"
        recip_headers = {"Authorization": f"Bearer {claim_data['access_token']}"}

        # Verify access to /api/recipient/me
        me = client.get("/api/recipient/me", headers=recip_headers)
        assert me.status_code == 200
        assert me.json()["recipient"]["email"] == "recipient@example.com"

        # Verify invitation status is 'claimed' in DB
        with sqlite3.connect(main.DATABASE) as conn:
            row = conn.execute(
                "SELECT status, claimed_at FROM invitations WHERE id=?",
                (create_resp.json()["invitation"]["id"],),
            ).fetchone()
            assert row[0] == "claimed"
            assert row[1] is not None

        # Verify login with created password works
        login_resp = client.post(
            "/api/recipient/login",
            json={"email": "recipient@example.com", "password": "recipient-password-123"},
        )
        assert login_resp.status_code == 200
        assert login_resp.json()["principal_type"] == "recipient"

        # 1b. Nominee invitation acceptance
        nom_resp = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Trusted Nominee",
                "email": "nominee@example.com",
                "relationship": "Sister",
                "phone": "+1 555 0199",
            },
        )
        assert nom_resp.status_code == 200
        nominee_id = nom_resp.json()["nominee"]["id"]

        create_nom_invite = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "nominee", "target_id": nominee_id},
        )
        assert create_nom_invite.status_code == 201
        nom_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Preview check for nominee
        nom_preview = client.get(f"/api/guest/invitations/preview?token={nom_token}")
        assert nom_preview.status_code == 200
        assert nom_preview.json()["role"] == "nominee"
        assert nom_preview.json()["requires_password"] is True
        assert nom_preview.json()["existing_account"] is False

        # Claim nominee invitation
        nom_claimed = client.post(
            "/api/guest/invitations/claim",
            json={"token": nom_token, "password": "nominee-password-123"},
        )
        assert nom_claimed.status_code == 200
        nom_headers = {"Authorization": f"Bearer {nom_claimed.json()['access_token']}"}

        # Verify access to /api/nominee/me
        nom_me = client.get("/api/nominee/me", headers=nom_headers)
        assert nom_me.status_code == 200
        assert nom_me.json()["nominee"]["email"] == "nominee@example.com"


def test_stage2_invalid_expired_revoked_and_claimed_invitations(tmp_path, monkeypatch):
    """2. Safe, clear error messages for invalid, expired, revoked, and already-claimed invitations."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # 2a. Invalid token (<43 chars or unknown token)
        for bad_token in ["too-short", "x" * 42, "unknown-token-" + "x" * 32]:
            prev = client.get(f"/api/guest/invitations/preview?token={bad_token}")
            assert prev.status_code == 400
            assert "invalid" in prev.json()["detail"].lower()

            cl = client.post("/api/guest/invitations/claim", json={"token": bad_token, "password": "password-12345"})
            assert cl.status_code == 400
            assert "invalid" in cl.json()["detail"].lower()

        # Create valid invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        valid_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # 2b. Expired invitation
        with sqlite3.connect(main.DATABASE) as conn:
            conn.execute("UPDATE invitations SET expires_at='2020-01-01T00:00:00+00:00'")

        prev_exp = client.get(f"/api/guest/invitations/preview?token={valid_token}")
        assert prev_exp.status_code == 400
        assert "expired" in prev_exp.json()["detail"].lower()

        cl_exp = client.post("/api/guest/invitations/claim", json={"token": valid_token, "password": "password-12345"})
        assert cl_exp.status_code == 400
        assert "expired" in cl_exp.json()["detail"].lower()

        # 2c. Revoked invitation
        inv_id = client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        ).json()["invitation"]["id"]
        revoked_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Owner explicitly revokes
        del_resp = client.delete(f"/api/invitations/{inv_id}", headers=owner_headers)
        assert del_resp.status_code == 204

        prev_rev = client.get(f"/api/guest/invitations/preview?token={revoked_token}")
        assert prev_rev.status_code == 400
        assert "revoked" in prev_rev.json()["detail"].lower()

        cl_rev = client.post("/api/guest/invitations/claim", json={"token": revoked_token, "password": "password-12345"})
        assert cl_rev.status_code == 400
        assert "revoked" in cl_rev.json()["detail"].lower()

        # 2d. Already-claimed invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        claimable_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        claim_first = client.post(
            "/api/guest/invitations/claim",
            json={"token": claimable_token, "password": "password-12345"},
        )
        assert claim_first.status_code == 200

        # Attempt to preview and claim again
        prev_again = client.get(f"/api/guest/invitations/preview?token={claimable_token}")
        assert prev_again.status_code == 400
        assert "already been accepted" in prev_again.json()["detail"].lower()

        claim_again = client.post(
            "/api/guest/invitations/claim",
            json={"token": claimable_token, "password": "password-12345"},
        )
        assert claim_again.status_code == 400
        assert "already been accepted" in claim_again.json()["detail"].lower()


def test_stage2_rejects_claim_with_different_email_identity(tmp_path, monkeypatch):
    """3. Attempting to accept an invitation using a different email identity."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "legit-recipient@example.com")

        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Attacker tries to claim under a different email identity
        response = client.post(
            "/api/guest/invitations/claim",
            json={
                "token": token,
                "password": "attacker-password-123",
                "email": "attacker@evil.com",
            },
        )
        assert response.status_code == 403
        assert "email address does not match" in response.json()["detail"]

        # Ensure invitation was not claimed
        with sqlite3.connect(main.DATABASE) as conn:
            status = conn.execute("SELECT status FROM invitations").fetchone()[0]
            assert status == "sent"


def test_stage2_rejects_claim_with_tampered_role(tmp_path, monkeypatch):
    """4. Attempting to change the assigned role."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Attacker tries to claim a recipient invite as a nominee or owner
        for fake_role in ["nominee", "owner", "admin"]:
            response = client.post(
                "/api/guest/invitations/claim",
                json={
                    "token": token,
                    "password": "role-tamper-password",
                    "role": fake_role,
                },
            )
            assert response.status_code == 403
            assert "role does not match" in response.json()["detail"]

        # Ensure invitation was not claimed
        with sqlite3.connect(main.DATABASE) as conn:
            status = conn.execute("SELECT status FROM invitations").fetchone()[0]
            assert status == "sent"


def test_stage2_duplicate_account_creation_and_repeated_acceptance(tmp_path, monkeypatch):
    """5. Duplicate account creation and repeated acceptance behavior."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # 5a. First invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token1 = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Claim first time
        claim1 = client.post(
            "/api/guest/invitations/claim",
            json={"token": token1, "password": "password-initial-123"},
        )
        assert claim1.status_code == 200

        # Repeated acceptance on the exact same token must fail and not create duplicates
        claim1_repeat = client.post(
            "/api/guest/invitations/claim",
            json={"token": token1, "password": "password-initial-123"},
        )
        assert claim1_repeat.status_code == 400
        assert "already been accepted" in claim1_repeat.json()["detail"].lower()

        # Check DB: exactly 1 account exists
        with sqlite3.connect(main.DATABASE) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM recipient_accounts WHERE recipient_id=?", (recipient_id,)
            ).fetchone()[0]
            assert count == 1

        # 5b. Owner re-invites the same recipient contact (e.g. for re-setup / password reset)
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token2 = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]

        # Preview must report existing_account = True
        preview2 = client.get(f"/api/guest/invitations/preview?token={token2}")
        assert preview2.status_code == 200
        assert preview2.json()["existing_account"] is True

        # Claiming with updated password
        claim2 = client.post(
            "/api/guest/invitations/claim",
            json={"token": token2, "password": "new-password-updated-123"},
        )
        assert claim2.status_code == 200

        # Verify exactly 1 account still exists (no duplicate account created)
        with sqlite3.connect(main.DATABASE) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM recipient_accounts WHERE recipient_id=?", (recipient_id,)
            ).fetchone()[0]
            assert count == 1

        # Old password fails; new password works
        assert client.post(
            "/api/recipient/login",
            json={"email": "recipient@example.com", "password": "password-initial-123"},
        ).status_code == 401

        new_login = client.post(
            "/api/recipient/login",
            json={"email": "recipient@example.com", "password": "new-password-updated-123"},
        )
        assert new_login.status_code == 200


def test_stage2_unauthenticated_access_to_protected_endpoints(tmp_path, monkeypatch):
    """6. Unauthenticated access to protected endpoints is rejected with 401."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    with TestClient(main.app) as client:
        # Protected endpoints must reject missing or invalid authorization
        endpoints = [
            ("GET", "/api/documents"),
            ("GET", "/api/documents/1/download"),
            ("GET", "/api/recipient/documents"),
            ("GET", "/api/recipient/documents/1"),
            ("GET", "/api/recipient/documents/1/download"),
            ("GET", "/api/recipient/me"),
            ("GET", "/api/recipient/history"),
            ("POST", "/api/recipient/logout"),
            ("GET", "/api/nominee/dashboard"),
            ("GET", "/api/nominee/case"),
            ("GET", "/api/nominee/me"),
            ("GET", "/api/nominee/history"),
            ("POST", "/api/nominee/logout"),
            ("GET", "/api/dashboard"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/auth/logout"),
            ("GET", "/api/recipients"),
            ("GET", "/api/invitations"),
        ]
        for method, endpoint in endpoints:
            if method == "GET":
                resp = client.get(endpoint)
            else:
                resp = client.post(endpoint)
            assert resp.status_code == 401, f"{method} {endpoint} returned {resp.status_code}"


def test_stage2_recipient_accessing_unassigned_document_blocked(tmp_path, monkeypatch):
    """7. A recipient accessing an unassigned document is rejected with 404."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # Document 1: assigned to recipient
        doc1 = client.post(
            "/api/documents",
            headers=owner_headers,
            data={"name": "Assigned Document", "category": "Legal", "recipient_ids_json": json.dumps([recipient_id])},
            files={"file": ("doc1.txt", b"assigned content", "text/plain")},
        ).json()["document"]["id"]

        # Document 2: NOT assigned to recipient
        doc2 = client.post(
            "/api/documents",
            headers=owner_headers,
            data={"name": "Private Document", "category": "Financial", "recipient_ids_json": json.dumps([])},
            files={"file": ("doc2.txt", b"private content", "text/plain")},
        ).json()["document"]["id"]

        # Recipient accepts invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        recip_token = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recipient-password-123"},
        ).json()["access_token"]
        recip_headers = {"Authorization": f"Bearer {recip_token}"}

        # Recipient lists documents: only doc1 appears
        docs = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"]
        doc_ids = [d["id"] for d in docs]
        assert doc1 in doc_ids
        assert doc2 not in doc_ids

        # Attempt to get details of doc2 -> 404
        assert client.get(f"/api/recipient/documents/{doc2}", headers=recip_headers).status_code == 404

        # Attempt to download doc2 -> 404
        assert client.get(f"/api/recipient/documents/{doc2}/download", headers=recip_headers).status_code == 404


def test_stage2_nominee_attempting_to_download_documents_blocked(tmp_path, monkeypatch):
    """8. A nominee attempting to download documents without authorization is rejected."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")

        # Create a document
        doc_id = client.post(
            "/api/documents",
            headers=owner_headers,
            data={"name": "Vault Record", "category": "Personal"},
            files={"file": ("record.txt", b"vault record content", "text/plain")},
        ).json()["document"]["id"]

        # Create nominee and accept invitation
        nom_resp = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Trusted Nominee",
                "email": "nominee@example.com",
                "relationship": "Brother",
                "phone": "+1 555 0199",
            },
        )
        nominee_id = nom_resp.json()["nominee"]["id"]

        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "nominee", "target_id": nominee_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        nom_token = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "nominee-password-123"},
        ).json()["access_token"]
        nom_headers = {"Authorization": f"Bearer {nom_token}"}

        # Nominee attempts owner document endpoints -> 401
        assert client.get("/api/documents", headers=nom_headers).status_code == 401
        assert client.get(f"/api/documents/{doc_id}/download", headers=nom_headers).status_code == 401

        # Nominee attempts recipient document endpoints -> 401
        assert client.get("/api/recipient/documents", headers=nom_headers).status_code == 401
        assert client.get(f"/api/recipient/documents/{doc_id}", headers=nom_headers).status_code == 401
        assert client.get(f"/api/recipient/documents/{doc_id}/download", headers=nom_headers).status_code == 401


def test_stage2_cross_owner_data_access_blocked(tmp_path, monkeypatch):
    """9. Cross-owner data access attempts are strictly blocked."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        # Owner 1 sets up account, document, and recipient
        register(client, "owner1@example.com")
        owner1_headers = login(client, "owner1@example.com")
        recip1_id = _create_recipient(client, owner1_headers, "recip1@example.com")
        doc1_id = client.post(
            "/api/documents",
            headers=owner1_headers,
            data={"name": "Owner 1 Secret", "category": "Legal"},
            files={"file": ("secret.txt", b"owner 1 secret content", "text/plain")},
        ).json()["document"]["id"]
        invite1_id = client.post(
            "/api/invitations",
            headers=owner1_headers,
            json={"role": "recipient", "target_id": recip1_id},
        ).json()["invitation"]["id"]

        # Owner 2 registers and attempts cross-owner access
        register(client, "owner2@example.com")
        owner2_headers = login(client, "owner2@example.com")

        # Owner 2 cannot download Owner 1's document
        assert client.get(f"/api/documents/{doc1_id}/download", headers=owner2_headers).status_code == 404

        # Owner 2 cannot delete Owner 1's document
        assert client.delete(f"/api/documents/{doc1_id}", headers=owner2_headers).status_code == 404

        # Owner 2's document list does not contain doc1
        assert client.get("/api/documents", headers=owner2_headers).json()["documents"] == []

        # Owner 2's recipient list does not contain recip1
        assert client.get("/api/recipients", headers=owner2_headers).json()["recipients"] == []

        # Owner 2 cannot revoke Owner 1's invitation
        assert client.delete(f"/api/invitations/{invite1_id}", headers=owner2_headers).status_code == 404


def test_stage2_invitation_acceptance_does_not_release_documents(tmp_path, monkeypatch):
    """10. Confirmation that invitation acceptance does not release documents."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # Upload document and assign to recipient
        doc_id = client.post(
            "/api/documents",
            headers=owner_headers,
            data={"name": "Will and Testament", "category": "Legal", "recipient_ids_json": json.dumps([recipient_id])},
            files={"file": ("will.txt", b"confidential will contents", "text/plain")},
        ).json()["document"]["id"]

        # Send and claim invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        recip_token = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recipient-password-123"},
        ).json()["access_token"]
        recip_headers = {"Authorization": f"Bearer {recip_token}"}

        # Check document list: access_status MUST BE 'pending', released_at MUST BE None
        doc_list = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"]
        assert len(doc_list) == 1
        assert doc_list[0]["id"] == doc_id
        assert doc_list[0]["access_status"] == "pending"
        assert doc_list[0]["released_at"] is None
        assert doc_list[0]["case_approved"] is False

        # Attempt to download: MUST be rejected with 403 Forbidden!
        download_resp = client.get(f"/api/recipient/documents/{doc_id}/download", headers=recip_headers)
        assert download_resp.status_code == 403
        assert "locked" in download_resp.json()["detail"].lower()

        # Database verification: no release was granted
        with sqlite3.connect(main.DATABASE) as conn:
            releases = conn.execute(
                "SELECT * FROM document_releases WHERE document_id=? AND recipient_id=?",
                (doc_id, recipient_id),
            ).fetchall()
            assert len(releases) == 0


def test_stage2_session_expiry_and_logout_behavior(tmp_path, monkeypatch):
    """11. Session expiry and logout behavior for recipient, nominee, and owner."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # Set up recipient
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        recip_token_str = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        recip_jwt = client.post(
            "/api/guest/invitations/claim",
            json={"token": recip_token_str, "password": "recipient-password-123"},
        ).json()["access_token"]
        recip_headers = {"Authorization": f"Bearer {recip_jwt}"}

        # 11a. Recipient logout
        assert client.get("/api/recipient/me", headers=recip_headers).status_code == 200
        logout_resp = client.post("/api/recipient/logout", headers=recip_headers)
        assert logout_resp.status_code == 204
        # Access with revoked token must be rejected
        assert client.get("/api/recipient/me", headers=recip_headers).status_code == 401

        # 11b. Recipient session expiry
        login_again = client.post(
            "/api/recipient/login",
            json={"email": "recipient@example.com", "password": "recipient-password-123"},
        )
        active_headers = {"Authorization": f"Bearer {login_again.json()['access_token']}"}
        assert client.get("/api/recipient/me", headers=active_headers).status_code == 200

        # Simulate session expiration in DB
        with sqlite3.connect(main.DATABASE) as conn:
            conn.execute("UPDATE recipient_sessions SET expires_at='2020-01-01T00:00:00+00:00'")
        assert client.get("/api/recipient/me", headers=active_headers).status_code == 401

        # 11c. Nominee logout and expiry
        nom_resp = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={
                "name": "Trusted Nominee",
                "email": "nominee@example.com",
                "relationship": "Sister",
                "phone": "+1 555 0199",
            },
        )
        nominee_id = nom_resp.json()["nominee"]["id"]
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "nominee", "target_id": nominee_id},
        )
        nom_token_str = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        nom_jwt = client.post(
            "/api/guest/invitations/claim",
            json={"token": nom_token_str, "password": "nominee-password-123"},
        ).json()["access_token"]
        nom_headers = {"Authorization": f"Bearer {nom_jwt}"}

        assert client.get("/api/nominee/me", headers=nom_headers).status_code == 200
        assert client.post("/api/nominee/logout", headers=nom_headers).status_code == 204
        assert client.get("/api/nominee/me", headers=nom_headers).status_code == 401

        # 11d. Owner logout
        assert client.get("/api/auth/me", headers=owner_headers).status_code == 200
        assert client.post("/api/auth/logout", headers=owner_headers).status_code == 204
        assert client.get("/api/auth/me", headers=owner_headers).status_code == 401


def test_pending_document_download_blocked_under_all_conditions(tmp_path, monkeypatch):
    """Verify backend security for assigned documents with 'Pending' release status.
    Direct API download calls must be rejected under all unapproved/hold/unassigned conditions.
    """
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "REVIEWER_EMAIL", "reviewer@example.com")
    reviewer_password = "reviewer-password-123"
    monkeypatch.setattr(
        main,
        "REVIEWER_PASSWORD_HASH",
        main.bcrypt.hashpw(reviewer_password.encode(), main.bcrypt.gensalt()).decode(),
    )
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-msg-id"}),
    )

    with TestClient(main.app) as client:
        # 1. Owner registers and sets up recipient
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recipient_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # 2. Owner uploads the test document
        upload = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "CloudGuard security test document",
                "category": "Legal",
                "recipient_ids_json": json.dumps([recipient_id]),
            },
            files={"file": ("security_test.pdf", b"harmless test document contents", "application/pdf")},
        )
        assert upload.status_code == 201
        document_id = upload.json()["document"]["id"]

        # Also create a document NOT assigned to this recipient
        unassigned_doc = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "Unassigned Vault Document",
                "category": "Personal",
                "recipient_ids_json": json.dumps([]),
            },
            files={"file": ("unassigned.txt", b"secret unassigned", "text/plain")},
        ).json()["document"]["id"]

        # 3. Recipient accepts invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recipient_id},
        )
        invitation_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        claim_resp = client.post(
            "/api/guest/invitations/claim",
            json={"token": invitation_token, "password": "recipient-password-123"},
        )
        assert claim_resp.status_code == 200
        recip_headers = {"Authorization": f"Bearer {claim_resp.json()['access_token']}"}

        # 4. Check document listing: status MUST be "pending", release not approved
        doc_list = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"]
        assert len(doc_list) == 1
        test_doc = doc_list[0]
        assert test_doc["id"] == document_id
        assert test_doc["name"] == "CloudGuard security test document"
        assert test_doc["access_status"] == "pending"
        assert test_doc["released_at"] is None
        assert test_doc["case_approved"] is False

        # Details endpoint verification: release_approved is False
        details = client.get(f"/api/recipient/documents/{document_id}", headers=recip_headers).json()["document"]
        assert details["access_status"] == "pending"
        assert details["released_at"] is None

        # 5. Direct API download call while pending: MUST return 403 Forbidden
        direct_download = client.get(
            f"/api/recipient/documents/{document_id}/download",
            headers=recip_headers,
        )
        assert direct_download.status_code == 403
        assert "locked" in direct_download.json()["detail"].lower()

        # 6. Attempt download via owner endpoint: MUST return 401 Unauthorized
        owner_endpoint_attempt = client.get(
            f"/api/documents/{document_id}/download",
            headers=recip_headers,
        )
        assert owner_endpoint_attempt.status_code == 401

        # 7. Attempt download of unassigned document: MUST return 404 Not Found
        unassigned_attempt = client.get(
            f"/api/recipient/documents/{unassigned_doc}/download",
            headers=recip_headers,
        )
        assert unassigned_attempt.status_code == 404

        # 8. Attempt download of another owner's document: MUST return 404 Not Found
        register(client, "other-owner@example.com")
        other_headers = login(client, "other-owner@example.com")
        other_doc_id = client.post(
            "/api/documents",
            headers=other_headers,
            data={"name": "Other Owner Document", "category": "Financial"},
            files={"file": ("other.txt", b"other owner confidential", "text/plain")},
        ).json()["document"]["id"]

        other_owner_attempt = client.get(
            f"/api/recipient/documents/{other_doc_id}/download",
            headers=recip_headers,
        )
        assert other_owner_attempt.status_code == 404

        # 9. Verify death case condition:
        # Nominee creates case and gets it approved by reviewer, but owner has NOT approved document release
        nom_resp = client.put(
            "/api/nominee",
            headers=owner_headers,
            json={"name": "Nominee", "email": "nominee@example.com", "relationship": "Sister", "phone": "+1 555 0123"},
        )
        nominee_id = nom_resp.json()["nominee"]["id"]
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "nominee", "target_id": nominee_id},
        )
        nom_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        nom_jwt = client.post(
            "/api/guest/invitations/claim",
            json={"token": nom_token, "password": "nominee-password-123"},
        ).json()["access_token"]
        nom_headers = {"Authorization": f"Bearer {nom_jwt}"}

        # Submit case
        case_resp = client.post(
            "/api/nominee/case",
            headers=nom_headers,
            data={"note": "Death certificate submission"},
            files={"evidence": ("cert.pdf", b"%PDF-1.7 test cert", "application/pdf")},
        )
        assert case_resp.status_code == 201, case_resp.text
        case_id = case_resp.json()["case"]["id"]

        # Reviewer approves case
        rev_login = client.post(
            "/api/reviewer/login",
            json={"email": "reviewer@example.com", "password": reviewer_password},
        )
        rev_headers = {"Authorization": f"Bearer {rev_login.json()['access_token']}"}
        client.post(
            f"/api/reviewer/cases/{case_id}/decision",
            headers=rev_headers,
            json={"decision": "approved", "message": "Approved."},
        )

        # Death case is approved, but OWNER RELEASE is still NOT approved!
        # Direct download must STILL be rejected with 403 Forbidden!
        unreleased_download = client.get(
            f"/api/recipient/documents/{document_id}/download",
            headers=recip_headers,
        )
        assert unreleased_download.status_code == 403
        assert "owner approves release" in unreleased_download.json()["detail"].lower()

        # 10. Verify Medical Hold condition:
        # If owner releases the document, but Medical Hold is active:
        client.post(
            f"/api/documents/{document_id}/releases",
            headers=owner_headers,
            json={"recipient_id": recipient_id},
        )
        client.put("/api/medical-hold", headers=owner_headers, json={"action": "start"})

        # Direct download MUST be blocked by Medical Hold (403 Forbidden)!
        hold_download = client.get(
            f"/api/recipient/documents/{document_id}/download",
            headers=recip_headers,
        )
        assert hold_download.status_code == 403
        assert "medical hold" in hold_download.json()["detail"].lower()


def test_owner_live_checkin_and_history_verification(tmp_path, monkeypatch):
    """Verify owner live check-in success, failure handling, scheduling, history, and unauthorized access rejection."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "providers_available", lambda: True)

    provider_status = {
        "face_status": "passed",
        "face_detail": "matched",
        "liveness_status": "passed",
        "speech_status": "passed",
    }
    expected_sentence = "Today I am confirming my CloudGuard check-in."

    def mock_verify_recording(path, samples, sentence):
        assert len(samples) == 5
        assert sentence == expected_sentence
        assert path.read_bytes() == b"valid-checkin-video"
        return provider_status.copy()

    monkeypatch.setattr(main, "verify_recording", mock_verify_recording)

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")

        # 1. Successful owner check-in
        checkin_resp = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": expected_sentence},
            files={"recording": ("clip.webm", b"valid-checkin-video", "video/webm")},
        )
        assert checkin_resp.status_code == 201
        res = checkin_resp.json()
        assert res["passed"] is True
        assert res["checkin"]["status"] == "success"
        assert res["checkin"]["face_status"] == "passed"
        assert res["checkin"]["liveness_status"] == "passed"
        assert res["checkin"]["speech_status"] == "passed"
        assert res["checkin"]["sentence"] == expected_sentence
        assert "All verification checks passed" in res["message"]

        checkin_id = res["checkin"]["id"]
        with sqlite3.connect(main.DATABASE) as connection:
            events = [row[0] for row in connection.execute("SELECT event FROM audit_logs").fetchall()]
        assert f"checkin.verified_passed:{checkin_id}" in events

        # 2. Check-in history records the correct owner and time
        history_resp = client.get("/api/check-ins", headers=owner_headers)
        assert history_resp.status_code == 200
        history = history_resp.json()["checkins"]
        assert len(history) == 1
        assert history[0]["id"] == checkin_id
        assert history[0]["status"] == "success"
        assert history[0]["sentence"] == expected_sentence
        assert history[0]["created_at"] is not None

        # Dashboard uses configured checkin_interval
        dashboard_resp = client.get("/api/dashboard", headers=owner_headers).json()
        assert dashboard_resp["last_checkin"]["status"] == "success"
        assert dashboard_resp["last_checkin"]["mode"] == "local_ai"
        assert dashboard_resp["last_checkin"]["created_at"] is not None
        assert dashboard_resp["next_checkin"] is not None
        expected_due = (
            main._parse_iso_utc(history[0]["created_at"]) + timedelta(days=30)
        ).isoformat()
        assert dashboard_resp["next_checkin"] == expected_due

        # Update checkin_interval in profile to 14 days
        profile_patch = client.patch(
            "/api/profile",
            headers=owner_headers,
            json={"name": "Test User", "phone": "+1 555 0100", "checkin_interval": 14},
        )
        assert profile_patch.status_code == 200
        assert profile_patch.json()["user"]["checkin_interval"] == 14
        updated_dashboard = client.get("/api/dashboard", headers=owner_headers).json()
        new_expected_due = (
            main._parse_iso_utc(history[0]["created_at"]) + timedelta(days=14)
        ).isoformat()
        assert updated_dashboard["next_checkin"] == new_expected_due

        # 3. Failed or incomplete check-in is NOT recorded as successful
        provider_status["face_status"] = "failed"
        provider_status["face_detail"] = "different_person"
        failed_resp = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": expected_sentence},
            files={"recording": ("clip.webm", b"valid-checkin-video", "video/webm")},
        )
        assert failed_resp.status_code == 201
        failed_data = failed_resp.json()
        assert failed_data["passed"] is False
        assert failed_data["checkin"]["status"] == "failed"
        assert failed_data["checkin"]["face_status"] == "failed"
        assert failed_data["checkin"]["face_detail"] == "different_person"

        # History includes the failed event
        history_after_fail = client.get("/api/check-ins", headers=owner_headers).json()["checkins"]
        assert len(history_after_fail) == 2
        assert history_after_fail[0]["status"] == "failed"
        assert history_after_fail[1]["status"] == "success"

        # 4. Invalid requests rejected
        bad_sentence = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": "An arbitrary invented sentence."},
            files={"recording": ("clip.webm", b"valid-checkin-video", "video/webm")},
        )
        assert bad_sentence.status_code == 422

        bad_file = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": expected_sentence},
            files={"recording": ("clip.exe", b"executable", "application/octet-stream")},
        )
        assert bad_file.status_code == 415

        empty_file = client.post(
            "/api/check-ins",
            headers=owner_headers,
            data={"sentence": expected_sentence},
            files={"recording": ("clip.webm", b"", "video/webm")},
        )
        assert empty_file.status_code == 400

        # 5. Unauthorized users cannot perform owner check-ins
        unauth_resp = client.post(
            "/api/check-ins",
            data={"sentence": expected_sentence},
            files={"recording": ("clip.webm", b"valid-checkin-video", "video/webm")},
        )
        assert unauth_resp.status_code == 401

        # A recipient token cannot check in
        _configure_resend(monkeypatch)
        sent_messages = []
        monkeypatch.setattr(
            main.resend.Emails,
            "send",
            lambda msg: (sent_messages.append(msg) or {"id": "resend-id"}),
        )
        recip_id = _create_recipient(client, owner_headers, "recipient@example.com")
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recip_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        claim_resp = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recip-password-123"},
        )
        recip_headers = {"Authorization": f"Bearer {claim_resp.json()['access_token']}"}

        recip_checkin = client.post(
            "/api/check-ins",
            headers=recip_headers,
            data={"sentence": expected_sentence},
            files={"recording": ("clip.webm", b"valid-checkin-video", "video/webm")},
        )
        assert recip_checkin.status_code == 401
        assert "owner sign-in is required" in recip_checkin.json()["detail"].lower()

        # Recipient cannot view check-in history
        assert client.get("/api/check-ins", headers=recip_headers).status_code == 401

        # Another owner only sees their own check-in history
        register(client, "other@example.com")
        other_headers = login(client, "other@example.com")
        other_history = client.get("/api/check-ins", headers=other_headers).json()["checkins"]
        assert len(other_history) == 0


def test_missed_checkins_never_release_documents(tmp_path, monkeypatch):
    """Verify that a missed check-in never automatically releases documents or bypasses death verification."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-id"}),
    )

    with TestClient(main.app) as client:
        register(client, "owner@example.com")
        owner_headers = login(client, "owner@example.com")
        recip_id = _create_recipient(client, owner_headers, "recipient@example.com")

        # Owner uploads document assigned to recipient
        doc_resp = client.post(
            "/api/documents",
            headers=owner_headers,
            data={
                "name": "Estate Planning Document",
                "category": "Legal",
                "recipient_ids_json": json.dumps([recip_id]),
            },
            files={"file": ("estate.pdf", b"%PDF-1.7 confidential estate plan", "application/pdf")},
        )
        assert doc_resp.status_code == 201
        doc_id = doc_resp.json()["document"]["id"]

        # Recipient accepts invitation
        client.post(
            "/api/invitations",
            headers=owner_headers,
            json={"role": "recipient", "target_id": recip_id},
        )
        token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        claim = client.post(
            "/api/guest/invitations/claim",
            json={"token": token, "password": "recip-password-123"},
        )
        recip_headers = {"Authorization": f"Bearer {claim.json()['access_token']}"}

        # 1. Status is pending, direct download is blocked
        listing = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"]
        assert listing[0]["access_status"] == "pending"
        assert client.get(f"/api/recipient/documents/{doc_id}/download", headers=recip_headers).status_code == 403

        # 2. Simulate missed check-in: insert a past check-in from 90 days ago (interval is 30 days)
        past_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        with sqlite3.connect(main.DATABASE) as connection:
            owner_row = connection.execute("SELECT id FROM users WHERE email='owner@example.com'").fetchone()
            connection.execute(
                """INSERT INTO checkins(owner_id,mode,status,face_status,liveness_status,speech_status,sentence,created_at)
                   VALUES(?,'local_ai','success','passed','passed','passed','Today I am confirming my CloudGuard check-in.',?)""",
                (owner_row[0], past_date),
            )

        # Dashboard shows check-in was due 60 days ago
        dash = client.get("/api/dashboard", headers=owner_headers).json()
        assert dash["last_checkin"] is not None
        assert dash["next_checkin"] is not None
        assert main._parse_iso_utc(dash["next_checkin"]) < datetime.now(timezone.utc)

        # 3. Document MUST STILL remain locked and unreleased
        # A missed check-in does NOT automatically declare death, does NOT approve cases, and does NOT release files
        recip_docs = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"]
        assert recip_docs[0]["access_status"] == "pending"
        assert recip_docs[0]["case_approved"] is False

        # Direct download request MUST still be rejected with 403 Forbidden!
        download_attempt = client.get(f"/api/recipient/documents/{doc_id}/download", headers=recip_headers)
        assert download_attempt.status_code == 403
        assert "no death-notification case has been approved" in download_attempt.json()["detail"].lower()


def test_medical_hold_lifecycle_permissions_and_fresh_checkin(tmp_path, monkeypatch):
    """Test full Medical Hold lifecycle, permissions, cross-owner isolation, and fresh check-in requirement."""
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(main, "REVIEWER_EMAIL", "reviewer@example.com")
    reviewer_password = "reviewer-password-123"
    monkeypatch.setattr(
        main,
        "REVIEWER_PASSWORD_HASH",
        main.bcrypt.hashpw(reviewer_password.encode(), main.bcrypt.gensalt()).decode(),
    )
    monkeypatch.setattr(main, "providers_available", lambda: True)

    provider_status = {
        "face_status": "passed",
        "face_detail": "matched",
        "liveness_status": "passed",
        "speech_status": "passed",
    }
    monkeypatch.setattr(
        main,
        "verify_recording",
        lambda _path, _samples, _sentence: provider_status.copy(),
    )

    _configure_resend(monkeypatch)
    sent_messages = []
    monkeypatch.setattr(
        main.resend.Emails,
        "send",
        lambda msg: (sent_messages.append(msg) or {"id": "resend-id"}),
    )

    with TestClient(main.app) as client:
        # Register owner A
        register(client, "owner-a@example.com")
        owner_a_headers = login(client, "owner-a@example.com")
        recip_id = _create_recipient(client, owner_a_headers, "recip@example.com")

        # Upload document
        doc_resp = client.post(
            "/api/documents",
            headers=owner_a_headers,
            data={
                "name": "Life Insurance Policy",
                "category": "Insurance",
                "recipient_ids_json": json.dumps([recip_id]),
            },
            files={"file": ("policy.pdf", b"%PDF-1.7 insurance policy", "application/pdf")},
        )
        doc_id = doc_resp.json()["document"]["id"]

        # Recipient accepts invitation
        client.post(
            "/api/invitations",
            headers=owner_a_headers,
            json={"role": "recipient", "target_id": recip_id},
        )
        recip_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        claim = client.post(
            "/api/guest/invitations/claim",
            json={"token": recip_token, "password": "recip-pass-123"},
        )
        recip_headers = {"Authorization": f"Bearer {claim.json()['access_token']}"}

        # Setup nominee and submit case
        nom_resp = client.put(
            "/api/nominee",
            headers=owner_a_headers,
            json={"name": "Nominee", "email": "nominee@example.com", "relationship": "Brother", "phone": "+1 555 0100"},
        )
        nominee_id = nom_resp.json()["nominee"]["id"]
        client.post(
            "/api/invitations",
            headers=owner_a_headers,
            json={"role": "nominee", "target_id": nominee_id},
        )
        nom_token = sent_messages[-1]["text"].split("#guest-invitation=", 1)[1].splitlines()[0]
        nom_claim = client.post(
            "/api/guest/invitations/claim",
            json={"token": nom_token, "password": "nom-pass-123"},
        )
        nom_headers = {"Authorization": f"Bearer {nom_claim.json()['access_token']}"}

        case_resp = client.post(
            "/api/nominee/case",
            headers=nom_headers,
            data={"note": "Death certificate submitted"},
            files={"evidence": ("cert.pdf", b"%PDF-1.7 death certificate", "application/pdf")},
        )
        case_id = case_resp.json()["case"]["id"]

        # Reviewer approves case
        rev_login = client.post(
            "/api/reviewer/login",
            json={"email": "reviewer@example.com", "password": reviewer_password},
        )
        rev_headers = {"Authorization": f"Bearer {rev_login.json()['access_token']}"}
        client.post(
            f"/api/reviewer/cases/{case_id}/decision",
            headers=rev_headers,
            json={"decision": "approved", "message": "Approved."},
        )

        # Owner approves release
        client.post(
            f"/api/documents/{doc_id}/releases",
            headers=owner_a_headers,
            json={"recipient_id": recip_id},
        )

        # At this point, release conditions are satisfied: document can be downloaded
        assert client.get(f"/api/recipient/documents/{doc_id}/download", headers=recip_headers).status_code == 200

        # 1. Owner A activates Medical Hold
        start_hold = client.put(
            "/api/medical-hold",
            headers=owner_a_headers,
            json={"action": "start"},
        )
        assert start_hold.status_code == 200
        assert start_hold.json()["medical_hold"]["status"] == "active"

        # Verify Medical Hold status is visible on dashboard and get endpoint
        assert client.get("/api/medical-hold", headers=owner_a_headers).json()["medical_hold"]["status"] == "active"
        dash = client.get("/api/dashboard", headers=owner_a_headers).json()
        assert dash["medical_hold"]["status"] == "active"

        # 2. Medical Hold blocks downloads even though death case & owner release were approved!
        blocked_download = client.get(
            f"/api/recipient/documents/{doc_id}/download",
            headers=recip_headers,
        )
        assert blocked_download.status_code == 403
        assert "medical hold" in blocked_download.json()["detail"].lower()

        # Recipient document listing shows status paused
        recip_doc = client.get("/api/recipient/documents", headers=recip_headers).json()["documents"][0]
        assert recip_doc["access_status"] == "pending"
        assert recip_doc["release_hold"] is True

        # Approving releases while hold is active is rejected with 409 Conflict
        assert client.post(
            f"/api/documents/{doc_id}/releases",
            headers=owner_a_headers,
            json={"recipient_id": recip_id},
        ).status_code == 409

        # 3. Unauthorized users cannot change Medical Hold
        assert client.put("/api/medical-hold", json={"action": "start"}).status_code == 401
        assert client.put("/api/medical-hold", headers=recip_headers, json={"action": "start"}).status_code == 401
        assert client.put("/api/medical-hold", headers=nom_headers, json={"action": "start"}).status_code == 401

        # Another owner (Owner B) cannot change Owner A's hold
        register(client, "owner-b@example.com")
        owner_b_headers = login(client, "owner-b@example.com")
        # Owner B ends their own hold (returns 409 because B has no active hold)
        assert client.put("/api/medical-hold", headers=owner_b_headers, json={"action": "end"}).status_code == 409
        # Owner B starts their own hold
        client.put("/api/medical-hold", headers=owner_b_headers, json={"action": "start"})
        # Owner A's hold remains active!
        assert client.get("/api/medical-hold", headers=owner_a_headers).json()["medical_hold"]["status"] == "active"

        # 4. Deactivating Medical Hold does NOT automatically release documents
        end_hold = client.put(
            "/api/medical-hold",
            headers=owner_a_headers,
            json={"action": "end"},
        )
        assert end_hold.status_code == 200
        assert end_hold.json()["medical_hold"]["status"] == "awaiting_checkin"
        assert end_hold.json()["medical_hold"]["ended_at"] is not None

        # Document download MUST STILL be rejected with 403 Forbidden!
        post_end_download = client.get(
            f"/api/recipient/documents/{doc_id}/download",
            headers=recip_headers,
        )
        assert post_end_download.status_code == 403
        assert "medical hold" in post_end_download.json()["detail"].lower()

        # 5. Check-in during awaiting_checkin that FAILS verification does NOT clear the hold
        provider_status["speech_status"] = "failed"
        failed_checkin = client.post(
            "/api/check-ins",
            headers=owner_a_headers,
            data={"sentence": "Today I am confirming my CloudGuard check-in."},
            files={"recording": ("clip.webm", b"test-clip", "video/webm")},
        )
        assert failed_checkin.status_code == 201
        assert failed_checkin.json()["passed"] is False
        assert client.get("/api/medical-hold", headers=owner_a_headers).json()["medical_hold"]["status"] == "awaiting_checkin"
        assert client.get(f"/api/recipient/documents/{doc_id}/download", headers=recip_headers).status_code == 403

        # 6. Fresh live check-in that PASSES verification clears the hold
        provider_status["speech_status"] = "passed"
        passed_checkin = client.post(
            "/api/check-ins",
            headers=owner_a_headers,
            data={"sentence": "Today I am confirming my CloudGuard check-in."},
            files={"recording": ("clip.webm", b"test-clip", "video/webm")},
        )
        assert passed_checkin.status_code == 201
        assert passed_checkin.json()["passed"] is True
        assert client.get("/api/medical-hold", headers=owner_a_headers).json()["medical_hold"]["status"] == "inactive"

        # Now document can be downloaded!
        final_download = client.get(
            f"/api/recipient/documents/{doc_id}/download",
            headers=recip_headers,
        )
        assert final_download.status_code == 200
        assert final_download.content == b"%PDF-1.7 insurance policy"

        # Verify audit logs
        with sqlite3.connect(main.DATABASE) as connection:
            events = [row[0] for row in connection.execute("SELECT event FROM audit_logs").fetchall()]
        assert "medical_hold.started" in events
        assert "medical_hold.ended_awaiting_checkin" in events
        assert "medical_hold.fresh_checkin_completed" in events


def test_face_enrollment_update_and_decryption_failure_handling(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE", tmp_path / "test.db")

    with TestClient(main.app) as client:
        register(client, "owner-biometric@example.com")
        headers = login(client, "owner-biometric@example.com")

        # 1. Unauthorized attempt to update face enrollment is rejected
        unauth_resp = client.put(
            "/api/profile/face-enrollment",
            json={
                "face_enrollment_samples_b64": [
                    base64.b64encode(b"\xff\xd8\xffsample-" + bytes([i])).decode()
                    for i in range(5)
                ]
            },
        )
        assert unauth_resp.status_code == 401

        # 2. Invalid samples (wrong count or bad format) are rejected
        invalid_resp = client.put(
            "/api/profile/face-enrollment",
            headers=headers,
            json={"face_enrollment_samples_b64": ["not-enough"]},
        )
        assert invalid_resp.status_code == 422

        # 3. Successful update of face enrollment
        valid_samples = [
            base64.b64encode(b"\xff\xd8\xffnew-sample-" + bytes([i])).decode()
            for i in range(5)
        ]
        update_resp = client.put(
            "/api/profile/face-enrollment",
            headers=headers,
            json={"face_enrollment_samples_b64": valid_samples},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["message"] == "Face enrollment updated successfully."

        # Verify audit log recorded profile.face_enrollment_updated
        with sqlite3.connect(main.DATABASE) as connection:
            events = [row[0] for row in connection.execute("SELECT event FROM audit_logs").fetchall()]
        assert "profile.face_enrollment_updated" in events

        # 4. Corrupt / mismatched key in face_enrollment returns HTTP 409 (not 500!)
        with sqlite3.connect(main.DATABASE) as connection:
            connection.execute(
                "UPDATE users SET face_enrollment = ? WHERE email = ?",
                (b"corrupted-undecryptable-ciphertext", "owner-biometric@example.com"),
            )

        monkeypatch.setattr(main, "providers_available", lambda: True)
        corrupt_resp = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": "Today I am confirming my CloudGuard check-in."},
            files={"recording": ("clip.webm", b"test-clip-content", "video/webm")},
        )
        assert corrupt_resp.status_code == 409
        assert "re-enroll" in corrupt_resp.json()["detail"].lower()

        # 5. After re-enrolling, check-in succeeds past the decryption step
        re_enroll_resp = client.put(
            "/api/profile/face-enrollment",
            headers=headers,
            json={"face_enrollment_samples_b64": valid_samples},
        )
        assert re_enroll_resp.status_code == 200

        def mock_verify_recording(path, samples, sentence):
            return {
                "face_status": "passed",
                "face_detail": "matched",
                "liveness_status": "passed",
                "speech_status": "passed",
            }

        monkeypatch.setattr(main, "verify_recording", mock_verify_recording)
        checkin_resp = client.post(
            "/api/check-ins",
            headers=headers,
            data={"sentence": "Today I am confirming my CloudGuard check-in."},
            files={"recording": ("clip.webm", b"test-clip-content", "video/webm")},
        )
        assert checkin_resp.status_code == 201
        assert checkin_resp.json()["passed"] is True


