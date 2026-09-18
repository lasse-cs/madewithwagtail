"""Tests for consent/banner suppression in the browser capture.

These test the constants and the contract that capture_screenshot wires
up — the real Chromium behavior is exercised manually against live sites
(and was against londonmuseum.org.uk, whose Cookiebot dialog motivated
this feature).
"""

import io
import subprocess
import sys
import types

from PIL import Image

from pipeline.browser import (
    BANNER_HIDE_CSS,
    CONSENT_INIT_JS,
    DISMISS_OVERLAYS_JS,
    OVERLAY_PROBE_JS,
    capture_screenshot,
)


class TestBannerHideCss:
    """BANNER_HIDE_CSS is a bare selector list interpolated into one rule."""

    def test_is_pure_selector_list(self):
        # The list is substituted into `CSS = \`%s { display: none !important; }\``.
        # Stray braces or backticks would corrupt that rule or the template
        # literal around it.
        assert "{" not in BANNER_HIDE_CSS
        assert "}" not in BANNER_HIDE_CSS
        assert "`" not in BANNER_HIDE_CSS
        assert "${" not in BANNER_HIDE_CSS

    def test_interpolated_into_hidden_rule(self):
        # Regression: the rule wrapper lives in the init script — the list
        # alone (no declaration block) is invalid CSS that Chromium drops.
        assert "{ display: none !important; }" in CONSENT_INIT_JS
        assert BANNER_HIDE_CSS.strip() in CONSENT_INIT_JS

    def test_covers_major_cmp_containers(self):
        for selector in (
            "#onetrust-consent-sdk",
            "#CybotCookiebotDialog",
            "#usercentrics-root",
            "#iubenda-cs-banner",
            "#cmplz-cookiebanner-container",
            "#cky-consent-bar",
            ".cc-window",
            ".cookie-banner",
        ):
            assert selector in BANNER_HIDE_CSS

    def test_generic_cookie_class_fallback(self):
        # Generic attribute-selector fallback, scoped to banner-capable
        # container elements (unscoped it would hide recipe grids on food
        # blogs). The i flag covers CamelCase class names. Matches
        # wagtail.org's <div class="cookie"> banner. <dialog> and
        # dialog-role containers are hidden unconditionally instead — there
        # is deliberately no dialog[class*="cookie"] selector.
        for tag in ("div", "section", "aside", "footer", "header"):
            assert f'{tag}[class*="cookie" i]' in BANNER_HIDE_CSS
            assert f'{tag}[id*="cookie" i]' in BANNER_HIDE_CSS
        assert 'dialog, [role="dialog"]' in BANNER_HIDE_CSS

    def test_no_selector_hides_body_or_html(self):
        # Hiding <body> would blank the whole screenshot. Selectors like
        # ".cookie-consent:not(body):not(html)" carry explicit guards.
        without_comment = BANNER_HIDE_CSS.split("*/", 1)[1]
        selectors = [s.strip() for s in without_comment.split(",") if s.strip()]
        assert selectors  # list is not empty
        for selector in selectors:
            assert selector not in ("body", "html", "*"), selector
            assert not selector.startswith(("body ", "html ", "body>", "html>")), selector


class TestConsentInitJs:
    """The init script must be parseable JS that installs the stylesheet."""

    def test_css_was_interpolated(self):
        # Regression: an unformatted %s produces a JS SyntaxError that
        # kills the whole init script silently.
        assert "%s" not in CONSENT_INIT_JS
        assert "CybotCookiebotDialog" in CONSENT_INIT_JS

    def test_defers_install_and_bails_without_root(self):
        # Regression: the CSS hide-style is only the fallback for the JS
        # overlay-dismissal pass — the capture driver installs it explicitly
        # (and re-installs after same-origin reloads), so it must be deferred
        # behind window.__mww_install_banner_css rather than run at init
        # time. The install itself must bail instead of throwing when the
        # document has no head/html yet.
        assert "window.__mww_install_banner_css = () => {" in CONSENT_INIT_JS
        assert "if (!root) return;" in CONSENT_INIT_JS
        assert '"__mww_banner_hide"' in CONSENT_INIT_JS
        # No self-scheduling: installation is driven by the capture flow.
        assert "setInterval" not in CONSENT_INIT_JS
        assert "DOMContentLoaded" not in CONSENT_INIT_JS

    def test_parses_as_javascript(self, tmp_path):
        # node --check rejects --eval, so parse from a temp module file.
        # Wrapping in a function body defers execution while still parsing.
        script = tmp_path / "init_script.mjs"
        script.write_text(f"(() => {{ {CONSENT_INIT_JS} }})")
        result = subprocess.run(
            ["node", "--check", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_style_id_is_stable(self):
        assert "style.id = ID;" in CONSENT_INIT_JS
        assert '"__mww_banner_hide"' in CONSENT_INIT_JS


class _FakePlaywright:
    """Records the context/page calls capture_screenshot must make."""

    def __init__(self, png: bytes):
        self.png = png
        self.events = []

    class chromium:
        _instance = None

        @staticmethod
        def launch():
            return _FakePlaywright.chromium._instance

    class _Context:
        def __init__(self, pw):
            self.pw = pw

        def route(self, pattern, guard):
            self.pw.events.append(("route", pattern))

        def add_init_script(self, script):
            self.pw.events.append(("init", script))

        def new_page(self):
            self.pw.events.append(("new_page",))
            return self.pw.page

        def close(self):
            self.pw.events.append(("context_close",))

    class _Page:
        def __init__(self, pw):
            self.pw = pw

        def goto(self, url, **kwargs):
            self.pw.events.append(("goto", url))

        def wait_for_timeout(self, ms):
            self.pw.events.append(("wait", ms))

        def wait_for_load_state(self, state, **kwargs):
            self.pw.events.append(("load_state", state))

        def evaluate(self, js, *args):
            self.pw.events.append(("evaluate", js))
            # Values the driver acts on: the settle loop's mutation probe
            # must report no pending mutations, and the dismissal script's
            # probe result is dereferenced with .get() — it must be a dict.
            if "__mww_mutations" in js:
                return 0
            return {}

        def on(self, event, handler):
            self.pw.events.append(("on", event))

        def screenshot(self, **kwargs):
            self.pw.events.append(("screenshot", kwargs))
            return self.pw.png

    class _Browser:
        def __init__(self, pw):
            self.pw = pw

        def new_context(self, **kwargs):
            self.pw.events.append(("new_context", kwargs))
            return _FakePlaywright._Context(self.pw)

        def close(self):
            self.pw.events.append(("browser_close",))


class TestCaptureScreenshotContract:
    """capture_screenshot must arm the init script and disable animations."""

    def test_arms_init_script_and_disables_animations(self, monkeypatch, tmp_path):
        png_buf = io.BytesIO()
        Image.new("RGB", (1200, 996), "#123456").save(png_buf, "PNG")

        pw = _FakePlaywright(png_buf.getvalue())
        pw.page = _FakePlaywright._Page(pw)
        _FakePlaywright.chromium._instance = _FakePlaywright._Browser(pw)

        # capture_screenshot imports playwright.sync_api inside the
        # function; stub the module so the suite can run without the
        # real (heavy) dependency installed.
        sync_api_stub = types.ModuleType("playwright.sync_api")
        sync_api_stub.sync_playwright = lambda: _nullcontext(pw)
        playwright_stub = types.ModuleType("playwright")
        playwright_stub.sync_api = sync_api_stub
        monkeypatch.setitem(sys.modules, "playwright", playwright_stub)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_stub)

        out = tmp_path / "shot.webp"
        capture_screenshot("https://example.com/", out)

        events = pw.events
        kinds = [e[0] for e in events]

        # Setup order: context (with the capture viewport), route guard,
        # init script, then the page.
        assert kinds[0] == "new_context"
        assert ("route", "**/*") in events
        assert kinds.index("route") < kinds.index("init") < kinds.index("new_page")
        assert (
            "goto",
            "https://example.com/",
        ) in events  # the URL as given, before any redirect handling
        init_event = next(e for e in events if e[0] == "init")
        assert init_event[1] == CONSENT_INIT_JS
        context_kwargs = events[0][1]
        assert context_kwargs["viewport"] == {"width": 1200, "height": 996}
        assert context_kwargs["device_scale_factor"] == 1

        def evaluate_indices(fragment):
            return [
                i
                for i, event in enumerate(events)
                if event[0] == "evaluate" and fragment in event[1]
            ]

        # The page is settled first (network activity is tracked), then
        # interaction proceeds: nav blocker before the overlay probe, the
        # dismissal script, and finally the CSS-hide fallback install.
        assert ("on", "request") in events
        goto_idx = kinds.index("goto")
        nav_blocker = evaluate_indices("__mww_nav_blocked")
        assert nav_blocker and nav_blocker[0] > goto_idx
        probe_install = next(
            i
            for i, event in enumerate(events)
            if event[0] == "evaluate" and event[1] == OVERLAY_PROBE_JS
        )
        assert probe_install > nav_blocker[0]
        dismiss = next(
            i
            for i, event in enumerate(events)
            if event[0] == "evaluate" and event[1] == DISMISS_OVERLAYS_JS
        )
        assert dismiss > probe_install
        css_install = evaluate_indices("__mww_install_banner_css")
        assert css_install and css_install[0] > dismiss

        screenshot_event = next(e for e in events if e[0] == "screenshot")
        assert screenshot_event[1].get("type") == "png"
        assert screenshot_event[1].get("animations") == "disabled"

        assert kinds.index("context_close") < kinds.index("browser_close")
        out_img = Image.open(out)
        assert out_img.format == "WEBP"
        assert out_img.size == (1200, 996)


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc):
        return False
