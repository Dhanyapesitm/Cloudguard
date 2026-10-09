import base64
import json
import os
import secrets
import sqlite3

os.environ.setdefault("CLOUDGUARD_JWT_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("CLOUDGUARD_FILE_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("CLOUDGUARD_ENV", "development")

from fastapi.testclient import TestClient

import main
from verification import _phrase_matches


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
        assert history[1]["status"] == "success"


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
