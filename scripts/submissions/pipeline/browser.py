"""Headless-Chromium screenshots with cookie-banner/overlay suppression."""

from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .images import encode_screenshot
from .net import _is_browsable_ip


def is_private_browser_host(url: str) -> bool:
    """True if the URL points at a private/loopback host literal.

    Used by the Playwright route guard. Hostnames cannot be resolved
    here, so only IP literals and 'localhost' are classified;
    hostname-based SSRF is backstopped by the credential-free container.
    """
    host = (urlsplit(url).hostname or "").rstrip(".")
    if not host:
        return False
    try:
        return not _is_browsable_ip(ipaddress.ip_address(host))
    except ValueError:
        return host.casefold() == "localhost"


# ---------------------------------------------------------------------------
# JS-based overlay dismissal (dialog / cookie-banner / popup agnostic).
#
# Runs inside the page after load, in tiers — each tier only if the previous
# left an overlay in place — so we disturb pages as little as possible:
#   1. Escape key (the standard, human way to dismiss dialogs)
#   2. Close affordances inside dialog/[role=dialog] containers
#   3. CMP accept/reject buttons (selectors from mozilla/cookie-banner-rules-list)
#   4. Text-matched accept/dismiss/close buttons in visible banners
#   5. Native <dialog>.close() as a last resort
#
# The style-based hiding (BANNER_HIDE_CSS) only runs afterwards, as a
# fallback for banners our interaction heuristics cannot dismiss — measured
# by OVERLAY_PROBE_JS, which counts remaining overlays by category.

BANNER_PROBE_SELECTOR = (
    "#onetrust-consent-sdk, #onetrust-banner-sdk, #optanon-popup-bg, "
    "#CybotCookiebotDialog, #usercentrics-root, #fc-consent-root, "
    "#cookiebanner, #cookie-banner, .cookie-banner, #cookie_consent, .cookie_consent, "
    "#qc-cmp2-container, #qc-cmp2-ui, #truste-consent-track, #truste-consent-content, "
    "#Osano-CookieDialog, .osano-cm-window, #termly-code-snippet-support, "
    ".termly-consent-banner, #iubenda-cs-banner, .iubenda-cs-banner, "
    "#cmplz-cookiebanner-container, .cmplz-cookiebanner, #cookie-script, "
    "#cookiescript_injected, #klaro, .klaro, #tarteaucitronRoot, "
    "#tarteaucitronAlertBig, #BorlabsCookieBox, #cky-consent-bar, .cky-consent-bar, "
    "#cky-overlay, .cky-overlay, #cc-banner, .cc-window, .cc-banner, "
    "#cookiebanner, #cookie-banner, #cookie_consent, .cookie_consent, "
    "#cookie-law-info-bar, #cliSettingsPopup, .cli-modal-backdrop, "
    "#moove_gdpr_cookie_info_bar, #moove_gdpr_cookie_modal, "
    "#gdpr-cookie-message, .gdpr-cookie-notice, #gdpr-banner, "
    ".eu-cookie-compliance-banner, .js-cookie-banner, .cookie-notice, "
    ".cookie-consent-banner, .cookie-popup, #cookie-box, #cookie-bar, "
    "#cookie-hint, #cookiehint, #cookies-banner, .cookies-banner, "
    "#cookie-message, .cookies-eu-banner, #cookie-law-banner, #cookiesck, "
    ".sqs-cookie-banner-v2, .wpgdprc-consent-bar, .avia-cookie-consent-wrap, "
    ".fusion-privacy-bar, .woodmart-cookies-popup, .thb-cookie-bar, "
    ".pum-overlay, .elementor-popup-modal"
)
OVERLAY_PROBE_JS = """\
(() => {
  const visible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  };
  // Filled, non-transparent box painted by the page: a scrim/spinner/backdrop.
  const painted = (el) => {
    if (!(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (style.backgroundColor === "rgba(0, 0, 0, 0)" || style.backgroundImage === "none") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  };
  const inViewport = (el) => {
    const rect = el.getBoundingClientRect();
    return (
      rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth
    );
  };
  const isOpenDialog = (el) => {
    if (el instanceof HTMLDialogElement) return el.open;
    if (el.getAttribute("role") === "dialog" || el.getAttribute("aria-modal") === "true") {
      return visible(el);
    }
    return false;
  };
  const Dialog = "[dialog],[role='dialog'],[aria-modal='true'],div[class*='modal' i],div[class*='overlay' i],div[class*='popup' i]";
  const spinner = "[class*='spinner' i],[class*='loading' i],[class*='preloader' i],[aria-busy='true'],img[alt*='loading' i],img[src*='loading' i],img[src*='spinner' i]";
  window.__mww_overlay_probe = () => ({
    dialog: [...document.querySelectorAll(Dialog)].filter((el) => isOpenDialog(el) && inViewport(el)).length,
    banner: [...document.querySelectorAll("%BANNER_PROBE_SELECTOR%")].filter((el) => visible(el) && inViewport(el)).length,
    spinner: [...document.querySelectorAll(spinner)].filter((el) => visible(el)).length,
    scrim: [...document.querySelectorAll("body *")].filter((el) => {
      const rect = el.getBoundingClientRect();
      const covers = rect.width >= window.innerWidth * 0.95 && rect.height >= window.innerHeight * 0.95;
      return covers && painted(el) && !el.closest(Dialog) && !el.matches(spinner);
    }).length,
  });
  return window.__mww_overlay_probe();
})()
"""

OVERLAY_PROBE_JS = OVERLAY_PROBE_JS.replace(
    "%BANNER_PROBE_SELECTOR%", BANNER_PROBE_SELECTOR
)

DISMISS_CLICK_SELECTORS = (
    "[role='dialog'] [aria-label*='close' i], [aria-modal='true'] [aria-label*='close' i], "
    "dialog[open] [aria-label*='close' i], "
    "[role='dialog'] [data-dismiss], [aria-modal='true'] [data-dismiss], dialog[open] [data-dismiss], "
    "[role='dialog'] [data-close], [aria-modal='true'] [data-close], dialog[open] [data-close], "
    "[role='dialog'] button.close, [aria-modal='true'] button.close, dialog[open] button.close, "
    "[role='dialog'] .modal-close, [aria-modal='true'] .modal-close, dialog[open] .modal-close, "
    "#onetrust-accept-btn-handler, .ot-pc-refuse-all-handler, #onetrust-reject-all-handler, "
    "#onetrust-banner-sdk .onetrust-close-btn-ui, #onetrust-pc-sdk .onetrust-close-btn-ui, "
    "button#didomi-notice-agree-button, button#didomi-notice-disagree-button, "
    ".didomi-continue-without-agreeing, #didomi-host .didomi-button, "
    "button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, "
    "button#CybotCookiebotDialogBodyButtonDecline, #CybotCookiebotDialogBodyButtons, "
    "button#truste-consent-button, button#truste-consent-required, #truste-consent-track button, "
    "button.fc-cta-consent, button.fc-cta-do-not-consent, .fc-cta-consent, "
    "button#almacmp-modalConfirmBtn, button.iubenda-cs-accept-btn, .iubenda-cs-accept-btn, "
    "a.cc-dismiss, .cc-button, .sp_choice_type_11, .sp_choice_type_ACCEPT, "
    "button#_evidon-accept-button, button#_evidon-decline-button, "
    "button.osano-cm-accept-all, .osano-cm-accept-all, .osano-cm-denyAll, "
    "#cmpwelcomebtnyes a, #cmpwelcomebtnno a, a.cmptxt_btn_yes, "
    "#popin_tc_privacy_button_2, #popin_tc_privacy_button_3, "
    "#gdpr-banner-accept, #gdpr-banner-decline, .js-accept, .js-decline, "
    "button.rodo-popup-agree, button#declineButton, .orejime-Notice-declineButton, "
    ".qc-cmp2-summary-buttons button, .qc-cmp2-buttons-desktop button, "
    "#termly-code-snippet-support button[aria-label*='accept' i], "
    ".cky-btn-accept, .cky-btn-reject, .cky-btn-close, "
    "button[data-cky-tag='accept-button'], button[data-cky-tag='reject-button'], "
    ".cookie-consent button, .cookie-banner button, .cookie-notice button, "
    ".cookie-alert button, .cookie-bar button, .cookie-box button, .cookie-hint button, "
    ".cookies-banner button, .cookie-policy button, .gdpr-banner button, "
    "#cookie-banner button, #cookie_consent button, #cookie-law-info-bar button, "
    "#moove_gdpr_cookie_info_bar button, .eu-cookie-compliance-banner button, "
    ".sqs-cookie-banner-v2 button, .pum-close, .elementor-popup-modal .elementor-button, "
    "[aria-label*='close' i], [title*='close' i], [data-dismiss], [data-close], "
    "[data-action='close'], [data-action='dismiss'], [data-testid*='close' i], "
    "button.close, .modal-close, .popup-close, .dialog-close, .banner-close, "
    "button[class*='close' i]:not([class*='closed' i]), "
    "a[class*='close' i]:not([class*='closed' i])"
)

# Text content that marks a button as the one that dismisses a cookie banner
# or popup. Multi-language: en, de, fr, es, it, nl, pt, sv, da, fi, ar, zh.
# Full-string match only: a bare "order" alternative must not match
# "My order history" links, so every alternative is boundary-anchored.
DISMISS_BUTTON_TEXT_RE = re.compile(
    r"""(?ix)^\s*(?:
      (?:accept|allow|agree|ok(?:ay)?|yes|ja|si|sì|oui|sim)\b[\s\w',.()-]{0,30} |
      (?:reject|decline|refuse|deny|dismiss|close|continue|got\s+it|no\b[\s,]*(?:thanks)?|not\s+now|nein|non|não)\b[\s\w',.()-]{0,20} |
      (?:only\s+)?(?:necessary|essential|required)(?:\s+(?:cookies?|only))?\b |
      (?:manage|customize)\s+(?:cookies?|preferences?|options?|settings?)\b |
      preferences?\b | settings?\b
      | akzeptieren\w* | alle\s+akzeptieren | zulassen\w* | ablehnen\w*
      | nur\s+(?:notwendige|erforderliche)\w* | schließen | weiter\s+ohne\w*
      | tout\s+accepter | tout\s+refuser | accepter\w* | refuser\w* | fermer
      | continuer\s+sans\w* | aceptar\w* | rechazar\w* | cerrar
      | continuar\s+sin\w* | accetta\w* | rifiuta\w* | chiudi
      | continua\s+senza\w* | accepteer\w* | weiger\w* | sluiten
      | verder\s+zonder\w* | aceitar\w* | rejeitar\w* | fechar | aceito\w*
      | acceptera\w* | avvisa\w* | stäng | godkänn\w* | afvis\w* | luk
      | hyväksy\w* | sulje
      | قبول | إغلاق | رفض | 接受 | 拒绝 | 关闭 | 同意 | 继续
    )\s*$"""
)

# Buttons whose click would navigate / commit forms / act on data.
DISMISS_BUTTON_TEXT_BLOCK_RE = re.compile(
    r"(?ix)\b(?:subscrib|newsletter|sign\s*up|register|buy|purchase|checkout|"
    r"download|submit|pay|donate|log\s*in|sign\s*in|share|order)\b"
)
DISMISS_OVERLAYS_JS = """\
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  };
  const inViewport = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
  };
  const safeClick = (el) => {
    try {
      const opts = { bubbles: true, cancelable: true, view: window };
      el.dispatchEvent(new PointerEvent("pointerdown", opts));
      el.dispatchEvent(new MouseEvent("mousedown", opts));
      el.dispatchEvent(new PointerEvent("pointerup", opts));
      el.dispatchEvent(new MouseEvent("mouseup", opts));
      el.click();
    } catch (e) {}
  };
  const pressEscape = () => {
    const target = document.activeElement instanceof HTMLElement ? document.activeElement : document.body;
    const opts = { key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true, cancelable: true };
    try {
      target.dispatchEvent(new KeyboardEvent("keydown", opts));
      target.dispatchEvent(new KeyboardEvent("keyup", opts));
    } catch (e) {}
  };
  const Dialog = "[dialog],[role='dialog'],[aria-modal='true'],div[class*='modal' i],div[class*='overlay' i],div[class*='popup' i]";
  const dialogCloseAffordances = () => {
    const closeSel = '[aria-label*="close" i], [title*="close" i], [data-dismiss], [data-close], [data-action="close"], [data-action="dismiss"], button.close, .modal-close, .popup-close, .dialog-close, [role="button"]';
    const out = [];
    for (const box of document.querySelectorAll(Dialog)) {
      for (const btn of box.querySelectorAll(closeSel)) {
        if (visible(btn) && inViewport(btn)) out.push(btn);
      }
    }
    return out;
  };
  const bannerButtons = () => {
    const sel = "%BANNER_PROBE_SELECTOR%, dialog[open]";
    const buttons = [];
    for (const box of document.querySelectorAll(sel)) {
      if (!visible(box) || !inViewport(box)) continue;
      for (const btn of box.querySelectorAll("button, a, [role='button'], input[type='button']")) {
        if (visible(btn) && inViewport(btn)) buttons.push(btn);
      }
    }
    return buttons;
  };
  const textLike = (el) => {
    const parts = [el.textContent || "", el.getAttribute("aria-label") || "", el.getAttribute("title") || ""];
    for (const name of el.getAttributeNames()) {
      if (/^(data-|aria-)/.test(name)) parts.push(el.getAttribute(name) || "");
    }
    return parts.join(" ").trim().replace(/\\s+/g, " ");
  };
  const cmpButtons = () => {
    const sel = "%DISMISS_CLICK_SELECTORS%";
    return [...document.querySelectorAll(sel)].filter((el) => visible(el) && inViewport(el));
  };
  const textButtons = (roots) => {
    const out = [];
    for (const btn of roots) {
      const text = textLike(btn);
      if (!text || text.length > 120) continue;
      if ((%DISMISS_BUTTON_TEXT_BLOCK_RE%).test(text)) continue;
      if ((%DISMISS_BUTTON_TEXT_RE%).test(text)) out.push(btn);
    }
    return out;
  };
  const probe = () => {
    try { return window.__mww_overlay_probe() || {}; } catch (e) { return {}; }
  };
  const overlayLeft = () => {
    const p = probe();
    return (p.dialog || 0) > 0 || (p.banner || 0) > 0 || (p.scrim || 0) > 0;
  };
  // 1. Escape — the standard way to dismiss dialogs and popups.
  pressEscape();
  await sleep(250);
  if (overlayLeft()) {
    // 2. Close affordances inside dialog-like containers (never other
    // buttons: clicking a dialog's primary CTA would subscribe/buy).
    for (const btn of dialogCloseAffordances()) safeClick(btn);
    await sleep(400);
  }
  if (overlayLeft()) {
    // 3. CMP accept / reject buttons (mozilla/cookie-banner-rules-list).
    // One click only: accept + decline together cancel the consent, and
    // clicking multiple banners' buttons would consent to the wrong CMP.
    safeClick(cmpButtons()[0]);
    await sleep(500);
  }
  if (overlayLeft()) {
    // 4. Text-matched accept/dismiss/close buttons in visible banners and
    // open native dialogs (a home-grown <dialog class=cookie-banner>).
    // Same single-click rule: the list prefers accept-like labels, and
    // clicking accept then decline would undo the choice.
    safeClick(textButtons(bannerButtons())[0]);
    await sleep(500);
  }
  if (overlayLeft()) {
    // 5. Native <dialog>.close() as a last resort.
    for (const el of document.querySelectorAll("dialog[open]")) {
      try { el.close(); } catch (e) {}
    }
    await sleep(200);
  }
  return probe();
})();
"""


def _js_regex(pattern: re.Pattern) -> str:
    """JS regex literal for a Python pattern. JS has no (?ix) inline flags
    and no VERBOSE mode: strip the flag prefix, drop newlines, and collapse
    the free-spacing indentation (in VERBOSE mode it is ignored; left in
    place it would become mandatory literal spaces). Real spaces in the
    patterns are always written as \\s+ — verified per alternative."""
    source = pattern.pattern
    prefix = "(?ix)" if source.startswith("(?ix)") else "(?i)"
    assert source.startswith(prefix), source  # keep flag support honest
    stripped = re.sub(r"\s+", " ", source[len(prefix) :]).replace("  ", " ")
    # Whitespace that VERBOSE ignored is gone; a single space between
    # alternatives is still ignored by the JS engine only outside classes —
    # but JS does NOT ignore it, so remove it entirely. Literals needing
    # spaces use \s or ' ' explicitly in the source (none do).
    return "/" + stripped.replace(" ", "") + "/i"


DISMISS_OVERLAYS_JS = (
    DISMISS_OVERLAYS_JS.replace("%BANNER_PROBE_SELECTOR%", BANNER_PROBE_SELECTOR)
    .replace("%DISMISS_CLICK_SELECTORS%", DISMISS_CLICK_SELECTORS)
    .replace("%DISMISS_BUTTON_TEXT_RE%", _js_regex(DISMISS_BUTTON_TEXT_RE))
    .replace("%DISMISS_BUTTON_TEXT_BLOCK_RE%", _js_regex(DISMISS_BUTTON_TEXT_BLOCK_RE))
)
BANNER_HIDE_CSS = """\
/* Major CMP SDK containers (grounded in AdGuard's Cookie Notices filter) */
#onetrust-consent-sdk, #onetrust-banner-sdk, #onetrust-pc-sdk,
#optanon-popup-bg, .optanon-show-settings,
#CybotCookiebotDialog, #CybotCookiebotDialogBodyUnderlay, .CybotCookiebotDialogBodyOverlay,
#usercentrics-root, #usercentrics-cmp-ui, #fc-consent-root,
#didomi-host, #didomi-popup, .didomi-host,
#sp_message_container, .sp-message-container, #sp_privacy_manager_container,
#qc-cmp2-container, #qc-cmp2-ui, .qc-cmp2-summary-buttons,
#truste-consent-track, .truste-consent-track, #truste-consent-content,
#Osano-CookieDialog, .osano-cm-window, .osano-cm-info,
#termly-code-snippet-support, .termly-consent-banner,
#iubenda-cs-banner, .iubenda-cs-banner,
#cmplz-cookiebanner-container, .cmplz-cookiebanner,
#cookie-script, .cookiescript_injected_wrapper, #cookiescript_injected,
#klaro, .klaro, .cookie-consent:not(body):not(html),
#klaro0, .klaro-manager-overlay,
#tarteaucitronRoot, .tarteaucitron-root, #tarteaucitronAlertBig, .tarteaucitron-banner,
#BorlabsCookieBox, .borlabs-hide,
#ccm-widget, #ccm-block,
#cky-consent-bar, .cky-consent-bar, .cky-consent-container, #cky-overlay, .cky-overlay,
#cm, #cc-banner, .cc-window, .cc-banner, .cc-revoke,
#cookiebanner, .cookie-banner, #cookie-banner, #cookie_consent, .cookie_consent,
#cookie-law-info-bar, #cookie-law-info-again, .cli-bar-container, #cliSettingsPopup,
.cli-popupbar-overlay, .cli-modal-backdrop,
#moove_gdpr_cookie_info_bar, #moove_gdpr_cookie_modal,
#gdpr-cookie-message, .gdpr-cookie-notice, .gdpr-banner, #gdpr-banner, .gdpr_cookie_bar,
.eu-cookie-compliance-banner:not(body):not(html), .eu-cookie-compliance-overlay,
.js-cookie-banner, .js-cookie-consent, .cookie-notice:not(body):not(html),
.cookie-consent-banner, .cookie-alert:not(body):not(html), .cookie-bar:not(body):not(html),
.cookie-warning, .cookie-policy:not(body):not(html), #cookie-policy,
.cookie-popup, .cookie-popup-wrapper, .cookies-wrapper,
#cookie-box, .cookie-box:not(body):not(html), #cookie-bar, .cookie-bar-overlay,
#cookie-hint, #cookiehint, #cookie-hinweis, .cookie-hint,
#cookies-banner, #cookies-banner-container, .cookies-banner,
#cookie-msg, #cookie-message, .cookie-message:not(body):not(html),
.cookies-eu-banner, #cookie-law-banner, .cookie-law-banner,
#cookiesck, .sqs-cookie-banner-v2, .wpgdprc-consent-bar,
.avia-cookie-consent-wrap, .fusion-privacy-bar, .woodmart-cookies-popup,
.thb-cookie-bar, .pum-open .pum-overlay, .elementor-popup-modal:not(:empty),
/* Generic fallback: any container whose class mentions "cookie". Scoped to
   banner-capable container elements — an unscoped [class*="cookie"] would
   hide recipe content on food blogs (e.g. .cookie-recipes-grid) and blank
   the screenshot. The i flag covers CamelCase classes. */
dialog, [role="dialog"], div[class*="cookie" i], section[class*="cookie" i],
aside[class*="cookie" i], footer[class*="cookie" i], header[class*="cookie" i],
/* Same scoping for id-based banners: id attribute mentioning "cookie". */
div[id*="cookie" i], section[id*="cookie" i], aside[id*="cookie" i],
footer[id*="cookie" i], header[id*="cookie" i]
"""
CONSENT_INIT_JS = (
    """\
(() => {
  const CSS = `%s { display: none !important; }`;
  const ID = "__mww_banner_hide";
  // Deferred installation: the overlay-dismissal script (DISMISS_OVERLAYS_JS)
  // runs first, and this CSS hide-style is only its fallback. The driver
  // calls `window.__mww_install_banner_css()` after the dismissal attempt.
  window.__mww_install_banner_css = () => {
    if (document.getElementById(ID)) return;
    const root = document.head || document.documentElement;
    if (!root) return;
    const style = document.createElement("style");
    style.id = ID;
    style.textContent = CSS;
    root.appendChild(style);
  };
})();"""
    % BANNER_HIDE_CSS
)


def wait_for_page_settled(page, timeout_s: float = 15.0) -> None:
    """Wait until the page stops mutating the DOM and fetching, so spinners,
    lazy-loaded heroes, and CMP mounts have had their chance to appear."""
    mutations = {"count": 0}
    inflight = {"open": 0}
    observer_js = """
    window.__mww_mutations = 0;
    new MutationObserver((records) => { window.__mww_mutations += records.length; })
      .observe(document, { childList: true, subtree: true, attributes: true });
    """
    try:
        page.evaluate(observer_js)
    except Exception:
        pass  # CSP-locked page: settle on network activity alone.

    def _request(_request):
        inflight["open"] += 1

    def _settled(_request):
        inflight["open"] -= 1

    page.on("request", _request)
    page.on("requestfinished", _settled)
    page.on("requestfailed", _settled)

    deadline = time.monotonic() + timeout_s
    last_activity = time.monotonic()
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        now = time.monotonic()
        try:
            count = page.evaluate("window.__mww_mutations || 0")
            if count:
                mutations["count"] += count
                last_activity = now
                page.evaluate("window.__mww_mutations = 0")
        except Exception:
            pass
        if inflight["open"] > 0:
            last_activity = now
        if now - last_activity >= 2.0:
            return
    # Deadline reached; proceed with whatever the page shows now.


def capture_screenshot(url: str, out_path: Path) -> None:
    """Load the URL in headless Chromium and save an encoded WebP screenshot.

    Overlay suppression is JS-first (DISMISS_OVERLAYS_JS): wait for the page
    to settle, then dismiss dialogs / cookie banners / popups by pressing
    Escape and clicking close/accept affordances, before falling back to the
    BANNER_HIDE_CSS style. Navigation is blocked during interaction so a
    misclicked control cannot take the screenshot to a different page.
    """
    from playwright.sync_api import sync_playwright

    def route_guard(route):
        if is_private_browser_host(route.request.url):
            route.abort()
        else:
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1200, "height": 996}, device_scale_factor=1
        )
        context.route("**/*", route_guard)
        context.add_init_script(CONSENT_INIT_JS)
        page = context.new_page()
        target_origin = urlsplit(url)
        origin = f"{target_origin.scheme}://{target_origin.netloc}"
        try:
            page.goto(url, timeout=30_000, wait_until="load")
            wait_for_page_settled(page)
            # Block navigation during interaction: a misclick on a link
            # inside a banner must not screenshot a different page.
            # Cross-origin navigation is always blocked; same-origin is
            # allowed (banner accept often reloads the same URL).
            page.evaluate(
                """(origin) => {
                  window.__mww_nav_blocked = 0;
                  document.addEventListener('click', (event) => {
                    const link = event.target && event.target.closest && event.target.closest('a[href]');
                    if (!link) return;
                    try {
                      const target = new URL(link.href || link.getAttribute('href') || '', location.href);
                      if (target.origin !== origin) {
                        event.preventDefault();
                        event.stopPropagation();
                        window.__mww_nav_blocked += 1;
                      }
                    } catch (e) {}
                  }, true);
                }""",
                origin,
            )
            page.evaluate(OVERLAY_PROBE_JS)  # installs __mww_overlay_probe
            # Dismissing a banner often reloads the page (the consent cookie
            # only takes effect after navigation), destroying the execution
            # context. Re-run until the probe reports no overlay left.
            probe: dict | None = None
            for _ in range(3):
                try:
                    probe = page.evaluate(DISMISS_OVERLAYS_JS)
                except Exception:
                    # Same-origin reload from the click; re-arm and retry.
                    page.wait_for_load_state("load", timeout=20_000)
                    page.wait_for_timeout(500)
                    page.evaluate(OVERLAY_PROBE_JS)
                    continue
                if not any(probe.get(k) for k in ("dialog", "banner", "scrim")):
                    break
                page.wait_for_timeout(400)
            # Always install the CSS hide-style afterwards as the final
            # fallback: banners re-mounted by CMP timers after the JS pass.
            try:
                page.evaluate(
                    "window.__mww_install_banner_css && window.__mww_install_banner_css()"
                )
            except Exception:
                page.wait_for_load_state("load", timeout=20_000)
                page.evaluate(
                    "window.__mww_install_banner_css && window.__mww_install_banner_css()"
                )
            png = page.screenshot(type="png", animations="disabled")
        finally:
            context.close()
            browser.close()
    leftover = ", ".join(f"{k}={v}" for k, v in (probe or {}).items() if v)
    if leftover:
        print(f"capture_screenshot: overlay remained after dismissal ({leftover})")
    out_path.write_bytes(encode_screenshot(png))
