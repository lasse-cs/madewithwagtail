import io

import pytest
from PIL import Image

from pipeline.images import assert_webp, encode_logo, encode_screenshot


def make_png(size: tuple[int, int], color=(120, 40, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestEncodeScreenshot:
    def test_exact_dimensions(self):
        out = encode_screenshot(make_png((1200, 996)))
        img = assert_webp(out)
        assert img.size == (1200, 996)

    def test_upscales_smaller_input(self):
        out = encode_screenshot(make_png((800, 600)))
        assert assert_webp(out).size == (1200, 996)

    def test_crops_larger_input(self):
        out = encode_screenshot(make_png((2400, 2000)))
        assert assert_webp(out).size == (1200, 996)

    def test_output_is_small(self):
        # Solid-color input must compress far below the cap.
        assert len(encode_screenshot(make_png((1200, 996)))) < 20_000

    def test_over_large_rejects(self):
        # A cap of 0 bytes forces the size rejection path.
        with pytest.raises(ValueError, match="too large"):
            encode_screenshot(make_png((1200, 996)), max_bytes=0)


class TestEncodeLogo:
    def test_max_dimension(self):
        out = encode_logo(make_png((512, 256)))
        img = assert_webp(out)
        assert max(img.size) == 120
        assert img.size == (120, 60)  # aspect preserved

    def test_small_input_untouched_dimension(self):
        out = encode_logo(make_png((64, 64)))
        assert assert_webp(out).size == (64, 64)


class TestAssertWebp:
    def test_rejects_non_webp(self):
        with pytest.raises(ValueError, match="WEBP"):
            assert_webp(make_png((10, 10)))
