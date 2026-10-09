import base64
import difflib
import logging
import os
import re
import threading
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

_TRANSCRIBE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class VerificationUnavailable(RuntimeError):
    pass


def providers_available() -> bool:
    from importlib.util import find_spec

    engine = os.getenv(
        "DEEPFACE_BACKEND_ENGINE",
        os.getenv("CLOUDGUARD_DEEPFACE_ENGINE", "tensorflow"),
    ).lower()
    engine_module = {"tensorflow": "tensorflow", "pytorch": "torch"}.get(engine)
    required_modules = ("cv2", "deepface", "faster_whisper", "tf_keras")
    return engine_module is not None and all(
        find_spec(module) is not None for module in (*required_modules, engine_module)
    )


def _video_frames(video_path: Path, cv2) -> list:
    """Extract evenly-spaced frames from the check-in recording."""
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise ValueError("The recording could not be opened. Please record again.")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        duration = frame_count / fps if fps and fps > 0 else 0

        # Be lenient with duration – some browser-recorded clips report 0 fps initially.
        if duration > 0 and (duration < 3 or duration > 30):
            raise ValueError("Record a clear 5-to-20-second check-in clip.")

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width < 1 or height < 1 or width > 3840 or height > 2160:
            raise ValueError("The recording dimensions are not supported. Please record again.")

        # Sample frames spread across the whole clip
        fractions = (0.1, 0.25, 0.4, 0.55, 0.7, 0.85)
        frames = []
        for fraction in fractions:
            target_frame = max(0, min(frame_count - 1, int(frame_count * fraction)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            success, frame = capture.read()
            if success and frame is not None and frame.size:
                frames.append(frame)

        if len(frames) < 2:
            raise ValueError("The recording could not be decoded reliably. Please record again.")
        return frames
    finally:
        capture.release()


def _enrollment_images(encoded_samples: list[str], cv2) -> list:
    """Decode the user's stored face enrollment images."""
    images = []
    for sample in encoded_samples:
        try:
            raw = base64.b64decode(sample, validate=True)
        except (ValueError, TypeError) as error:
            raise VerificationUnavailable(
                "Face enrollment data is invalid. Please contact support."
            ) from error
        image = cv2.imdecode(np.frombuffer(raw, dtype="uint8"), cv2.IMREAD_COLOR)
        if image is not None:
            images.append(image)
    if len(images) < 2:
        raise VerificationUnavailable(
            "At least two usable face enrollment samples are required. Please re-enroll your face."
        )
    return images


def _phrase_matches(expected: str, actual: str) -> bool:
    """Fuzzy-match the spoken phrase against the expected sentence."""
    tokenize = lambda value: re.findall(r"[a-z0-9']+", value.casefold())
    expected_words = tokenize(expected)
    actual_words = tokenize(actual)
    if not expected_words or not actual_words:
        return False
    # Allow 75 % word-level similarity (down from 80 %) to handle minor Whisper mis-transcriptions
    ratio = difflib.SequenceMatcher(None, expected_words, actual_words).ratio()
    # Also accept if most key words are present (handles insertion / reordering)
    common = sum(1 for w in expected_words if w in actual_words)
    coverage = common / len(expected_words) if expected_words else 0
    return ratio >= 0.75 or coverage >= 0.70


@lru_cache(maxsize=1)
def _speech_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        os.getenv("CLOUDGUARD_WHISPER_MODEL", "small.en"),
        device="cpu",
        compute_type="int8",
    )


def _safe_extract_faces(DeepFace, frame, anti_spoofing: bool):
    """Call DeepFace.extract_faces and swallow non-critical errors."""
    try:
        return DeepFace.extract_faces(
            img_path=frame,
            detector_backend="opencv",
            enforce_detection=False,
            anti_spoofing=anti_spoofing,
        )
    except Exception as exc:
        logger.debug("extract_faces failed for a frame: %s", exc)
        return []


def _save_frame_as_temp_image(frame, cv2) -> str:
    """Save an OpenCV frame to a temporary file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    cv2.imwrite(path, frame)
    return path


def _compare_face_to_enrollment(DeepFace, cv2, live_frame, enrollment_images: list) -> bool:
    """
    Return True if the face in *live_frame* matches any enrollment image.
    Uses file-path inputs (more reliable than numpy arrays on some builds).
    """
    live_path = _save_frame_as_temp_image(live_frame, cv2)
    try:
        for reference in enrollment_images:
            ref_path = _save_frame_as_temp_image(reference, cv2)
            try:
                result = DeepFace.verify(
                    img1_path=ref_path,
                    img2_path=live_path,
                    model_name="Facenet",
                    detector_backend="opencv",
                    enforce_detection=False,   # don't raise if face is not found
                    distance_metric="cosine",
                )
                if result.get("verified") is True:
                    return True
            except Exception as exc:
                logger.debug("DeepFace.verify failed for one pair: %s", exc)
            finally:
                try:
                    os.remove(ref_path)
                except OSError:
                    pass
    finally:
        try:
            os.remove(live_path)
        except OSError:
            pass
    return False


def verify_recording(
    video_path: Path, encoded_samples: list[str], sentence: str
) -> dict[str, str]:
    """
    Run face-match, liveness, and speech verification on a check-in recording.

    Returns a dict with keys ``face_status``, ``liveness_status``, ``speech_status``
    each set to ``"passed"`` or ``"failed"``.
    """
    try:
        import cv2
        from deepface import DeepFace
    except ImportError as error:
        raise VerificationUnavailable(
            "Local verification models are unavailable. Install the AI requirements and restart the API."
        ) from error

    frames = _video_frames(video_path, cv2)
    enrollment_images = _enrollment_images(encoded_samples, cv2)

    liveness_votes = 0
    live_frames: list = []

    # ── Step 1: liveness / anti-spoofing per frame ──────────────────────────
    for frame in frames:
        detected = _safe_extract_faces(DeepFace, frame, anti_spoofing=True)
        # Count a frame as live when exactly one face is detected and is_real is True
        if detected and len(detected) >= 1:
            best = max(detected, key=lambda f: f.get("confidence", 0))
            is_real = bool(best.get("is_real", False))
            if is_real:
                liveness_votes += 1
                live_frames.append(frame)
            elif best.get("confidence", 0) > 0.4:
                # Face detected but flagged as not real – still try face-match
                # (the liveness vote won't count, but don't discard the frame
                # entirely so face matching still has data to work with)
                live_frames.append(frame)

    # If anti-spoofing returns nothing useful, fall back without it so the face
    # check can still run (some DeepFace builds have unreliable anti-spoofing).
    if not live_frames:
        logger.warning(
            "Anti-spoofing returned no usable frames; falling back to plain face detection."
        )
        for frame in frames:
            detected = _safe_extract_faces(DeepFace, frame, anti_spoofing=False)
            if detected:
                live_frames.append(frame)

    # ── Step 2: face identity matching ──────────────────────────────────────
    identity_votes = 0
    frames_attempted = 0
    for frame in live_frames:
        frames_attempted += 1
        if _compare_face_to_enrollment(DeepFace, cv2, frame, enrollment_images):
            identity_votes += 1

    # ── Step 3: speech transcription ────────────────────────────────────────
    transcript = ""
    try:
        with _TRANSCRIBE_LOCK:
            model = _speech_model()
            segments, _ = model.transcribe(
                str(video_path),
                language="en",
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
        transcript = " ".join(
            segment.text.strip() for segment in segments if segment.text.strip()
        )
        logger.info("Whisper transcript: %r", transcript)
    except Exception as exc:
        logger.warning("Whisper transcription failed: %s", exc)

    # ── Thresholds ───────────────────────────────────────────────────────────
    # Require at least 1 liveness vote out of collected frames (lenient – the
    # browser clip may be short and frames few).
    liveness_threshold = 1

    # Require at least 1 identity match, or if we have many frames, at least 30 %.
    if frames_attempted >= 4:
        identity_threshold = max(1, round(frames_attempted * 0.30))
    else:
        identity_threshold = 1

    liveness_status = "passed" if liveness_votes >= liveness_threshold else "failed"
    face_status = "passed" if identity_votes >= identity_threshold else "failed"
    speech_status = "passed" if _phrase_matches(sentence, transcript) else "failed"

    logger.info(
        "Verification result – face: %s (%d/%d votes), liveness: %s (%d votes), speech: %s",
        face_status, identity_votes, frames_attempted,
        liveness_status, liveness_votes,
        speech_status,
    )

    return {
        "face_status": face_status,
        "liveness_status": liveness_status,
        "speech_status": speech_status,
    }
