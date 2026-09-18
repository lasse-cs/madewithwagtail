"""Screenshot/logo encoding through Pillow, with size caps."""

from __future__ import annotations

import io

from PIL import Image

SCREENSHOT_MAX_BYTES = 250_000
LOGO_MAX_BYTES = 50_000


def cover_crop(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    """Resize to cover `target`, then center-crop to exactly `target`."""
    tw, th = target
    scale = max(tw / img.width, th / img.height)
    resized = img.resize(
        (round(img.width * scale), round(img.height * scale)), Image.LANCZOS
    )
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def encode_screenshot(
    data: bytes,
    *,
    target: tuple[int, int] = (1200, 996),
    quality: int = 70,
    max_bytes: int = SCREENSHOT_MAX_BYTES,
) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = cover_crop(img, target)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    out = buf.getvalue()
    if len(out) > max_bytes:
        raise ValueError(
            f"Screenshot encodes to {len(out)} bytes, too large (over the {max_bytes} byte cap)"
        )
    return out


def encode_logo(
    data: bytes, *, max_size: int = 120, max_bytes: int = LOGO_MAX_BYTES
) -> bytes:
    img = Image.open(io.BytesIO(data))
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80, method=6)
    out = buf.getvalue()
    if len(out) > max_bytes:
        raise ValueError(
            f"Logo encodes to {len(out)} bytes, too large (over the {max_bytes} byte cap)"
        )
    return out


def assert_webp(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    if img.format != "WEBP":
        raise ValueError(f"Expected WEBP image, got {img.format}")
    return img
