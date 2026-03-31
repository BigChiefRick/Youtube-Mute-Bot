#!/usr/bin/env python3
"""
yt-mute-bot: Automates YouTube Studio copyright claim muting.

Workflow:
  1. Opens YouTube Studio with a persistent browser session.
  2. Finds live-stream VODs that have copyright restrictions.
  3. Navigates to each video's Copyright page.
  4. Clicks "Take action" -> "Mute song" on the first available claim.
  5. Polls until YouTube finishes processing.
  6. Repeats for the next claim, then the next video.

First run: use --login to open a headed browser and log in manually.
Subsequent runs: the saved browser profile is reused headlessly.
"""

import argparse
import json
import logging
import os
import socket
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def setup_logging(log_file: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger = logging.getLogger("yt-mute-bot")
    logger.setLevel(logging.DEBUG)

    # File handler - verbose
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # Console handler - info+
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    return logger


def save_screenshot(page, screenshot_dir: str, label: str):
    """Save a debug screenshot with timestamp."""
    os.makedirs(screenshot_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(screenshot_dir, f"{ts}_{label}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

STUDIO_BASE = "https://studio.youtube.com"


def content_live_url(channel_id: str) -> str:
    return (
        f"{STUDIO_BASE}/channel/{channel_id}/videos/live"
        f"?c={channel_id}"
    )


def video_copyright_url(video_id: str, channel_id: str) -> str:
    return (
        f"{STUDIO_BASE}/video/{video_id}/copyright"
        f"?c={channel_id}"
    )


# ---------------------------------------------------------------------------
# Core automation
# ---------------------------------------------------------------------------

class YTMuteBot:
    def __init__(self, cfg: dict, logger: logging.Logger):
        self.cfg = cfg
        self.log = logger
        self.claims_processed = 0

    # -- Browser lifecycle --------------------------------------------------

    def launch(self, playwright, headless: bool, remote_debug: bool = False):
        profile_dir = self.cfg["browser_profile_dir"]
        os.makedirs(profile_dir, exist_ok=True)

        extra_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
        if remote_debug:
            extra_args.extend([
                "--remote-debugging-port=9222",
                "--remote-debugging-address=0.0.0.0",
            ])

        self.browser = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            args=extra_args,
        )
        self.page = self.browser.new_page()
        self.page.set_default_timeout(30_000)

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass

    # -- Login flow (interactive, headed) -----------------------------------

    def interactive_login(self):
        """Open Studio and wait for the user to log in manually."""
        self.log.info("Opening YouTube Studio for manual login...")
        self.page.goto(f"{STUDIO_BASE}/", wait_until="networkidle")
        self.log.info(
            ">>> Log in to your Google account in the browser window. <<<"
        )
        self.log.info(
            ">>> Once you see the Studio dashboard, press ENTER here. <<<"
        )
        input("Press ENTER after logging in...")
        if "studio.youtube.com" in self.page.url:
            self.log.info("Login successful - session saved to browser profile.")
        else:
            self.log.error(f"Unexpected URL after login: {self.page.url}")
            sys.exit(1)

    def remote_login(self):
        """Headless login via Chrome remote debugging. No X11/VNC needed."""
        self.log.info("Opening YouTube Studio for remote login...")
        self.page.goto(f"{STUDIO_BASE}/", wait_until="networkidle")
        time.sleep(3)

        # Get the page ID from CDP endpoint
        page_id = None
        try:
            resp = urllib.request.urlopen("http://localhost:9222/json")
            pages = json.loads(resp.read())
            for p in pages:
                if p.get("type") == "page":
                    page_id = p["id"]
                    break
        except Exception as e:
            self.log.warning(f"Could not query CDP endpoint: {e}")

        # Get this machine's IP
        server_ip = self._get_local_ip()

        print()
        print("=" * 70)
        print("  REMOTE LOGIN")
        print("=" * 70)
        print()
        if page_id:
            direct_url = (
                f"http://{server_ip}:9222"
                f"/devtools/inspector.html"
                f"?ws={server_ip}:9222/devtools/page/{page_id}"
            )
            print("  Paste this URL directly into Edge/Chrome on your workstation:")
            print()
            print(f"    {direct_url}")
            print()
        else:
            print(f"  Open this in Edge/Chrome to see available tabs:")
            print()
            print(f"    http://{server_ip}:9222")
            print()
        print("  You'll see a DevTools inspector with the browser page.")
        print("  The page view is in the top-right panel.")
        print("  If it's tiny, click the phone/tablet icon to toggle")
        print("  device mode, or drag the divider to resize.")
        print()
        print("  Log into Google, then navigate to YouTube Studio.")
        print("  Once you see the Studio dashboard, come back here")
        print("  and press ENTER.")
        print("=" * 70)
        print()
        input("Press ENTER after logging in... ")

        # Give cookies time to sync
        time.sleep(5)

        # Reload to pick up navigation done via DevTools
        try:
            self.page.reload(wait_until="networkidle")
            time.sleep(5)
        except Exception:
            pass

        # Log cookie state
        cookies = self.browser.cookies()
        yt_cookies = [c["name"] for c in cookies
                      if "youtube" in c.get("domain", "")
                      or "google" in c.get("domain", "")]
        self.log.info(f"Cookies captured: {len(yt_cookies)} google/youtube cookies")

        url = self.page.url
        self.log.info(f"Final URL: {url}")

        if "studio.youtube.com" in url and "/channel/" in url:
            self.log.info("Login successful - session saved to browser profile.")
        elif "accounts.google.com" in url or len(yt_cookies) < 5:
            self.log.error(
                "Login does not appear complete. "
                f"URL: {url}, cookies: {len(yt_cookies)}"
            )
            self.log.error("Try again — make sure you fully log in before pressing Enter.")
            sys.exit(1)
        elif "studio.youtube.com" in url:
            self.log.info("Login successful - session saved to browser profile.")
        else:
            self.log.warning(f"Ended on: {url}")
            self.log.warning("Session may not be valid. Test with a normal run.")

    @staticmethod
    def _get_local_ip() -> str:
        """Get this machine's LAN IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "<this-server-ip>"

    # -- Check session validity ---------------------------------------------

    def verify_session(self) -> bool:
        """Navigate to Studio and confirm we're still logged in."""
        self.log.info("Verifying YouTube Studio session...")
        self.page.goto(f"{STUDIO_BASE}/", wait_until="networkidle")
        time.sleep(5)
        url = self.page.url
        self.log.info(f"Landed on: {url}")

        # Also log cookies for debugging
        cookies = self.browser.cookies()
        yt_cookies = [c["name"] for c in cookies if "youtube" in c.get("domain", "") or "google" in c.get("domain", "")]
        self.log.info(f"Session cookies found: {len(yt_cookies)}")

        if "accounts.google.com" in url or "signin" in url.lower():
            self.log.error(
                "Session expired - re-run with --remote-login to re-authenticate."
            )
            return False
        if "studio.youtube.com" in url:
            self.log.info("Session is valid.")
            return True
        self.log.warning(f"Unexpected URL: {url}")
        return False

    # -- Find videos with copyright claims ----------------------------------

    def get_flagged_video_ids(self) -> list:
        """
        Navigate to Content -> Live and return video IDs that have
        'Copyright' in their Restrictions column.
        """
        channel_id = self.cfg["channel_id"]
        url = content_live_url(channel_id)
        self.log.info(f"Loading live content list: {url}")
        self.page.goto(url, wait_until="networkidle")
        time.sleep(5)

        # Wait for the video table to render
        try:
            self.page.wait_for_selector(
                "ytcp-video-row", timeout=15_000
            )
        except PwTimeout:
            self.log.warning("Could not find video rows in the live tab.")
            save_screenshot(
                self.page, self.cfg["screenshot_dir"], "no_video_rows"
            )
            return []

        # Scrape video rows
        rows = self.page.query_selector_all("ytcp-video-row")
        self.log.info(f"Found {len(rows)} video rows in live tab.")

        flagged = []
        for row in rows:
            text = row.inner_text()
            if "Copyright" not in text:
                continue

            # Extract the video ID from any link in the row
            links = row.query_selector_all("a[href*='/video/']")
            for link in links:
                href = link.get_attribute("href") or ""
                parts = href.split("/video/")
                if len(parts) > 1:
                    vid_id = parts[1].split("/")[0].split("?")[0]
                    if vid_id and vid_id not in flagged:
                        flagged.append(vid_id)
                        self.log.info(f"  Flagged video: {vid_id}")
                    break

        if not flagged:
            self.log.info("No flagged videos found via row scan.")
            save_screenshot(
                self.page, self.cfg["screenshot_dir"], "no_flagged"
            )

        return flagged

    # -- Process one video's copyright claims -------------------------------

    def process_video(self, video_id: str) -> int:
        """
        Navigate to a video's copyright page and mute claims one at a time.
        Returns the number of claims successfully muted.
        """
        channel_id = self.cfg["channel_id"]
        url = video_copyright_url(video_id, channel_id)
        self.log.info(f"Opening copyright page: {url}")
        self.page.goto(url, wait_until="networkidle")
        time.sleep(5)

        muted_count = 0

        while True:
            # Check claim limit
            max_claims = self.cfg.get("max_claims_per_run", 0)
            if max_claims > 0 and self.claims_processed >= max_claims:
                self.log.info(
                    f"Reached max claims per run ({max_claims}). Stopping."
                )
                return muted_count

            # Check if editing is already in progress
            if self._is_editing_in_progress():
                self.log.info("A mute job is already processing. Waiting...")
                if not self._wait_for_processing():
                    self.log.error("Timed out waiting for processing.")
                    save_screenshot(
                        self.page, self.cfg["screenshot_dir"],
                        f"timeout_{video_id}"
                    )
                    return muted_count
                # Reload page for fresh state
                self.page.goto(url, wait_until="networkidle")
                time.sleep(5)

            # Find available "Take action" buttons
            action_btn = self._find_take_action_button()
            if not action_btn:
                self.log.info(
                    f"No more actionable claims on video {video_id}."
                )
                break

            # Get claim name for logging
            claim_name = self._get_claim_name_near(action_btn)
            self.log.info(f"Muting claim: {claim_name}")

            # Click "Take action" to open dropdown
            try:
                action_btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                action_btn.click()
                time.sleep(2)
            except Exception as e:
                self.log.error(f"Failed to click Take action: {e}")
                save_screenshot(
                    self.page, self.cfg["screenshot_dir"],
                    f"click_fail_{video_id}"
                )
                break

            # Select "Mute song" from the dropdown
            if not self._click_mute_song():
                self.log.error("Could not find 'Mute song' option.")
                save_screenshot(
                    self.page, self.cfg["screenshot_dir"],
                    f"no_mute_option_{video_id}"
                )
                self.page.keyboard.press("Escape")
                time.sleep(1)
                break

            time.sleep(2)

            # Handle any confirmation dialog
            self._handle_confirmation()
            time.sleep(3)

            muted_count += 1
            self.claims_processed += 1
            self.log.info(
                f"Mute initiated for '{claim_name}' "
                f"(total this run: {self.claims_processed})"
            )

            # Wait for this mute job to finish
            if self._is_editing_in_progress():
                self.log.info("Waiting for mute processing to complete...")
                if not self._wait_for_processing():
                    self.log.error("Timed out waiting. Will retry next run.")
                    save_screenshot(
                        self.page, self.cfg["screenshot_dir"],
                        f"processing_timeout_{video_id}"
                    )
                    return muted_count

            # Reload for fresh claim list
            self.page.goto(url, wait_until="networkidle")
            time.sleep(5)

        return muted_count

    # -- UI interaction helpers ---------------------------------------------

    def _is_editing_in_progress(self) -> bool:
        """Check if the 'Video editing is in progress' banner is showing."""
        indicators = [
            "text=Video editing is in progress",
            "text=editing is in progress",
            "text=Edit processing",
        ]
        for sel in indicators:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _wait_for_processing(self) -> bool:
        """
        Poll the page until the 'editing in progress' banner disappears.
        Returns True if processing completed, False if timed out.
        """
        poll = self.cfg.get("poll_interval_seconds", 300)
        max_wait = self.cfg.get("max_wait_per_claim_seconds", 14400)
        elapsed = 0

        while elapsed < max_wait:
            self.log.debug(f"Polling... ({elapsed}s / {max_wait}s max)")
            time.sleep(poll)
            elapsed += poll

            try:
                self.page.reload(wait_until="networkidle")
                time.sleep(5)
            except Exception as e:
                self.log.warning(f"Reload failed: {e}. Retrying...")
                continue

            if not self._is_editing_in_progress():
                self.log.info(f"Processing complete after ~{elapsed}s.")
                return True

        return False

    def _find_take_action_button(self):
        """Find the first clickable 'Take action' button on the page."""
        selectors = [
            "button:has-text('Take action')",
            "ytcp-button:has-text('Take action')",
        ]
        for sel in selectors:
            try:
                buttons = self.page.query_selector_all(sel)
                for btn in buttons:
                    if btn.is_visible() and btn.is_enabled():
                        return btn
            except Exception:
                continue

        # Last resort: text match
        try:
            el = self.page.query_selector("text=Take action")
            if el and el.is_visible():
                return el
        except Exception:
            pass

        return None

    def _get_claim_name_near(self, action_btn) -> str:
        """Extract the song/artist name from the same claim row."""
        try:
            row = action_btn.evaluate_handle(
                """el => {
                    let p = el;
                    for (let i = 0; i < 10; i++) {
                        p = p.parentElement;
                        if (!p) break;
                        if (p.tagName === 'TR' ||
                            p.classList.contains('claim-row') ||
                            (p.getAttribute('class') || '').includes('row'))
                            return p;
                    }
                    return el.parentElement?.parentElement?.parentElement;
                }"""
            )
            if row:
                text = row.evaluate("el => el.innerText")
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if lines:
                    return lines[0]
        except Exception:
            pass
        return "(unknown claim)"

    def _click_mute_song(self) -> bool:
        """Click the 'Mute song' option in the Take action dropdown."""
        time.sleep(1)
        selectors = [
            "tp-yt-paper-item:has-text('Mute song')",
            "[role='menuitem']:has-text('Mute song')",
            "tp-yt-paper-listbox >> text=Mute song",
            "ytcp-text-menu >> text=Mute song",
        ]
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    self.log.debug(f"Clicked 'Mute song' via: {sel}")
                    return True
            except Exception:
                continue

        # Fallback: direct text click
        try:
            self.page.click("text=Mute song", timeout=5_000)
            return True
        except PwTimeout:
            pass

        return False

    def _handle_confirmation(self):
        """Click through any confirmation dialog after muting."""
        confirm_selectors = [
            "button:has-text('Continue')",
            "button:has-text('Confirm')",
            "button:has-text('Mute')",
            "ytcp-button:has-text('Continue')",
            "ytcp-button:has-text('Confirm')",
            "#confirm-button",
        ]
        time.sleep(1)
        for sel in confirm_selectors:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    self.log.debug(f"Clicked confirmation: {sel}")
                    time.sleep(1)
                    return
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Automate YouTube Studio copyright claim muting."
    )
    parser.add_argument(
        "--config", "-c",
        default="/opt/yt-mute-bot/config.yaml",
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Run in headed mode for initial Google login (needs display).",
    )
    parser.add_argument(
        "--remote-login",
        action="store_true",
        dest="remote_login",
        help="Login via Chrome remote debugging. No display needed. "
             "Connect from your workstation via chrome://inspect.",
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Process a single video ID instead of scanning all.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg["log_file"])

    log.info("=" * 60)
    log.info("yt-mute-bot starting")
    log.info("=" * 60)

    headless = not args.login and cfg.get("headless", True)
    remote_debug = args.remote_login

    with sync_playwright() as pw:
        bot = YTMuteBot(cfg, log)
        try:
            bot.launch(pw, headless=headless, remote_debug=remote_debug)

            if args.remote_login:
                bot.remote_login()
                bot.close()
                log.info("Login complete. Run without flags from now on.")
                return

            if args.login:
                bot.interactive_login()
                bot.close()
                log.info("Login complete. Run without --login from now on.")
                return

            if not bot.verify_session():
                bot.close()
                sys.exit(1)

            # Determine which videos to process
            if args.video:
                video_ids = [args.video]
                log.info(f"Processing single video: {args.video}")
            else:
                video_ids = bot.get_flagged_video_ids()
                if not video_ids:
                    log.info("No videos with copyright claims found. Done.")
                    bot.close()
                    return

            total_muted = 0
            for vid_id in video_ids:
                log.info(f"--- Processing video: {vid_id} ---")
                count = bot.process_video(vid_id)
                total_muted += count
                log.info(f"Muted {count} claims on video {vid_id}")

                max_claims = cfg.get("max_claims_per_run", 0)
                if max_claims > 0 and bot.claims_processed >= max_claims:
                    break

            log.info(f"Run complete. Total claims muted: {total_muted}")

        except KeyboardInterrupt:
            log.info("Interrupted by user.")
        except Exception as e:
            log.error(f"Fatal error: {e}", exc_info=True)
            try:
                save_screenshot(
                    bot.page, cfg["screenshot_dir"], "fatal_error"
                )
            except Exception:
                pass
        finally:
            bot.close()

    log.info("yt-mute-bot finished.")


if __name__ == "__main__":
    main()
