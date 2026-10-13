import base64
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
