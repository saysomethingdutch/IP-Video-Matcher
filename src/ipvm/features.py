from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Protocol

import numpy as np
from PIL import Image


class ImageEncoder(Protocol):
    name: str
    version: str

    def encode_images(self, images: Iterable[np.ndarray]) -> np.ndarray: ...

    def cache_identity(self) -> dict[str, Any]: ...


@lru_cache(maxsize=4)
def _dct_matrix(size: int) -> np.ndarray:
    positions = np.arange(size, dtype=np.float32)
    frequencies = positions[:, None]
    matrix = np.cos(np.pi * (2 * positions + 1) * frequencies / (2 * size)).astype(np.float32)
    matrix[0] *= np.sqrt(1 / size)
    matrix[1:] *= np.sqrt(2 / size)
    return matrix


class DCTVisualEncoder:
    """Small deterministic Phase-1 descriptor behind the visual encoder interface."""

    name = "dct-low-frequency"
    version = "1"

    def __init__(self, image_size: int = 32, coefficient_size: int = 12):
        self.image_size = image_size
        self.coefficient_size = coefficient_size

    def _encode_one(self, image: np.ndarray) -> np.ndarray:
        resized = Image.fromarray(image).resize(
            (self.image_size, self.image_size), Image.Resampling.LANCZOS
        )
        values = np.asarray(resized, dtype=np.float32) / 255.0
        values -= values.mean()
        scale = values.std()
        if scale > 1e-6:
            values /= scale
        transform = _dct_matrix(self.image_size)
        coefficients = transform @ values @ transform.T
        low = coefficients[: self.coefficient_size, : self.coefficient_size].copy()
        low[0, 0] = 0.0
        vector = low.reshape(-1)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-8 else vector

    def encode_images(self, images: Iterable[np.ndarray]) -> np.ndarray:
        vectors = [self._encode_one(image) for image in images]
        dimension = self.coefficient_size**2
        if not vectors:
            return np.empty((0, dimension), dtype=np.float32)
        return np.asarray(vectors, dtype=np.float32)

    def cache_identity(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "image_size": self.image_size,
            "coefficient_size": self.coefficient_size,
            "normalization": "mean-center-standardize-l2",
            "resize": "pillow-lanczos",
        }
