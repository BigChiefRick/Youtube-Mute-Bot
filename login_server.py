#!/usr/bin/env python3
"""
login_server.py — Web-based login for yt-mute-bot.

Starts a headless browser and serves a web UI on port 8080.
Open http://<server-ip>:8080 in any browser to see and interact
with the headless Chromium. Log into Google, get to YouTube Studio,
click "Save & Exit". Browser profile is saved for yt_mute_bot.py.

No VNC. No X11. No extensions. Just a web page.
"""

import io
import json
import os
import signal
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

import yaml
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Globals (set in main)
# ---------------------------------------------------------------------------
PAGE = None
BROWSER = None
PW_INSTANCE = None
PROFILE_SAVED = False

# ---------------------------------------------------------------------------
# HTML UI — embedded directly, no external files needed
# ---------------------------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html>
<head>
<title>yt-mute-bot Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a1a; color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; padding: 20px;
  }
  h1 { margin-bottom: 10px; color: #fff; }
  .status { margin-bottom: 15px; color: #aaa; font-size: 14px; }
  #browser-frame {
    border: 2px solid #444; cursor: crosshair; display: block;
    max-width: 100%; image-rendering: auto;
  }
  .controls {
    margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;
    justify-content: center; align-items: center;
  }
  input[type="text"] {
    background: #2a2a2a; border: 1px solid #555; color: #fff;
    padding: 8px 12px; border-radius: 4px; font-size: 14px;
    width: 300px;
  }
  button {
    padding: 8px 16px; border: none; border-radius: 4px;
    font-size: 14px; cursor: pointer;
  }
  .btn-type { background: #4a9eff; color: #fff; }
  .btn-key { background: #555; color: #fff; }
  .btn-done { background: #2ea043; color: #fff; font-size: 16px; padding: 10px 24px; }
  .btn-nav { background: #6f42c1; color: #fff; }
  .hint {
    margin-top: 15px; color: #888; font-size: 13px;
    max-width: 700px; text-align: center; line-height: 1.5;
  }
</style>
</head>
<body>
  <h1>yt-mute-bot Login</h1>
  <div class="status" id="status">Click on the browser image to interact. Type below to enter text.</div>

  <img id="browser-frame" src="/screenshot" width="960" height="540" />

  <div class="controls">
    <input type="text" id="text-input" placeholder="Type text here, then click Send or press Enter" />
    <button class="btn-type" onclick="sendText()">Send Text</button>
    <button class="btn-key" onclick="sendKey('Enter')">Enter</button>
    <button class="btn-key" onclick="sendKey('Tab')">Tab</button>
    <button class="btn-key" onclick="sendKey('Backspace')">Backspace</button>
    <button class="btn-key" onclick="sendKey('Escape')">Esc</button>
  </div>
  <div class="controls">
    <button class="btn-nav" onclick="navigate('https://accounts.google.com')">Go to Google Login</button>
    <button class="btn-nav" onclick="navigate('https://studio.youtube.com')">Go to YouTube Studio</button>
    <button class="btn-done" onclick="saveDone()">Save &amp; Exit</button>
  </div>
  <div class="hint">
    Click directly on the screenshot to click in the browser. Use the text field + Send
    to type (passwords, emails, etc). When you see the YouTube Studio dashboard, click
    "Save &amp; Exit".
  </div>

<script>
  const img = document.getElementById('browser-frame');
  const status = document.getElementById('status');
  const textInput = document.getElementById('text-input');

  // Refresh screenshot every 600ms
  let refreshTimer = setInterval(() => {
    const newImg = new Image();
    newImg.onload = () => { img.src = newImg.src; };
    newImg.src = '/screenshot?' + Date.now();
  }, 600);

  // Click on screenshot
  img.addEventListener('click', (e) => {
    const rect = img.getBoundingClientRect();
    // Scale from displayed size to actual viewport (1920x1080)
    const scaleX = 1920 / rect.width;
    const scaleY = 1080 / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    status.textContent = 'Clicked at ' + x + ', ' + y;
    fetch('/click', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({x: x, y: y})
    });
  });

  // Send text
  function sendText() {
    const text = textInput.value;
    if (!text) return;
    status.textContent = 'Typing...';
    fetch('/type', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text})
    }).then(() => {
      status.textContent = 'Typed: ' + text.substring(0, 20) + (text.length > 20 ? '...' : '');
      textInput.value = '';
    });
  }

  // Enter key in text field sends text
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { sendText(); }
  });

  // Send special key
  function sendKey(key) {
    status.textContent = 'Key: ' + key;
    fetch('/key', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key})
    });
  }

  // Navigate
  function navigate(url) {
    status.textContent = 'Navigating to ' + url + '...';
    fetch('/navigate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url})
    });
  }

  // Save and exit
  function saveDone() {
    status.textContent = 'Saving session and shutting down...';
    clearInterval(refreshTimer);
    fetch('/done', {method: 'POST'}).then(() => {
      document.body.innerHTML = '<div style="text-align:center;margin-top:100px">' +
        '<h1 style="color:#2ea043">Session Saved</h1>' +
        '<p style="color:#aaa;margin-top:20px">You can close this tab.<br>' +
        'Run yt_mute_bot.py on the server to start muting.</p></div>';
    });
  }
</script>
</body>
</html>
"""


class LoginHandler(BaseHTTPRequestHandler):
    """HTTP handler for the login web UI."""

    def log_message(self, format, *args):
        # Suppress default request logging
        pass

    def do_GET(self):
        if self.path == "/":
            self._send_html(LOGIN_HTML)
        elif self.path.startswith("/screenshot"):
            self._send_screenshot()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/click":
            data = self._read_json()
            self._do_click(data["x"], data["y"])
            self._send_ok()
        elif self.path == "/type":
            data = self._read_json()
            self._do_type(data["text"])
            self._send_ok()
        elif self.path == "/key":
            data = self._read_json()
            self._do_key(data["key"])
            self._send_ok()
        elif self.path == "/navigate":
            data = self._read_json()
            self._do_navigate(data["url"])
            self._send_ok()
        elif self.path == "/done":
            self._do_done()
            self._send_ok()
        else:
            self.send_error(404)

    # -- Actions ---

    def _do_click(self, x, y):
        try:
            PAGE.mouse.click(x, y)
        except Exception as e:
            print(f"Click error: {e}")

    def _do_type(self, text):
        try:
            PAGE.keyboard.type(text, delay=50)
        except Exception as e:
            print(f"Type error: {e}")

    def _do_key(self, key):
        try:
            PAGE.keyboard.press(key)
        except Exception as e:
            print(f"Key error: {e}")

    def _do_navigate(self, url):
        try:
            PAGE.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"Navigate error: {e}")

    def _do_done(self):
        global PROFILE_SAVED
        PROFILE_SAVED = True
        print("\n  Session saved. Shutting down login server...")
        # Shut down in a separate thread so the response can be sent
        threading.Thread(target=self._shutdown, daemon=True).start()

    def _shutdown(self):
        time.sleep(1)
        os.kill(os.getpid(), signal.SIGINT)

    # -- Response helpers ---

    def _send_html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_screenshot(self):
        try:
            png = PAGE.screenshot(type="png")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(png)
        except Exception:
            self.send_error(500, "Screenshot failed")

    def _send_ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return json.loads(body)


def main():
    global PAGE, BROWSER, PW_INSTANCE

    # Load config
    config_path = "/opt/yt-mute-bot/config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    profile_dir = cfg.get("browser_profile_dir", "/opt/yt-mute-bot/browser-profile")
    port = int(cfg.get("login_port", 8080))

    os.makedirs(profile_dir, exist_ok=True)

    print()
    print("=" * 60)
    print("  yt-mute-bot Login Server")
    print("=" * 60)
    print()
    print("  Starting headless browser...")

    pw = sync_playwright().start()
    BROWSER = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=True,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    PAGE = BROWSER.new_page()
    PW_INSTANCE = pw

    # Navigate to Google login to start
    print("  Navigating to YouTube Studio...")
    try:
        PAGE.goto("https://studio.youtube.com/", wait_until="networkidle", timeout=30000)
    except Exception:
        pass

    # Get server IP
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "localhost"

    print()
    print(f"  Open this in your browser:")
    print()
    print(f"      http://{ip}:{port}")
    print()
    print("  Log into Google, get to YouTube Studio dashboard,")
    print("  then click 'Save & Exit'.")
    print()
    print("  Ctrl+C to abort without saving.")
    print("=" * 60)

    server = HTTPServer(("0.0.0.0", port), LoginHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print()
        if PROFILE_SAVED:
            print("  Browser profile saved successfully.")
            print("  Run yt_mute_bot.py to start muting:")
            print()
            print("    sudo -u ytmute /opt/yt-mute-bot/venv/bin/python /opt/yt-mute-bot/yt_mute_bot.py")
            print()
        else:
            print("  Aborted without saving.")
        BROWSER.close()
        pw.stop()


if __name__ == "__main__":
    main()
