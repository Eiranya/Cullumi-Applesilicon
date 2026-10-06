from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)


MODEL_VERSION = "yunet-2023mar+ocec-c-2025.10:v1"
MODEL_SHA256 = {
    "face_detection_yunet_2023mar.onnx": (
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    ),
    "ocec_c.onnx": (
        "779f6395bab036667f7652dce4e42cf84cb322a4f47600485fe07dddc6905749"
    ),
}
YU_OUTPUT_NAMES = (
    "cls_8",
    "cls_16",
    "cls_32",
    "obj_8",
    "obj_16",
    "obj_32",
    "bbox_8",
    "bbox_16",
    "bbox_32",
    "kps_8",
    "kps_16",
    "kps_32",
)

# ---------------------------------------------------------------------------
# Execution provider (hardware acceleration)
# ---------------------------------------------------------------------------
#
# Blink detection is the heaviest per-photo inference in a scan. On Apple
# Silicon, onnxruntime ships a CoreML execution provider that dispatches to the
# GPU/ANE; measured on an M5 it is ~5.3x faster for YuNet and ~4.0x for the eye
# classifier. It is opt-out rather than opt-in, but every path degrades to the
# CPU provider so a missing or unhappy accelerator can never break a scan.
#
# The resolved provider is folded into `FaceAnalyzer.input_fingerprint` so that
# flipping the preference invalidates cached results. That matters because the
# two providers do NOT agree bit-for-bit, and how far apart they land depends
# heavily on the input: on emoji faces the per-eye open-probability differed by
# 1e-4..5e-3 while the aggregate blink_confidence moved only ~2e-4, and an
# independent harness reproduced a single-eye delta as large as 7e-2. Without
# the tag in the fingerprint, `Scanner.blink_rescan_required` would see an
# unchanged fingerprint, skip the recompute, and leave the database holding a
# silent mix of results from two different providers.
#
# The state below is module-level on purpose. `input_fingerprint` is a
# `@staticmethod` invoked both through an instance and through the class itself
# (see `Scanner.blink_rescan_required`), so it has no instance to read from.
ACCELERATOR_PROVIDER = "CoreMLExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"

_accelerator_preference = True
# Flipped after a real load failure so we stop retrying a provider that cannot
# work on this machine, and so the fingerprint settles on what is actually used.
_accelerator_disabled = False
_provider_probe: list[str] | None = None
_tag_cache: str | None = None


def available_providers() -> list[str]:
    """Report onnxruntime's providers, or [] when it is not importable."""
    global _provider_probe
    if _provider_probe is None:
        try:
            import onnxruntime as ort

            _provider_probe = list(ort.get_available_providers())
        except Exception as error:  # pragma: no cover - import/env failure
            logger.warning("无法读取 onnxruntime 执行后端列表：%s", error)
            _provider_probe = []
    return _provider_probe


def accelerator_usable() -> bool:
    """True when the accelerator is both preferred and actually available."""
    return (
        _accelerator_preference
        and not _accelerator_disabled
        and ACCELERATOR_PROVIDER in available_providers()
    )


def accelerator_tag() -> str:
    """Short tag for the provider inference will use ('coreml' or 'cpu').

    Deliberately computable WITHOUT creating a session: `blink_rescan_required`
    checks cached fingerprints without loading any model.
    """
    global _tag_cache
    if _tag_cache is None:
        _tag_cache = "coreml" if accelerator_usable() else "cpu"
    return _tag_cache


def configure_accelerator(enabled: bool) -> str:
    """Set the process-wide accelerator preference; return the resulting tag.

    Enabling clears any earlier load failure, so an explicit user action always
    gets one fresh attempt -- otherwise the setting could read "on" while
    inference silently stayed on CPU for the rest of the session.

    Takes effect the next time a session is created. Callers that need it
    applied immediately should also call `FaceAnalyzer.reset_sessions()`.
    """
    global _accelerator_preference, _accelerator_disabled, _tag_cache
    _accelerator_preference = bool(enabled)
    if enabled:
        _accelerator_disabled = False
    _tag_cache = None
    return accelerator_tag()


def _demote_accelerator(error: BaseException) -> None:
    """Record that the accelerator failed to load, so we stop choosing it."""
    global _accelerator_disabled, _tag_cache
    _accelerator_disabled = True
    _tag_cache = None
    logger.warning("核心加速不可用，已回退 CPU 推理：%s", error)


def provider_chain() -> list[str]:
    """Provider list for `InferenceSession`, accelerator first when usable."""
    if accelerator_usable():
        return [ACCELERATOR_PROVIDER, CPU_PROVIDER]
    return [CPU_PROVIDER]


@dataclass(frozen=True)
class BlinkThresholds:
    face_confidence_min: float
    open_confidence_min: float
    closed_confidence_min: float
    min_eye_distance_px: int
    reliable_coverage_min: float

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> BlinkThresholds:
        values = profile["similarity"]["blink"]
        return cls(
            face_confidence_min=float(values["face_confidence_min"]),
            open_confidence_min=float(values["open_confidence_min"]),
            closed_confidence_min=float(values["closed_confidence_min"]),
            min_eye_distance_px=int(values["min_eye_distance_px"]),
            reliable_coverage_min=float(values["reliable_coverage_min"]),
        )

    def fingerprint(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class FaceDetection:
    x: float
    y: float
    width: float
    height: float
    landmarks: NDArray[np.float32]
    score: float


def empty_blink_values(status: str = "not_analyzed", error: str = "") -> dict[str, Any]:
    return {
        "blink_status": status,
        "blink_face_count": 0,
        "blink_closed_face_count": 0,
        "blink_uncertain_face_count": 0,
        "blink_closed_ratio": -1.0,
        "blink_confidence": 0.0,
        "blink_model_version": MODEL_VERSION if status != "not_analyzed" else "",
        "blink_input_fingerprint": "",
        "blink_analyzed_at": (
            datetime.now().isoformat(timespec="microseconds")
            if status != "not_analyzed"
            else ""
        ),
        "blink_error": error,
    }


class FaceAnalyzer:
    """Lazy face and eye-state inference over cached thumbnails.

    Inference prefers the CoreML execution provider (GPU/ANE on Apple Silicon)
    and degrades to CPU when it is unavailable or fails to initialise. See the
    module-level provider notes for why the chosen provider is folded into the
    cache fingerprint.
    """

    def __init__(self, model_root: Path):
        self.model_root = model_root
        self.face_model = model_root / "face_detection_yunet_2023mar.onnx"
        self.eye_model = model_root / "ocec_c.onnx"
        self._face_session: Any | None = None
        self._eye_session: Any | None = None
        self._session_lock = threading.RLock()
        self._inference_lock = threading.Lock()

    def reset_sessions(self) -> None:
        """Discard cached sessions so the next call re-resolves the provider.

        Used when the user flips the acceleration preference: the sessions are
        long-lived and would otherwise keep running on the old provider.
        """
        with self._session_lock:
            self._face_session = None
            self._eye_session = None

    def _sessions(self) -> tuple[Any, Any]:
        with self._session_lock:
            if self._face_session is not None and self._eye_session is not None:
                return self._face_session, self._eye_session
            if not self.face_model.is_file() or not self.eye_model.is_file():
                raise RuntimeError("眨眼检测模型缺失")
            for model in (self.face_model, self.eye_model):
                with model.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                if digest != MODEL_SHA256[model.name]:
                    raise RuntimeError(f"眨眼检测模型校验失败：{model.name}")
            try:
                import onnxruntime as ort
            except ImportError as error:
                raise RuntimeError("眨眼检测运行组件未安装") from error
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            self._face_session, self._eye_session = self._create_sessions(ort, options)
            return self._face_session, self._eye_session

    def _create_sessions(self, ort: Any, options: Any) -> tuple[Any, Any]:
        """Build both sessions, degrading to CPU if the accelerator fails.

        A CoreML load can fail for reasons outside the application's control
        (unsupported operator, ANE contention, managed-device policy). Blink
        detection must still work -- only slower -- instead of taking the whole
        scan down, so the failure is recorded and retried once on CPU.

        The fallback is whole-pipeline rather than per-model: a mixed pair
        would make the fingerprint tag dishonest about what actually ran.
        """
        providers = provider_chain()
        if providers[0] == ACCELERATOR_PROVIDER:
            try:
                face_session = ort.InferenceSession(
                    str(self.face_model), sess_options=options, providers=providers
                )
                eye_session = ort.InferenceSession(
                    str(self.eye_model), sess_options=options, providers=providers
                )
            except Exception as error:
                _demote_accelerator(error)
            else:
                logger.info("眨眼检测推理后端：%s", ACCELERATOR_PROVIDER)
                return face_session, eye_session
        return (
            ort.InferenceSession(
                str(self.face_model), sess_options=options, providers=[CPU_PROVIDER]
            ),
            ort.InferenceSession(
                str(self.eye_model), sess_options=options, providers=[CPU_PROVIDER]
            ),
        )

    @staticmethod
    def input_fingerprint(
        thumbnail: Path,
        row: Any,
        thresholds: BlinkThresholds,
    ) -> str:
        stat = thumbnail.stat()
        payload = {
            "thumbnail_size": stat.st_size,
            "thumbnail_mtime_ns": stat.st_mtime_ns,
            "cover_source": row["cover_source"],
            "cover_time_ms": int(row["cover_time_ms"] or 0),
            "cover_revision": int(row["cover_revision"] or 0),
            "model": MODEL_VERSION,
            # Fold the inference backend in: CoreML and CPU agree on the model
            # version but not bit-for-bit on the numbers, so switching providers
            # must invalidate cached verdicts rather than silently reuse them.
            "provider": accelerator_tag(),
            "thresholds": thresholds.fingerprint(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _prepare_canvas(thumbnail: Path) -> Image.Image:
        with Image.open(thumbnail) as source:
            image = source.convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        if image.size != (640, 640):
            scale = min(640 / image.width, 640 / image.height)
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            image.close()
            image = resized
        canvas = Image.new("RGB", (640, 640))
        canvas.paste(image, ((640 - image.width) // 2, (640 - image.height) // 2))
        image.close()
        return canvas

    @staticmethod
    def _iou(left: FaceDetection, right: FaceDetection) -> float:
        x1 = max(left.x, right.x)
        y1 = max(left.y, right.y)
        x2 = min(left.x + left.width, right.x + right.width)
        y2 = min(left.y + left.height, right.y + right.height)
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = left.width * left.height + right.width * right.height - intersection
        return intersection / union if union > 0 else 0.0

    @classmethod
    def decode_yunet(
        cls,
        outputs: dict[str, NDArray[np.float32]],
        score_threshold: float,
        nms_threshold: float = 0.3,
        top_k: int = 10,
    ) -> list[FaceDetection]:
        candidates: list[FaceDetection] = []
        for stride in (8, 16, 32):
            cols = 640 // stride
            class_scores = np.clip(outputs[f"cls_{stride}"][0, :, 0], 0, 1)
            object_scores = np.clip(outputs[f"obj_{stride}"][0, :, 0], 0, 1)
            scores = np.sqrt(class_scores * object_scores)
            indices = np.flatnonzero(scores >= score_threshold)
            boxes = outputs[f"bbox_{stride}"][0]
            landmarks = outputs[f"kps_{stride}"][0]
            for index in indices:
                row, column = divmod(int(index), cols)
                box = boxes[index]
                center_x = (column + float(box[0])) * stride
                center_y = (row + float(box[1])) * stride
                width = math.exp(float(np.clip(box[2], -20, 20))) * stride
                height = math.exp(float(np.clip(box[3], -20, 20))) * stride
                points = landmarks[index].reshape(5, 2).astype(np.float32, copy=True)
                points[:, 0] = (points[:, 0] + column) * stride
                points[:, 1] = (points[:, 1] + row) * stride
                candidates.append(
                    FaceDetection(
                        center_x - width / 2,
                        center_y - height / 2,
                        width,
                        height,
                        points,
                        float(scores[index]),
                    )
                )
        kept: list[FaceDetection] = []
        for candidate in sorted(candidates, key=lambda item: -item.score):
            if all(cls._iou(candidate, existing) <= nms_threshold for existing in kept):
                kept.append(candidate)
                if len(kept) >= top_k:
                    break
        return kept

    @classmethod
    def _detect_faces(
        cls,
        face_session: Any,
        canvas: Image.Image,
        threshold: float,
    ) -> list[FaceDetection]:
        rgb = np.asarray(canvas, dtype=np.float32)
        blob = np.ascontiguousarray(rgb[:, :, ::-1].transpose(2, 0, 1)[None, ...])
        values = face_session.run(list(YU_OUTPUT_NAMES), {"input": blob})
        return cls.decode_yunet(dict(zip(YU_OUTPUT_NAMES, values)), threshold)

    @staticmethod
    def _eye_crop(
        canvas: Image.Image,
        center: NDArray[np.float32],
        eye_distance: float,
        angle: float,
    ) -> NDArray[np.float32]:
        crop_width = max(4, round(eye_distance * 0.70))
        crop_height = max(4, round(eye_distance * 0.50))
        left = round(float(center[0]) - crop_width / 2)
        top = round(float(center[1]) - crop_height / 2)
        patch = canvas.crop((left, top, left + crop_width, top + crop_height))
        try:
            rotated = patch.rotate(-angle, resample=Image.Resampling.BILINEAR)
            try:
                target_width = max(4, round(eye_distance * 0.55))
                target_height = max(4, round(eye_distance * 0.33))
                x = max(0, (rotated.width - target_width) // 2)
                y = max(0, (rotated.height - target_height) // 2)
                eye = rotated.crop((x, y, x + target_width, y + target_height))
                try:
                    resized = eye.resize((40, 24), Image.Resampling.BILINEAR)
                    try:
                        rgb = np.asarray(resized, dtype=np.float32)
                        return np.ascontiguousarray(
                            rgb.transpose(2, 0, 1) / 255
                        )
                    finally:
                        resized.close()
                finally:
                    eye.close()
            finally:
                rotated.close()
        finally:
            patch.close()

    def analyze(
        self,
        thumbnail: Path,
        row: Any,
        profile: dict[str, Any],
        *,
        detailed: bool = False,
    ) -> dict[str, Any]:
        thresholds = BlinkThresholds.from_profile(profile)
        # Resolve the sessions BEFORE the fingerprint. Creating them is what
        # settles which provider is actually usable (a failed accelerator load
        # demotes it), so fingerprinting first could tag a result with a
        # provider it was not computed on.
        face_session, eye_session = self._sessions()
        fingerprint = self.input_fingerprint(thumbnail, row, thresholds)
        with self._inference_lock:
            canvas = self._prepare_canvas(thumbnail)
            try:
                faces = self._detect_faces(
                    face_session, canvas, thresholds.face_confidence_min
                )
                if not faces:
                    result = empty_blink_values("no_face")
                    result["blink_input_fingerprint"] = fingerprint
                    if detailed:
                        result["faces"] = []
                    return result

                eye_inputs: list[NDArray[np.float32]] = []
                face_eye_indices: list[tuple[int, int] | None] = []
                for face in faces:
                    right_eye, left_eye = face.landmarks[0], face.landmarks[1]
                    dx = float(left_eye[0] - right_eye[0])
                    dy = float(left_eye[1] - right_eye[1])
                    distance = math.hypot(dx, dy)
                    if distance < thresholds.min_eye_distance_px:
                        face_eye_indices.append(None)
                        continue
                    angle = math.degrees(math.atan2(dy, dx))
                    first = len(eye_inputs)
                    eye_inputs.append(self._eye_crop(canvas, right_eye, distance, angle))
                    eye_inputs.append(self._eye_crop(canvas, left_eye, distance, angle))
                    face_eye_indices.append((first, first + 1))

                probabilities = np.empty((0,), dtype=np.float32)
                if eye_inputs:
                    batch = np.asarray(eye_inputs, dtype=np.float32)
                    probabilities = np.asarray(
                        eye_session.run(["prob_open"], {"images": batch})[0]
                    ).reshape(-1)

                closed_count = 0
                uncertain_count = 0
                evidence: list[float] = []
                observations: list[dict[str, Any]] = []
                for face, indices in zip(faces, face_eye_indices):
                    observation = {
                        "x": round(face.x, 3),
                        "y": round(face.y, 3),
                        "width": round(face.width, 3),
                        "height": round(face.height, 3),
                        "face_confidence": round(face.score, 6),
                        "eye_open_probabilities": [],
                        "status": "uncertain",
                        "confidence": 0.0,
                    }
                    if indices is None:
                        uncertain_count += 1
                        observations.append(observation)
                        continue
                    eye_probabilities = [float(probabilities[index]) for index in indices]
                    observation["eye_open_probabilities"] = [
                        round(value, 6) for value in eye_probabilities
                    ]
                    closed_evidence = [1 - probability for probability in eye_probabilities]
                    if any(
                        confidence >= thresholds.closed_confidence_min
                        for confidence in closed_evidence
                    ):
                        closed_count += 1
                        confidence = min(face.score, max(closed_evidence))
                        evidence.append(confidence)
                        observation.update({
                            "status": "closed",
                            "confidence": round(confidence, 6),
                        })
                    elif all(
                        probability >= thresholds.open_confidence_min
                        for probability in eye_probabilities
                    ):
                        confidence = min(face.score, min(eye_probabilities))
                        evidence.append(confidence)
                        observation.update({
                            "status": "open",
                            "confidence": round(confidence, 6),
                        })
                    else:
                        uncertain_count += 1
                    observations.append(observation)

                reliable_count = len(faces) - uncertain_count
                ratio = closed_count / reliable_count if reliable_count else -1.0
                status = (
                    "closed" if closed_count
                    else "open" if reliable_count == len(faces)
                    else "uncertain"
                )
                result = {
                    "blink_status": status,
                    "blink_face_count": len(faces),
                    "blink_closed_face_count": closed_count,
                    "blink_uncertain_face_count": uncertain_count,
                    "blink_closed_ratio": round(ratio, 6),
                    "blink_confidence": round(min(evidence), 6) if evidence else 0.0,
                    "blink_model_version": MODEL_VERSION,
                    "blink_input_fingerprint": fingerprint,
                    "blink_analyzed_at": datetime.now().isoformat(timespec="microseconds"),
                    "blink_error": "",
                }
                if detailed:
                    result["faces"] = observations
                return result
            finally:
                canvas.close()

    def analyze_detailed(
        self,
        thumbnail: Path,
        row: Any,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Return aggregate fields plus per-face observations for evaluation."""
        return self.analyze(thumbnail, row, profile, detailed=True)


__all__ = [
    "ACCELERATOR_PROVIDER",
    "BlinkThresholds",
    "CPU_PROVIDER",
    "FaceAnalyzer",
    "FaceDetection",
    "MODEL_SHA256",
    "MODEL_VERSION",
    "YU_OUTPUT_NAMES",
    "accelerator_tag",
    "accelerator_usable",
    "available_providers",
    "configure_accelerator",
    "empty_blink_values",
    "provider_chain",
]
