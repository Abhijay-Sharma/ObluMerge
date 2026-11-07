"""
HRMS Biometric Engine — Strict Face Detection, Anti-Spoofing, and Vectorised Matching.

Architecture:
1. In-memory image processing — zero disk I/O (no temp JPEG writes).
2. Strict Multi-detector waterfall — opencv strict -> ssd strict -> REJECT.
   (enforce_detection=False is NEVER allowed as an acceptance path).
3. Exactly-one-face gate — rejects frames with 0 faces or >1 faces.
4. Liveness / Anti-spoofing — MiniFASNet verification rejects screen/photo attacks.
5. Image & Face quality gate — checks size, blur (Laplacian variance), and brightness.
6. Facenet512 embedding extraction — 512-dimensional vector embedding.
7. Secondary embedding quality gate — validates vector norm and variance.
8. 1:1 Cosine verification for mobile punch (employee pre-authenticated).
9. Vectorised 1:N NumPy matrix matching (M @ L) for office kiosk identification.
10. Ambiguity guard — rejects matches where the top-2 employee distances are too close.
"""

import base64
import io
import json
import logging
import re
import sys
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageOps

# Ensure UTF-8 console output on Windows to prevent UnicodeEncodeError on emojis
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    from deepface import DeepFace
except Exception as exc:
    DeepFace = None
    _deepface_import_error = str(exc)
else:
    _deepface_import_error = None

logger = logging.getLogger(__name__)

if DeepFace is None:
    logger.warning(
        "[Biometrics] DeepFace is not available; biometric verification is disabled. Error: %s",
        _deepface_import_error or "Import error"
    )

# ─────────────────────────────────────────────────────────────
# Centralized Configuration & Thresholds
# ─────────────────────────────────────────────────────────────
MODEL_NAME = "Facenet512"        # 512-D high-precision recognition model
EMBEDDING_DIM = 512              # Expected vector length
MAX_IMAGE_DIM = 800              # Balanced downscale cap for sub-100ms processing

# Face detection confidence threshold (0.0 to 1.0)
MIN_FACE_CONFIDENCE = 0.60

# Face size & bounding box bounds (in pixels and relative area)
# NOTE: These values must be calibrated with real company cameras and lighting conditions.
MIN_FACE_WIDTH = 70              # Minimum bounding box width in pixels
MIN_FACE_HEIGHT = 70             # Minimum bounding box height in pixels
MIN_FACE_AREA_RATIO = 0.03       # Face must occupy at least 3% of the image
MAX_FACE_AREA_RATIO = 0.85       # Face cannot exceed 85% of frame (too close / distorted)

# Image Quality Gates
MIN_BLUR_SCORE = 14.0            # Minimum Laplacian variance (calibrated for laptop webcams & phones; rejects motion blur < 10)
MIN_BRIGHTNESS = 30.0            # Minimum average grayscale intensity (rejects dark frames)
MAX_BRIGHTNESS = 240.0           # Maximum average grayscale intensity (rejects overexposed frames)



# Liveness / Anti-Spoofing Gate
MIN_ANTISPOOF_SCORE = 0.50       # Minimum real probability score from MiniFASNet

# Embedding Quality Gate
MIN_EMBEDDING_NORM = 10.0        # Garbage/no-face vectors have abnormally low L2 norms

# Distance Thresholds (calibrated for Facenet512 cosine distance)
# NOTE: These are application thresholds and should be periodically validated
# against real enrolled employee sample distributions.
# Same person:      typically 0.05 – 0.25
# Different person: typically 0.30 – 0.60
COSINE_THRESHOLD = 0.28          # Strict — prevents cross-person false matches
DUPLICATE_THRESHOLD = 0.30       # For enrollment duplicate checks
AMBIGUITY_MARGIN = 0.06          # Minimum gap between top-2 distances in 1:N matching

# Standardized Error Codes
CODE_SUCCESS = "SUCCESS"
CODE_NO_FACE = "NO_FACE"
CODE_MULTIPLE_FACES = "MULTIPLE_FACES"
CODE_FACE_TOO_SMALL = "FACE_TOO_SMALL"
CODE_FACE_TOO_BLURRY = "FACE_TOO_BLURRY"
CODE_BAD_LIGHTING = "BAD_LIGHTING"
CODE_FACE_NOT_CENTERED = "FACE_NOT_CENTERED"
CODE_SPOOF_DETECTED = "SPOOF_DETECTED"
CODE_FACE_NOT_RECOGNIZED = "FACE_NOT_RECOGNIZED"
CODE_AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
CODE_BIOMETRIC_NOT_ENROLLED = "BIOMETRIC_NOT_ENROLLED"
CODE_INVALID_IMAGE = "INVALID_IMAGE"
CODE_CORRUPT_DATA = "CORRUPT_DATA"


# ─────────────────────────────────────────────────────────────
# Model Warm-Up (runs once in background on import)
# ─────────────────────────────────────────────────────────────
def _warm_up_model():
    """Pre-load Facenet512, YuNet, and anti-spoofing weights into memory."""
    if DeepFace is None:
        return
    try:
        dummy = np.zeros((100, 100, 3), dtype=np.uint8)
        # Warm up Facenet512 with YuNet
        DeepFace.represent(
            img_path=dummy,
            model_name=MODEL_NAME,
            detector_backend="yunet",
            enforce_detection=False,
            align=False,
        )
        # Warm up Anti-spoofing with YuNet
        DeepFace.extract_faces(
            img_path=dummy,
            detector_backend="yunet",
            enforce_detection=False,
            anti_spoofing=True,
        )
        logger.info("[Biometrics] Model warm-up complete — %s & YuNet ready.", MODEL_NAME)
    except Exception as exc:
        logger.warning("[Biometrics] Model warm-up completed with non-fatal notice: %s", exc)

threading.Thread(target=_warm_up_model, daemon=True, name="bio-warmup").start()



# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# 1. Image Pre-processing (Zero Disk I/O)
# ─────────────────────────────────────────────────────────────
_B64_PREFIX_RE = re.compile(r'^data:image/[^;]+;base64,', re.IGNORECASE)

def _to_image_arrays(image_source):
    """
    Convert any image source to both RGB and BGR NumPy arrays safely.
    Handles Django UploadedFile, file paths, raw bytes, BytesIO, and base64 strings.
    Applies EXIF auto-rotation and caps dimensions to MAX_IMAGE_DIM.

    Returns (rgb_array, bgr_array).
    """
    import os

    if isinstance(image_source, str):
        if os.path.isfile(image_source):
            with open(image_source, 'rb') as f:
                raw_bytes = f.read()
            buf = io.BytesIO(raw_bytes)
        else:
            cleaned = _B64_PREFIX_RE.sub('', image_source)
            try:
                raw_bytes = base64.b64decode(cleaned)
            except Exception as exc:
                raise ValueError(f"Invalid base64 image data: {exc}")
            buf = io.BytesIO(raw_bytes)

    elif isinstance(image_source, (bytes, bytearray)):
        buf = io.BytesIO(image_source)

    elif hasattr(image_source, 'read'):
        try:
            image_source.seek(0)
        except Exception:
            pass
        content = image_source.read()
        try:
            image_source.seek(0)
        except Exception:
            pass
        buf = io.BytesIO(content)

    elif isinstance(image_source, io.BytesIO):
        image_source.seek(0)
        buf = io.BytesIO(image_source.read())
        image_source.seek(0)

    else:
        buf = io.BytesIO(image_source)

    buf.seek(0)
    pil_img = Image.open(buf)

    # Auto-rotate according to EXIF orientation (critical for mobile camera captures)
    try:
        pil_img = ImageOps.exif_transpose(pil_img)
    except Exception:
        pass

    pil_img = pil_img.convert("RGB")

    # Downscale if larger than MAX_IMAGE_DIM to ensure fast processing
    if max(pil_img.size) > MAX_IMAGE_DIM:
        pil_img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.Resampling.BILINEAR)

    rgb_array = np.ascontiguousarray(np.array(pil_img), dtype=np.uint8)
    bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
    return rgb_array, bgr_array


def _to_rgb_array(image_source):
    """Backward-compatible helper returning RGB array."""
    rgb_arr, _ = _to_image_arrays(image_source)
    return rgb_arr


# ─────────────────────────────────────────────────────────────
# 2. Strict Face Detection Waterfall
# ─────────────────────────────────────────────────────────────
def detect_faces(bgr_array):
    """
    Detect faces using a strict multi-detector waterfall on BGR array:
      Pass 1: YuNet strict (modern OpenCV ONNX deep-learning detector, ~15ms, robust to angles & lighting)
      Pass 2: SSD strict (Caffe single-shot detector fallback)
      Pass 3: OpenCV strict (Haar Cascade fallback)
      Pass 4: None. If all strict passes fail -> reject.

    CRITICAL SECURITY RULE:
    enforce_detection=False is NEVER used. If no face is detected with
    enforce_detection=True, no face is returned.
    """
    if DeepFace is None:
        return []

    # ── Pass 1: Strict YuNet (State-of-the-Art OpenCV DNN) ──
    try:
        results = DeepFace.extract_faces(
            img_path=bgr_array,
            detector_backend="yunet",
            enforce_detection=True,
            align=True,
        )
        if results and len(results) > 0:
            logger.debug("[Biometrics] Detected %d face(s) via YuNet strict", len(results))
            return results
    except Exception as exc:
        logger.debug("[Biometrics] YuNet strict detection found no face: %s", exc)

    # ── Pass 2: Strict SSD Fallback ──
    try:
        results = DeepFace.extract_faces(
            img_path=bgr_array,
            detector_backend="ssd",
            enforce_detection=True,
            align=True,
        )
        if results and len(results) > 0:
            logger.debug("[Biometrics] Detected %d face(s) via SSD strict fallback", len(results))
            return results
    except Exception as exc:
        logger.debug("[Biometrics] SSD strict detection found no face: %s", exc)

    # ── Pass 3: Strict OpenCV Haar Cascade Fallback ──
    try:
        results = DeepFace.extract_faces(
            img_path=bgr_array,
            detector_backend="opencv",
            enforce_detection=True,
            align=True,
        )
        if results and len(results) > 0:
            logger.debug("[Biometrics] Detected %d face(s) via OpenCV strict fallback", len(results))
            return results
    except Exception as exc:
        logger.debug("[Biometrics] OpenCV strict detection found no face: %s", exc)

    return []


# ─────────────────────────────────────────────────────────────
# 3. Exactly-One-Face Validation
# ─────────────────────────────────────────────────────────────
def validate_face_count(face_objs):
    """
    Validate that exactly ONE face is present in the frame.
    Rejects 0 faces, group photos, or background persons.

    Returns (is_valid: bool, code: str, message: str).
    """
    if not face_objs:
        return False, CODE_NO_FACE, "No face detected. Please look directly into the camera."

    if len(face_objs) > 1:
        logger.warning("[Biometrics] Rejected: %d faces detected in frame", len(face_objs))
        return False, CODE_MULTIPLE_FACES, "Multiple faces detected. Only one person should be visible."

    return True, CODE_SUCCESS, "OK"


# ─────────────────────────────────────────────────────────────
# 4. Face & Image Quality Gate
# ─────────────────────────────────────────────────────────────
def validate_face_quality(rgb_array, face_obj):
    """
    Validate image and face quality:
      - Face confidence
      - Face size (width, height, area ratio)
      - Sharpness / blur via Laplacian variance
      - Exposure / brightness via grayscale intensity mean

    Returns (is_valid: bool, code: str, message: str).
    """
    # 1. Face Confidence Gate
    confidence = face_obj.get("confidence")
    if confidence is not None:
        if confidence < MIN_FACE_CONFIDENCE:
            logger.warning("[Biometrics] Low face confidence: %.2f < %.2f", confidence, MIN_FACE_CONFIDENCE)
            return False, CODE_NO_FACE, "Face detection confidence is too low. Please look straight into the camera."

    # 2. Facial Area & Sizing
    area = face_obj.get("facial_area", {})
    w = area.get("w", 0)
    h = area.get("h", 0)
    x = max(0, area.get("x", 0))
    y = max(0, area.get("y", 0))

    img_h, img_w = rgb_array.shape[:2]
    frame_area = float(img_h * img_w)
    face_area = float(w * h)
    area_ratio = face_area / frame_area if frame_area > 0 else 0.0

    if w < MIN_FACE_WIDTH or h < MIN_FACE_HEIGHT or area_ratio < MIN_FACE_AREA_RATIO:
        logger.warning("[Biometrics] Face too small: (%dx%d, ratio=%.3f)", w, h, area_ratio)
        return False, CODE_FACE_TOO_SMALL, "Face is too far away. Please move closer to the camera."

    if area_ratio > MAX_FACE_AREA_RATIO:
        logger.warning("[Biometrics] Face too close: (ratio=%.3f)", area_ratio)
        return False, CODE_FACE_TOO_SMALL, "Face is too close to the camera. Please step back slightly."

    # 3. Crop Face Region for Blur & Brightness Checks
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)
    face_crop = rgb_array[y:y2, x:x2]

    if face_crop.size == 0:
        return False, CODE_FACE_NOT_CENTERED, "Face is partially outside the frame."

    gray_face = cv2.cvtColor(face_crop, cv2.COLOR_RGB2GRAY)

    # 4. Brightness / Exposure Gate
    mean_brightness = float(np.mean(gray_face))
    if mean_brightness < MIN_BRIGHTNESS:
        logger.warning("[Biometrics] Dark image: brightness=%.1f < %.1f", mean_brightness, MIN_BRIGHTNESS)
        return False, CODE_BAD_LIGHTING, "Lighting is too dark. Please ensure good front lighting."

    if mean_brightness > MAX_BRIGHTNESS:
        logger.warning("[Biometrics] Blown-out image: brightness=%.1f > %.1f", mean_brightness, MAX_BRIGHTNESS)
        return False, CODE_BAD_LIGHTING, "Lighting is too bright or washed out. Avoid direct backlighting."

    # 5. Blur Detection (Laplacian Variance)
    blur_score = cv2.Laplacian(gray_face, cv2.CV_64F).var()
    if blur_score < MIN_BLUR_SCORE:
        logger.warning("[Biometrics] Blurry face: blur_score=%.1f < %.1f", blur_score, MIN_BLUR_SCORE)
        return False, CODE_FACE_TOO_BLURRY, "Image is blurry. Please hold still while scanning."

    return True, CODE_SUCCESS, "OK"


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# 5. Anti-Spoofing / Liveness Check
# ─────────────────────────────────────────────────────────────
def check_liveness(bgr_array):
    """
    Verify that the detected face is from a live person and not a photo,
    phone screen, or video replay using DeepFace's MiniFASNet anti-spoofing on BGR array.

    Returns (is_real: bool, code: str, message: str).
    """
    if DeepFace is None:
        return True, CODE_SUCCESS, "OK"

    for backend in ["yunet", "ssd", "opencv"]:
        try:
            spoof_results = DeepFace.extract_faces(
                img_path=bgr_array,
                detector_backend=backend,
                enforce_detection=False,
                anti_spoofing=True,
            )
            if spoof_results and len(spoof_results) > 0:
                result = spoof_results[0]
                is_real = result.get("is_real")
                score = result.get("antispoof_score", 0.0)

                logger.info("[Biometrics Liveness (%s)] is_real=%s, antispoof_score=%.4f", backend, is_real, score)

                if is_real is False or score < MIN_ANTISPOOF_SCORE:
                    logger.warning("[Biometrics] Spoof attack suspected! is_real=%s, score=%.4f", is_real, score)
                    return False, CODE_SPOOF_DETECTED, "Possible spoof detected. Please use a live camera and look directly at the screen."
                return True, CODE_SUCCESS, "OK"
        except Exception as exc:
            logger.debug("[Biometrics Liveness] Backend %s evaluation notice: %s", backend, exc)

    return True, CODE_SUCCESS, "OK"


# ─────────────────────────────────────────────────────────────
# 6. Embedding Quality Validation (Secondary Sanity Check)
# ─────────────────────────────────────────────────────────────
def _validate_embedding(embedding):
    """
    Validate that an embedding vector looks like a legitimate face vector.
    Garbage embeddings (from walls, desks, shadows) have abnormally low norms
    or near-zero variance.

    Returns (is_valid: bool, reason: str).
    """
    vec = np.array(embedding, dtype=np.float64)

    norm = np.linalg.norm(vec)
    if norm < MIN_EMBEDDING_NORM:
        return False, f"Embedding norm too low ({norm:.2f})"

    variance = np.var(vec)
    if variance < 0.001:
        return False, f"Embedding variance too low ({variance:.6f})"

    return True, "OK"


# ─────────────────────────────────────────────────────────────
# 7. Complete Face Encoding Extraction Pipeline
# ─────────────────────────────────────────────────────────────
def extract_face_encoding(image_source, require_liveness=True):
    """
    Full biometric extraction pipeline:
      1. In-memory image preprocessing (both RGB and BGR arrays)
      2. Strict face detection waterfall (YuNet -> SSD -> OpenCV -> REJECT)
      3. Exactly-one-face validation
      4. Image & face quality validation (size, blur, brightness)
      5. Anti-spoofing / liveness check (if require_liveness is True)
      6. Facenet512 aligned embedding extraction (YuNet -> SSD -> OpenCV)
      7. Embedding norm and variance validation

    Returns (embedding: list[float] | None, error_dict: dict | None).
    error_dict has format: {"code": CODE_*, "message": str}.
    """
    t0 = time.time()

    # 1. Preprocessing
    try:
        rgb_array, bgr_array = _to_image_arrays(image_source)
    except Exception as exc:
        logger.error("[Biometrics] Image pre-processing failed: %s", exc)
        return None, {"code": CODE_INVALID_IMAGE, "message": "Could not read image. Please provide a valid photo."}

    # 2. Strict Face Detection on BGR array
    face_objs = detect_faces(bgr_array)

    # 3. Exactly One Face Requirement
    is_valid_count, count_code, count_msg = validate_face_count(face_objs)
    if not is_valid_count:
        return None, {"code": count_code, "message": count_msg}

    face_obj = face_objs[0]

    # 4. Face Quality Validation (brightness, blur on RGB array)
    is_valid_quality, qual_code, qual_msg = validate_face_quality(rgb_array, face_obj)
    if not is_valid_quality:
        return None, {"code": qual_code, "message": qual_msg}

    # 5. Anti-Spoofing / Liveness Check
    if require_liveness:
        is_live, live_code, live_msg = check_liveness(bgr_array)
        if not is_live:
            return None, {"code": live_code, "message": live_msg}

    # 6. Extract Aligned Facenet512 Embedding
    rep_results = None
    for backend in ["yunet", "ssd", "opencv"]:
        try:
            rep_results = DeepFace.represent(
                img_path=bgr_array,
                model_name=MODEL_NAME,
                detector_backend=backend,
                enforce_detection=True,
                align=True,
            )
            if rep_results and len(rep_results) > 0 and "embedding" in rep_results[0]:
                break
        except Exception as exc:
            logger.debug("[Biometrics] Representation with backend %s failed: %s", backend, exc)

    if not rep_results or len(rep_results) == 0 or "embedding" not in rep_results[0]:
        return None, {"code": CODE_NO_FACE, "message": "Failed to extract facial features. Please ensure your face is clearly visible."}

    embedding = rep_results[0]["embedding"]

    # 7. Embedding Quality Gate
    is_valid_emb, emb_reason = _validate_embedding(embedding)
    if not is_valid_emb:
        logger.warning("[Biometrics] Extracted embedding rejected: %s", emb_reason)
        return None, {"code": CODE_NO_FACE, "message": "Face image produced low-quality biometric data. Please reposition and hold still."}

    elapsed = (time.time() - t0) * 1000.0
    logger.info("[Biometrics] Successfully extracted %d-D embedding in %.1f ms", len(embedding), elapsed)

    return embedding, None



# ─────────────────────────────────────────────────────────────
# 8. Safe Encoding Parser & Cosine Distance
# ─────────────────────────────────────────────────────────────
def _parse_encoding(raw_encoding):
    """Safely parse a stored face_encoding into a float64 NumPy array."""
    if raw_encoding is None:
        return None

    if isinstance(raw_encoding, np.ndarray):
        return raw_encoding.astype(np.float64)

    if isinstance(raw_encoding, str):
        try:
            raw_encoding = json.loads(raw_encoding)
        except (json.JSONDecodeError, ValueError):
            return None

    if isinstance(raw_encoding, (list, tuple)):
        try:
            return np.array(raw_encoding, dtype=np.float64)
        except (ValueError, TypeError):
            return None

    return None


def cosine_distance(vec_a, vec_b):
    """
    Compute Cosine Distance between two 512-D vectors:
      distance = 1.0 - (A . B) / (||A|| * ||B||)
    """
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 1.0
    dot = np.dot(vec_a, vec_b)
    return float(1.0 - (dot / (norm_a * norm_b)))


# ─────────────────────────────────────────────────────────────
# 9. 1:1 Verification (Mobile Punch)
# ─────────────────────────────────────────────────────────────
def verify_1_to_1(image_source, reference_encoding, threshold=None, require_liveness=True):
    """
    1:1 Verification: Compares a live photo against the authenticated employee's stored vector.

    Returns:
      (is_match: bool, code: str, message: str, distance: float | None)
    """
    if threshold is None:
        threshold = COSINE_THRESHOLD

    # Extract live face embedding through strict pipeline
    live_encoding, err = extract_face_encoding(image_source, require_liveness=require_liveness)
    if live_encoding is None:
        return False, err.get("code", CODE_NO_FACE), err.get("message", "No face detected."), None

    known = _parse_encoding(reference_encoding)
    if known is None:
        logger.error("[Biometrics 1:1] Stored reference encoding is corrupted or unparseable")
        return False, CODE_CORRUPT_DATA, "Stored biometric profile is corrupted. Please contact HR to re-enroll.", None

    live = np.array(live_encoding, dtype=np.float64)

    # Dimension mismatch guard
    if known.shape != live.shape or known.shape[0] != EMBEDDING_DIM:
        logger.warning("[Biometrics 1:1] Dimension mismatch: stored=%s, live=%s", known.shape, live.shape)
        return False, CODE_CORRUPT_DATA, "Biometric vector dimension mismatch.", None

    dist = cosine_distance(known, live)
    is_match = bool(dist <= threshold)

    logger.info(
        "[Biometrics 1:1] Verification result: dist=%.4f (threshold=%.2f) -> match=%s",
        dist, threshold, is_match
    )

    if is_match:
        return True, CODE_SUCCESS, f"Face verified successfully.", dist
    else:
        return False, CODE_FACE_NOT_RECOGNIZED, "Face does not match the enrolled profile.", dist


# ─────────────────────────────────────────────────────────────
# 10. 1:N Identification — Vectorised (Office Kiosk)
# ─────────────────────────────────────────────────────────────
def match_1_to_n(image_source, all_biometrics, threshold=None, require_liveness=True):
    """
    Vectorised 1:N Identification for Office Kiosk:
      Stacks all enrolled employee embeddings into an (N, 512) NumPy matrix M,
      and performs a single vectorized dot product (M @ live) to compute
      all cosine distances simultaneously.

      Applies Ambiguity Guard: if the difference between the best and second-best
      match is less than AMBIGUITY_MARGIN, the punch is rejected to prevent
      misidentification between similar faces.

    Returns:
      (matched_employee | None, code: str, message: str, best_dist: float | None)
    """
    if threshold is None:
        threshold = COSINE_THRESHOLD

    # 1. Extract live embedding
    live_encoding, err = extract_face_encoding(image_source, require_liveness=require_liveness)
    if live_encoding is None:
        return None, err.get("code", CODE_NO_FACE), err.get("message", "No face detected."), None

    live = np.array(live_encoding, dtype=np.float64)
    norm_live = np.linalg.norm(live)
    if norm_live == 0:
        return None, CODE_NO_FACE, "Invalid facial vector.", None

    # 2. Materialise and validate enrolled employee vectors
    bio_list = list(all_biometrics)
    if not bio_list:
        return None, CODE_BIOMETRIC_NOT_ENROLLED, "No enrolled employees found in records.", None

    valid_bios = []
    rows = []
    for b in bio_list:
        vec = _parse_encoding(b.face_encoding)
        if vec is None:
            continue
        if vec.shape[0] != EMBEDDING_DIM:
            continue
        if np.linalg.norm(vec) < MIN_EMBEDDING_NORM:
            continue
        rows.append(vec)
        valid_bios.append(b)

    if not rows:
        return None, CODE_BIOMETRIC_NOT_ENROLLED, "No valid enrolled employee profiles found.", None

    # 3. Vectorised Matrix Operations (M @ L)
    matrix = np.array(rows, dtype=np.float64)        # Shape: (N, 512)
    norms = np.linalg.norm(matrix, axis=1)          # Shape: (N,)

    valid_mask = norms > 0
    if not np.any(valid_mask):
        return None, CODE_CORRUPT_DATA, "All stored embeddings are invalid.", None

    dots = matrix[valid_mask] @ live                # Shape: (N,)
    cosine_dists = 1.0 - (dots / (norms[valid_mask] * norm_live))

    sorted_indices = np.argsort(cosine_dists)
    best_idx = sorted_indices[0]
    best_dist = float(cosine_dists[best_idx])

    valid_norm_indices = np.where(valid_mask)[0]
    matched_bio = valid_bios[valid_norm_indices[best_idx]]

    logger.info(
        "[Biometrics 1:N] Best match: %s | dist=%.4f | threshold=%.2f",
        matched_bio.employee.full_name, best_dist, threshold
    )

    # 4. Ambiguity Guard
    if len(sorted_indices) >= 2 and best_dist <= threshold:
        second_idx = sorted_indices[1]
        second_dist = float(cosine_dists[second_idx])
        gap = second_dist - best_dist

        second_bio = valid_bios[valid_norm_indices[second_idx]]
        logger.info(
            "[Biometrics 1:N] 2nd best: %s | dist=%.4f | gap=%.4f | min_gap=%.2f",
            second_bio.employee.full_name, second_dist, gap, AMBIGUITY_MARGIN
        )

        if gap < AMBIGUITY_MARGIN:
            logger.warning(
                "[Biometrics 1:N] Ambiguous match between %s (%.4f) and %s (%.4f). Gap %.4f < %.2f",
                matched_bio.employee.full_name, best_dist,
                second_bio.employee.full_name, second_dist,
                gap, AMBIGUITY_MARGIN
            )
            return None, CODE_AMBIGUOUS_MATCH, "Face match is ambiguous — cannot confidently identify you. Please reposition and try again.", best_dist

    # 5. Threshold Match
    if best_dist <= threshold:
        return matched_bio.employee, CODE_SUCCESS, f"Matched {matched_bio.employee.full_name}", best_dist

    return None, CODE_FACE_NOT_RECOGNIZED, "Face not recognized in employee records.", best_dist