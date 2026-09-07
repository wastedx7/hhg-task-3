"""Face detection + encoding using InsightFace (SCRFD detection + ArcFace embeddings)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np

FACE_MODEL = os.environ.get("FACEFINDER_FACE_MODEL", "buffalo_l")


@dataclass
class FaceInfo:
    """A single detected face with its biometric encoding."""

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 (pixel coords, clipped to image)
    det_score: float
    embedding: np.ndarray          # L2-normalised float32 [512]
    embedding_hex: str             # raw float32 bytes as hex (tamper-evidence payload)
    embedding_norm: float          # norm of the un-normalised embedding
    kps: list                       # 5 keypoints [[x,y], ...]


class FaceScanner:
    """Detects faces in an image and produces 512-d biometric encodings."""

    def __init__(self, model_name: str = FACE_MODEL, det_size: tuple[int, int] = (640, 640)):
        self.model_name = model_name
        self.det_size = det_size
        self._app = None

    # -- lazy heavy imports so CLI help / verify stays fast -----------------
    def _get_app(self):
        if self._app is None:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name=self.model_name, providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=0, det_size=self.det_size)
            self._app = app
        return self._app

    @staticmethod
    def load_bgr(path: str) -> np.ndarray:
        img = cv2.imread(path)
        if img is None:
            raise ValueError(f"could not read image: {path}")
        return img

    def detect(self, img_bgr: np.ndarray, min_score: float = 0.3) -> list[FaceInfo]:
        app = self._get_app()
        h, w = img_bgr.shape[:2]
        faces = app.get(img_bgr)
        out: list[FaceInfo] = []
        for face in faces:
            if float(face.det_score) < min_score:
                continue
            emb = np.asarray(face.embedding, dtype=np.float32)
            norm = float(np.linalg.norm(emb))
            normed = emb / norm if norm > 0 else emb
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 16 or y2 - y1 < 16:
                continue
            out.append(
                FaceInfo(
                    bbox=(x1, y1, x2, y2),
                    det_score=float(face.det_score),
                    embedding=normed,
                    embedding_hex=normed.astype("<f4").tobytes().hex(),
                    embedding_norm=norm,
                    kps=face.kps.tolist() if hasattr(face, "kps") else [],
                )
            )
        out.sort(key=lambda f: f.det_score, reverse=True)
        return out

    def process(self, img_bgr: np.ndarray, min_score: float = 0.3) -> tuple[FaceInfo | None, list[FaceInfo]]:
        faces = self.detect(img_bgr, min_score=min_score)
        return (faces[0] if faces else None, faces)

    def crop_face(self, img_bgr: np.ndarray, face: FaceInfo, margin_frac: float = 0.4) -> np.ndarray:
        """Crop the face region with extra margin (padds/reclips to the image)."""
        h, w = img_bgr.shape[:2]
        x1, y1, x2, y2 = face.bbox
        mw = int((x2 - x1) * margin_frac)
        mh = int((y2 - y1) * margin_frac)
        cx1, cy1 = max(0, x1 - mw), max(0, y1 - mh)
        cx2, cy2 = min(w, x2 + mw), min(h, y2 + mh)
        return img_bgr[cy1:cy2, cx1:cx2]


def cropped_jpeg(img_bgr: np.ndarray, quality: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("could not encode crop as JPEG")
    return buf.tobytes()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))