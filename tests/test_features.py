from __future__ import annotations

import numpy as np

from ipvm.features import DCTVisualEncoder


def test_encoder_is_deterministic_and_normalized() -> None:
    image = np.arange(160 * 90, dtype=np.uint8).reshape(90, 160)
    encoder = DCTVisualEncoder()
    features = encoder.encode_images([image, image.copy()])
    assert features.shape == (2, 144)
    assert np.allclose(features[0], features[1])
    assert np.isclose(np.linalg.norm(features[0]), 1.0)


def test_encoder_handles_empty_input() -> None:
    assert DCTVisualEncoder().encode_images([]).shape == (0, 144)
