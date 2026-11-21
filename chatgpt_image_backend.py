#!/usr/bin/env python3
"""
ChatGPT Web Image Generation Backend (`chatgpt_image_backend.py`)
=================================================================
Ported from Agent Hub (`images.mjs`) — Pure Python 3.12, zero external dependencies.

Connects via Chrome DevTools Protocol (CDP) over RFC 6455 WebSocket to a local
ChatGPT Web browser session (Codex Web GPT Launcher at
`~/.codex-chatgpt-web/runtime/launcher-browser.json`, or standalone Chromium/Chrome
CDP configured via `CHATGPT_CDP_ENDPOINT` / `LAUNCHER_BROWSER_JSON`).

Supports:
  - Pure Text-to-Image generation on ChatGPT Web (GPT Image / DALL-E native pipeline)
  - Reference-to-Image / Image Editing (pastes 1-3 local reference images into the composer)
  - Aspect ratio guidance (`16:9`, `1:1`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`)
  - Multi-turn iterative editing via `conversation_id` (`/c/<id>`)
  - Session inspection, export/import, and optional launcher startup
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUTS_DIR = os.path.join(REPO_ROOT, "outputs")
SAVED_SESSION_PATH = os.path.join(DEFAULT_OUTPUTS_DIR, "chatgpt_web_session.json")

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PROMPT_CHARS = 4000
DEFAULT_TIMEOUT_SEC = 180.0

ASPECT_RATIO_HINTS = {
    "16:9": "widescreen 16:9 landscape aspect ratio",
    "1:1": "square 1:1 aspect ratio",
    "9:16": "vertical 9:16 portrait aspect ratio",
    "4:3": "classic 4:3 landscape aspect ratio",
    "3:4": "vertical 3:4 portrait aspect ratio",
    "3:2": "3:2 horizontal photo aspect ratio",
    "2:3": "2:3 vertical portrait aspect ratio",
}

_IMAGE_LOCK = threading.Lock()
_EP_CACHE: dict[str, Any] = {"at": 0.0, "value": ""}
_ACCOUNT_CACHE: dict[str, Any] = {"at": 0.0, "value": None}

# ---------------------------------------------------------------------------
# DOM Selectors & Browser JS Snippets (ported verbatim from Agent Hub images.mjs)
# ---------------------------------------------------------------------------

COMP_SEL_SHARED = (
    '[data-testid="prompt-textarea"], #prompt-textarea, '
    '[contenteditable="true"][data-lexical-editor="true"], '
    'form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"][role="textbox"], '
    'form[data-chatgpt-composer] [contenteditable="true"][role="textbox"]'
)

COMPOSER_JS = f"""(() => {{
  const all = [...document.querySelectorAll({json.dumps(COMP_SEL_SHARED)})];
  return all.find(el => {{
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    for (let n = el; n; n = n.parentElement) {{
      const st = getComputedStyle(n);
      if (n.hidden || st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    }}
    return true;
  }}) || all[all.length - 1] || null;
}})()"""

SEND_SEL_SHARED = (
    'form[data-chatgpt-composer] [data-testid="send-button"], '
    'form[data-chatgpt-composer] button[type="submit"], '
    '[data-testid="send-button"], #composer-submit-button, '
    'button[aria-label="Send prompt" i], button[aria-label="Send message" i], '
    'form[data-chatgpt-composer] button[aria-label*="Send"], '
    'form[data-chatgpt-composer] button[aria-label*="Gửi"]'
)

STOP_SEL_SHARED = (
    'button[data-testid="stop-button"], '
    '#composer-submit-button[aria-label*="Stop" i], '
    '#composer-submit-button[aria-label*="Dừng" i], '
    'button[aria-label="Stop streaming" i], '
    'button[aria-label="Stop generating" i], '
    'button[aria-label="Stop response" i], '
    'form button[aria-label*="Stop" i], '
    'form button[aria-label*="Dừng" i], '
    'button.size-token-button-composer[aria-label*="Stop" i], '
    'button.size-token-button-composer[aria-label*="Dừng" i], '
    'button[type="button"].size-token-button-composer:has(svg.icon-primary-action > path[d^="M4.5 5.75"])'
)

TURN_SEL_SHARED = (
    '[data-turn-key]:has([data-conversation-role="assistant"], [data-chatgpt-agent-turn-start]), '
    '[data-testid^="conversation-turn-"]:has([data-message-author-role="assistant"]), '
    '[data-message-author-role="assistant"]'
)

USER_SEL_SHARED = (
    '[data-turn-key]:has([data-user-message-bubble]), '
    '.bg-user-message, '
    '[data-testid^="conversation-turn-"]:has([data-message-author-role="user"]), '
    '[data-message-author-role="user"]'
)

ASST_DONE_SEL = (
    'button[data-testid="copy-turn-action-button"], '
    'button[data-testid="good-response-turn-action-button"], '
    'button[data-testid="bad-response-turn-action-button"], '
    'button[aria-label="Rate response" i], '
    'button[aria-label="Read aloud" i], '
    'button[aria-label="Regenerate response" i], '
    'button[aria-label="Copy" i], '
    'button[aria-label="Good response" i]'
)

BUSY_JS = f"""(() => {{
  const stops = [...document.querySelectorAll({json.dumps(STOP_SEL_SHARED)})].filter(el => {{
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    for (let n = el; n; n = n.parentElement) {{
      const st = getComputedStyle(n);
      if (n.hidden || st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    }}
    return true;
  }});
  if (!stops.length) return false;
  const turns = [...document.querySelectorAll({json.dumps(TURN_SEL_SHARED)})];
  const lastTurn = turns[turns.length - 1];
  if (lastTurn && lastTurn.querySelector({json.dumps(ASST_DONE_SEL)})) return false;
  return true;
}})()"""

LIST_IMAGES_EXPR = """(() => {
  const imgs = [...document.querySelectorAll('img')];
  for (const i of imgs) {
    if ((i.alt || '').startsWith('Generated image') || i.closest('[class*="generated-image"]')) {
      if (i.loading === 'lazy') i.loading = 'eager';
    }
  }
  return imgs.filter(i => {
    const src = i.currentSrc || i.src || '';
    if (!src || src.includes('avatars') || src.includes('favicon') || src.includes('/cdn-cgi/') || src.includes('profile')) return false;
    const alt = (i.alt || '').trim();
    const inGenBox = !!i.closest('[class*="generated-image"]');
    const inTurn = inGenBox || !!i.closest('[data-turn-key]:has([data-conversation-role="assistant"], [data-chatgpt-agent-turn-start]), [class*="MarkdownRoot"], [data-message-author-role="assistant"], article, [data-testid^="conversation-turn"]');
    return alt.startsWith('Generated image') || inGenBox || (inTurn && (i.naturalWidth >= 180 || i.width >= 180));
  }).map(i => ({
    src: i.currentSrc || i.src,
    alt: (i.alt || 'Generated image').trim(),
    w: i.naturalWidth || i.width || 0,
    h: i.naturalHeight || i.height || 0,
    complete: !!i.complete
  }));
})()"""


# ---------------------------------------------------------------------------
# Minimal Pure-Python RFC 6455 WebSocket + CDP Client
# ---------------------------------------------------------------------------


class ChatGPTWebError(RuntimeError):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class _MinimalWebSocket:
    """Zero-dependency RFC 6455 client for local ws:// CDP endpoints."""

    def __init__(self, ws_url: str, connect_timeout: float = 4.0):
        parsed = urllib.parse.urlparse(ws_url)
        if parsed.scheme != "ws":
            raise ChatGPTWebError(f"Unsupported CDP WebSocket scheme: {parsed.scheme}", 502)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._buf = bytearray()
        self._closed = False

        sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._sock.sendall(req)

        # Read HTTP 101 handshake response
        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                self.close()
                raise ChatGPTWebError("CDP WebSocket closed during handshake", 502)
            self._buf.extend(chunk)

        header_end = self._buf.index(b"\r\n\r\n")
        header_bytes = bytes(self._buf[:header_end])
        del self._buf[: header_end + 4]

        status_line = header_bytes.split(b"\r\n", 1)[0].decode("latin1", errors="ignore")
        if "101" not in status_line:
            self.close()
            raise ChatGPTWebError(f"CDP WebSocket handshake failed: {status_line}", 502)

    def _recv_exact(self, n: int, deadline: float) -> bytes:
        while len(self._buf) < n:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise TimeoutError("CDP WebSocket read timeout")
            self._sock.settimeout(min(rem, 5.0))
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                self._closed = True
                raise ConnectionError("CDP WebSocket closed by peer")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def send_text(self, text: str) -> None:
        if self._closed:
            raise ConnectionError("CDP WebSocket is closed")
        payload = text.encode("utf-8")
        length = len(payload)
        mask_key = os.urandom(4)

        header = bytearray()
        header.append(0x81)  # FIN + text frame
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + mask_key + masked)

    def _send_pong(self, payload: bytes) -> None:
        p = payload[:125]
        mask_key = os.urandom(4)
        header = bytes([0x8A, 0x80 | len(p)])
        masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(p))
        try:
            self._sock.sendall(header + mask_key + masked)
        except OSError:
            pass

    def recv_text(self, timeout: float = 10.0) -> str:
        deadline = time.monotonic() + timeout
        fragments: list[bytes] = []
        while True:
            hdr = self._recv_exact(2, deadline)
            b0, b1 = hdr[0], hdr[1]
            fin = (b0 & 0x80) != 0
            opcode = b0 & 0x0F
            is_masked = (b1 & 0x80) != 0
            length = b1 & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2, deadline))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8, deadline))[0]

            mask_key = self._recv_exact(4, deadline) if is_masked else b""
            payload = self._recv_exact(length, deadline) if length > 0 else b""
            if is_masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))

            if opcode == 0x8:  # close
                self._closed = True
                raise ConnectionError("CDP WebSocket closed by browser")
            if opcode == 0x9:  # ping
                self._send_pong(payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode in (0x0, 0x1, 0x2):
                fragments.append(payload)
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="replace")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


class CDPSession:
    """Synchronous Chrome DevTools Protocol session over a single WebSocket."""

    def __init__(self, ws_url: str, connect_timeout: float = 4.0):
        self._ws = _MinimalWebSocket(ws_url, connect_timeout=connect_timeout)
        self._seq = 0

    def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        self._seq += 1
        req_id = self._seq
        self._ws.send_text(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise ChatGPTWebError(f"CDP timeout waiting for {method}", 504)
            raw = self._ws.recv_text(timeout=rem)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != req_id:
                continue
            if "error" in msg and msg["error"]:
                err_msg = msg["error"].get("message") or str(msg["error"])
                raise ChatGPTWebError(f"CDP error ({method}): {err_msg}", 502)
            return msg.get("result") or {}

    def evaluate(self, expression: str, timeout: float = 10.0, await_promise: bool = True) -> Any:
        res = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": bool(await_promise),
                "returnByValue": True,
            },
            timeout=timeout,
        )
        return (res.get("result") or {}).get("value")

    def close(self) -> None:
        self._ws.close()

    def __enter__(self) -> "CDPSession":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _cdp_eval_once(ws_url: str, expression: str, timeout: float = 10.0, await_promise: bool = True) -> Any:
    with CDPSession(ws_url) as s:
        return s.evaluate(expression, timeout=timeout, await_promise=await_promise)


# ---------------------------------------------------------------------------
# HTTP & Launcher Discovery Helpers
# ---------------------------------------------------------------------------


def _http_get_json(url: str, timeout: float = 2.5, method: str = "GET") -> Any:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _is_cdp_alive(endpoint: str, timeout: float = 1.5) -> bool:
    if not endpoint:
        return False
    try:
        data = _http_get_json(f"{endpoint.rstrip('/')}/json/version", timeout=timeout)
        return isinstance(data, dict) and bool(data.get("Browser") or data.get("webSocketDebuggerUrl"))
    except Exception:
        return False


def _candidate_launcher_json_paths() -> list[str]:
    paths: list[str] = []
    env_p = os.environ.get("LAUNCHER_BROWSER_JSON", "").strip()
    if env_p:
        paths.append(env_p)
    home = os.path.expanduser("~")
    paths.append(os.path.join(home, ".codex-chatgpt-web", "runtime", "launcher-browser.json"))
    paths.append(os.path.join(home, ".codex-chatgpt-web-dev", "runtime", "launcher-browser.json"))
    return paths


def get_launcher_endpoint(force: bool = False) -> str:
    """
    Returns the active CDP HTTP endpoint (e.g. 'http://127.0.0.1:62691') if reachable,
    or '' if no ChatGPT CDP browser is currently running.
    """
    now = time.monotonic()
    if not force and _EP_CACHE["value"] and (now - _EP_CACHE["at"] < 2.0):
        return str(_EP_CACHE["value"])

    candidates: list[str] = []
    env_ep = os.environ.get("CHATGPT_CDP_ENDPOINT", "").strip().rstrip("/")
    if env_ep.startswith("http"):
        candidates.append(env_ep)

    for jp in _candidate_launcher_json_paths():
        if os.path.isfile(jp):
            try:
                with open(jp, "r", encoding="utf-8") as f:
                    j = json.load(f)
                ep = str(j.get("endpoint") or "").strip().rstrip("/")
                if ep.startswith("http") and ep not in candidates:
                    candidates.append(ep)
            except Exception:
                pass

    # Fallback probe for standalone server-browser.mjs port (59637)
    for fallback_port in (59637,):
        ep = f"http://127.0.0.1:{fallback_port}"
        if ep not in candidates:
            candidates.append(ep)

    for ep in candidates:
        if _is_cdp_alive(ep, timeout=1.5):
            _EP_CACHE["at"] = now
            _EP_CACHE["value"] = ep
            return ep

    _EP_CACHE["at"] = now
    _EP_CACHE["value"] = ""
    return ""


def is_chatgpt_image_available(force: bool = False) -> bool:
    return bool(get_launcher_endpoint(force=force))


def _list_cdp_targets(endpoint: str, timeout: float = 2.5) -> list[dict[str, Any]]:
    try:
        data = _http_get_json(f"{endpoint}/json", timeout=timeout)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _open_new_chatgpt_tab(endpoint: str) -> None:
    new_url = f"{endpoint}/json/new?{urllib.parse.quote('https://chatgpt.com/', safe='')}"
    try:
        _http_get_json(new_url, timeout=3.0, method="PUT")
    except Exception:
        try:
            _http_get_json(new_url, timeout=3.0, method="GET")
        except Exception:
            pass


def get_chatgpt_targets(force: bool = False) -> list[dict[str, Any]]:
    ep = get_launcher_endpoint(force=force)
    if not ep:
        return []
    targets = _list_cdp_targets(ep)
    active = [
        t
        for t in targets
        if t.get("type") == "page"
        and isinstance(t.get("url"), str)
        and t["url"].startswith("https://chatgpt.com")
        and t.get("webSocketDebuggerUrl")
    ]
    active.sort(key=lambda t: int("temporary-chat=true" in t.get("url", "")))
    if active:
        return active

    # Navigate an idle Codex Web GPT surface or about:blank tab
    idle = next(
        (
            t
            for t in targets
            if t.get("type") == "page"
            and isinstance(t.get("url"), str)
            and ("codex-web-gpt-browser-host" in t["url"] or t["url"] == "about:blank")
            and t.get("webSocketDebuggerUrl")
        ),
        None,
    )
    if idle:
        try:
            with CDPSession(idle["webSocketDebuggerUrl"]) as s:
                s.send("Page.navigate", {"url": "https://chatgpt.com/"}, timeout=4.0)
                for _ in range(25):
                    time.sleep(0.15)
                    try:
                        ready = s.evaluate(
                            "location.origin === 'https://chatgpt.com' && !!document.querySelector('form[data-chatgpt-composer] [contenteditable=\"true\"], #prompt-textarea')",
                            timeout=2.0,
                            await_promise=False,
                        )
                        if ready:
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        refreshed = _list_cdp_targets(ep)
        found = [
            t
            for t in refreshed
            if t.get("type") == "page"
            and isinstance(t.get("url"), str)
            and t["url"].startswith("https://chatgpt.com")
            and t.get("webSocketDebuggerUrl")
        ]
        if found:
            return found

    # Auto-create a ChatGPT tab if none exists
    _open_new_chatgpt_tab(ep)
    for _ in range(20):
        time.sleep(0.2)
        refreshed = _list_cdp_targets(ep)
        found = [
            t
            for t in refreshed
            if t.get("type") == "page"
            and isinstance(t.get("url"), str)
            and t["url"].startswith("https://chatgpt.com")
            and t.get("webSocketDebuggerUrl")
        ]
        if found:
            return found
    return []


def _pick_idle_target() -> dict[str, Any] | None:
    targets = get_chatgpt_targets(force=True)
    if not targets:
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for t in targets:
        try:
            with CDPSession(t["webSocketDebuggerUrl"]) as s:
                v = s.evaluate(
                    f"({{ busy: {BUSY_JS}, hidden: document.visibilityState === 'hidden', composer: !!{COMPOSER_JS} }})",
                    timeout=4.0,
                    await_promise=False,
                )
                if isinstance(v, dict) and not v.get("busy"):
                    score = (2 if v.get("hidden") else 0) + (1 if v.get("composer") else 0)
                    scored.append((score, t))
        except Exception:
            pass
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    return targets[0]


# ---------------------------------------------------------------------------
# Account / Session Management
# ---------------------------------------------------------------------------


def get_chatgpt_account_info(force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    if not force and _ACCOUNT_CACHE["value"] is not None and (now - _ACCOUNT_CACHE["at"] < 8.0):
        return dict(_ACCOUNT_CACHE["value"])

    ep = get_launcher_endpoint(force=force)
    if not ep:
        out = {
            "connected": False,
            "logged_in": False,
            "endpoint": "",
            "tab_count": 0,
            "name": "",
            "email": "",
            "plan": "",
        }
        _ACCOUNT_CACHE["at"] = now
        _ACCOUNT_CACHE["value"] = out
        return out

    targets = get_chatgpt_targets()
    if not targets:
        out = {
            "connected": True,
            "logged_in": False,
            "endpoint": ep,
            "tab_count": 0,
            "name": "",
            "email": "",
            "plan": "",
        }
        _ACCOUNT_CACHE["at"] = now
        _ACCOUNT_CACHE["value"] = out
        return out

    info = None
    try:
        info = _cdp_eval_once(
            targets[0]["webSocketDebuggerUrl"],
            """(async () => {
              try {
                const r = await fetch('/api/auth/session', { credentials: 'include' });
                if (!r.ok) return { loggedIn: false };
                const j = await r.json();
                const u = j?.user || {};
                return {
                  loggedIn: !!(u.email || u.name || j?.accessToken),
                  name: u.name || '',
                  email: u.email || '',
                  plan: j?.accountCheck?.accountPlan?.planType || j?.account?.planType || u.plan || ''
                };
              } catch {
                return { loggedIn: !!document.querySelector('[data-testid="profile-button"], nav') };
              }
            })()""",
            timeout=5.0,
            await_promise=True,
        )
    except Exception:
        info = None

    out = {
        "connected": True,
        "endpoint": ep,
        "tab_count": len(targets),
        "logged_in": bool((info or {}).get("loggedIn")),
        "name": str((info or {}).get("name") or ""),
        "email": str((info or {}).get("email") or ""),
        "plan": str((info or {}).get("plan") or ""),
    }
    _ACCOUNT_CACHE["at"] = now
    _ACCOUNT_CACHE["value"] = out
    return out


def export_chatgpt_session() -> dict[str, Any]:
    ep = get_launcher_endpoint(force=True)
    if not ep:
        raise ChatGPTWebError("No active ChatGPT CDP browser found.", 503)
    targets = get_chatgpt_targets()
    if not targets:
        raise ChatGPTWebError("No ChatGPT browser tab found.", 502)
    with CDPSession(targets[0]["webSocketDebuggerUrl"]) as s:
        try:
            s.send("Network.enable", {}, timeout=3.0)
        except Exception:
            pass
        res = s.send("Network.getAllCookies", {}, timeout=6.0)
        raw_cookies = res.get("cookies") if isinstance(res, dict) else []
        cookies = [
            c
            for c in (raw_cookies or [])
            if re.search(r"chatgpt\.com|openai\.com|auth0\.com", str(c.get("domain") or ""), re.I)
        ]
        try:
            ua = str(s.evaluate("navigator.userAgent", timeout=3.0, await_promise=False) or "")
        except Exception:
            ua = ""
    account = get_chatgpt_account_info(force=True)
    return {"ok": True, "cookies": cookies, "userAgent": ua, "account": account}


def import_chatgpt_session(
    cookies: list[dict[str, Any]],
    user_agent: str = "",
    persist: bool = True,
) -> dict[str, Any]:
    if not isinstance(cookies, list) or not cookies:
        raise ChatGPTWebError("Empty session cookies list.", 400)
    if persist:
        os.makedirs(os.path.dirname(SAVED_SESSION_PATH), exist_ok=True)
        with open(SAVED_SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "userAgent": user_agent, "updatedAt": int(time.time() * 1000)}, f, indent=2)

    ep = get_launcher_endpoint(force=True)
    if not ep:
        return {
            "ok": True,
            "persisted_only": True,
            "imported_count": len(cookies),
            "message": "Saved ChatGPT session to disk; will apply once the CDP browser is running.",
        }

    targets = get_chatgpt_targets()
    if not targets:
        _open_new_chatgpt_tab(ep)
        time.sleep(0.9)
        targets = get_chatgpt_targets()
    if not targets:
        return {
            "ok": True,
            "persisted_only": True,
            "imported_count": len(cookies),
            "message": "Saved ChatGPT session to disk.",
        }

    normalized = []
    for c in cookies:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        item: dict[str, Any] = {
            "name": name,
            "value": str(c.get("value") or ""),
            "domain": str(c.get("domain") or ".chatgpt.com"),
            "path": str(c.get("path") or "/"),
            "secure": c.get("secure", True) is not False,
            "httpOnly": bool(c.get("httpOnly")),
        }
        same_site = str(c.get("sameSite") or "")
        if same_site in ("Strict", "Lax", "None"):
            item["sameSite"] = same_site
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp > 0:
            item["expires"] = float(exp)
        normalized.append(item)

    with CDPSession(targets[0]["webSocketDebuggerUrl"]) as s:
        for method, params in (
            ("Network.enable", {}),
            ("Page.enable", {}),
            (
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"},
            ),
        ):
            try:
                s.send(method, params, timeout=3.0)
            except Exception:
                pass
        clean_ua = re.sub(r"HeadlessChrome", "Chrome", str(user_agent or ""), flags=re.I).strip()
        if clean_ua:
            try:
                s.send("Network.setUserAgentOverride", {"userAgent": clean_ua}, timeout=3.0)
            except Exception:
                pass
        s.send("Network.setCookies", {"cookies": normalized}, timeout=8.0)
        try:
            s.send("Storage.setCookies", {"cookies": normalized}, timeout=8.0)
        except Exception:
            pass
        try:
            s.send("Page.navigate", {"url": "https://chatgpt.com/"}, timeout=6.0)
        except Exception:
            pass

    time.sleep(2.5)
    _ACCOUNT_CACHE["at"] = 0.0
    _ACCOUNT_CACHE["value"] = None
    account = get_chatgpt_account_info(force=True)
    return {"ok": True, "imported_count": len(normalized), "account": account}


def launch_chatgpt_browser(wait_sec: float = 15.0) -> dict[str, Any]:
    """
    Starts Codex Web GPT Launcher (if installed) or Chrome/Edge with persistent profile
    on CDP port 59637 so ChatGPT Web image generation becomes ready.
    """
    existing = get_launcher_endpoint(force=True)
    if existing:
        return {"ok": True, "already_running": True, "endpoint": existing, "account": get_chatgpt_account_info(force=True)}

    home = os.path.expanduser("~")
    codex_exe = os.path.join(home, "AppData", "Local", "Programs", "Codex Web GPT", "Codex Web GPT.exe")
    if os.name == "nt" and os.path.isfile(codex_exe):
        subprocess.Popen([codex_exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        candidates = (
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]
            if os.name == "nt"
            else ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
        )
        chrome_bin = os.environ.get("CHROME_BIN") or next((p for p in candidates if os.path.isfile(p)), "")
        if not chrome_bin:
            raise ChatGPTWebError("Neither Codex Web GPT Launcher nor Chrome/Edge was found on this machine.", 503)
        cdp_port = int(os.environ.get("CHATGPT_CDP_PORT", "59637"))
        profile_dir = os.path.join(home, ".codex-chatgpt-web", "chromium-profile")
        runtime_dir = os.path.join(home, ".codex-chatgpt-web", "runtime")
        os.makedirs(profile_dir, exist_ok=True)
        os.makedirs(runtime_dir, exist_ok=True)
        args = [
            chrome_bin,
            f"--remote-debugging-port={cdp_port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,900",
            "https://chatgpt.com/",
        ]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + max(3.0, wait_sec)
    while time.monotonic() < deadline:
        time.sleep(1.0)
        ep = get_launcher_endpoint(force=True)
        if ep:
            return {"ok": True, "already_running": False, "endpoint": ep, "account": get_chatgpt_account_info(force=True)}

    return {
        "ok": False,
        "endpoint": "",
        "message": "Browser process launched but CDP endpoint did not become ready within timeout.",
    }


# ---------------------------------------------------------------------------
# Image Encoding / Extraction Helpers
# ---------------------------------------------------------------------------


def _file_to_data_url(path: str) -> str:
    if not path or not os.path.isfile(path):
        raise ChatGPTWebError(f"Reference image not found: {path}", 400)
    with open(path, "rb") as f:
        raw = f.read()
    if not raw:
        raise ChatGPTWebError(f"Reference image is empty: {path}", 400)
    if len(raw) > MAX_UPLOAD_IMAGE_BYTES:
        raise ChatGPTWebError(f"Reference image exceeds 10MB limit: {path}", 413)
    mime = "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    else:
        guessed, _ = mimetypes.guess_type(path)
        if guessed and guessed.startswith("image/"):
            mime = guessed
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _detect_ext_from_bytes(raw: bytes, fallback_mime: str = "image/png") -> tuple[str, str]:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    m = (fallback_mime or "image/png").lower()
    if "jpeg" in m or "jpg" in m:
        return ".jpg", "image/jpeg"
    if "webp" in m:
        return ".webp", "image/webp"
    return ".png", "image/png"


def _fetch_image_from_target(ws_url: str, src: str) -> tuple[str, bytes] | None:
    expr = f"""(async () => {{
      const s = {json.dumps(src)};
      const r = await fetch(s, {{ credentials: 'include' }});
      if (!r.ok) return null;
      const b = await r.blob();
      const dataUrl = await new Promise((ok, no) => {{
        const fr = new FileReader();
        fr.onload = () => ok(fr.result);
        fr.onerror = no;
        fr.readAsDataURL(b);
      }});
      return {{ mime: b.type || 'image/png', dataUrl }};
    }})()"""
    res = _cdp_eval_once(ws_url, expr, timeout=20.0, await_promise=True)
    if not isinstance(res, dict) or not res.get("dataUrl"):
        return None
    m = re.match(r"^data:([^;]+);base64,(.+)$", str(res["dataUrl"]), flags=re.S)
    if not m:
        return None
    mime = str(res.get("mime") or m.group(1) or "image/png")
    raw = base64.b64decode(m.group(2))
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    return mime, raw


def _snapshot_known_images(targets: list[dict[str, Any]]) -> set[str]:
    known: set[str] = set()
    for t in targets:
        try:
            imgs = _cdp_eval_once(t["webSocketDebuggerUrl"], LIST_IMAGES_EXPR, timeout=3.5, await_promise=False)
            if isinstance(imgs, list):
                for item in imgs:
                    if isinstance(item, dict) and item.get("src"):
                        known.add(str(item["src"]))
        except Exception:
            pass
    return known


def _build_image_prompt(prompt: str, aspect_ratio: str = "16:9", has_refs: bool = False) -> str:
    clean = re.sub(r"^\s*/(anh|ảnh|image)\b\s*", "", str(prompt or ""), flags=re.I).strip()
    clean = clean[:MAX_PROMPT_CHARS]
    if not clean:
        raise ChatGPTWebError("Missing required prompt for ChatGPT Web image generation.", 400)

    ratio_key = str(aspect_ratio or "").strip()
    ratio_hint = ASPECT_RATIO_HINTS.get(ratio_key, "")
    if ratio_hint and ratio_key not in clean:
        clean = f"{clean}. Aspect ratio: {ratio_hint} ({ratio_key})."

    if re.match(
        r"^(tạo\s+ảnh|vẽ\s+ảnh|hãy\s+vẽ|generate\s+an?\s+image|draw\s+an?\s+image|create\s+an?\s+image|edit\s+this\s+image)",
        clean,
        flags=re.I,
    ):
        return clean
    if has_refs:
        return f"Generate an image based on the attached reference image(s): {clean}"
    return f"Generate an image: {clean}"


# ---------------------------------------------------------------------------
# Main Image Generation Entry Point
# ---------------------------------------------------------------------------


def generate_chatgpt_image(
    prompt: str,
    output_path: str,
    aspect_ratio: str = "16:9",
    image_files: list[str] | None = None,
    conversation_id: str = "",
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    on_status: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """
    Generates an image using the live ChatGPT Web browser session via CDP and saves
    the result to `output_path` (creating parent directories as needed).
    """
    def _emit(stage: str, msg: str) -> None:
        if callable(on_status):
            try:
                on_status(stage, msg)
            except Exception:
                pass

    ref_data_urls: list[str] = []
    for p in (image_files or [])[:3]:
        if p and os.path.isfile(p):
            ref_data_urls.append(_file_to_data_url(p))

    full_prompt = _build_image_prompt(prompt, aspect_ratio=aspect_ratio, has_refs=bool(ref_data_urls))

    with _IMAGE_LOCK:
        _emit("connect", "Connecting to ChatGPT Web CDP session...")
        if not is_chatgpt_image_available(force=True):
            raise ChatGPTWebError(
                "ChatGPT Web CDP browser is not running. Open Codex Web GPT Launcher "
                "(or call POST /api/chatgpt/launch) and ensure ChatGPT is signed in.",
                503,
            )

        target = _pick_idle_target()
        if not target:
            raise ChatGPTWebError(
                "No idle ChatGPT Web tab found in the CDP browser. Ensure https://chatgpt.com is open and signed in.",
                503,
            )

        ws_url = target["webSocketDebuggerUrl"]
        target_conv_id = str(conversation_id or "").strip()
        if not re.fullmatch(r"[a-f0-9-]{20,}", target_conv_id, flags=re.I):
            target_conv_id = ""

        with CDPSession(ws_url) as s:
            try:
                s.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, timeout=2.0)
            except Exception:
                pass

            page_check = None
            try:
                page_check = s.evaluate(
                    f"""({{
                      onChatGpt: location.origin === 'https://chatgpt.com',
                      isTemp: location.search.includes('temporary-chat'),
                      pathname: location.pathname,
                      users: document.querySelectorAll({json.dumps(USER_SEL_SHARED)}).length,
                      hasComp: !!{COMPOSER_JS},
                      busy: {BUSY_JS}
                    }})""",
                    timeout=3.0,
                    await_promise=False,
                )
            except Exception:
                page_check = None

            if isinstance(page_check, dict) and page_check.get("busy"):
                try:
                    s.evaluate(
                        f"(() => {{ const b = document.querySelector({json.dumps(STOP_SEL_SHARED)}); if (b) b.click(); }})()",
                        timeout=2.0,
                        await_promise=False,
                    )
                except Exception:
                    pass
                time.sleep(0.35)

            desired_url = f"https://chatgpt.com/c/{target_conv_id}" if target_conv_id else "https://chatgpt.com/"
            needs_nav = (
                not isinstance(page_check, dict)
                or not page_check.get("onChatGpt")
                or page_check.get("isTemp")
                or not page_check.get("hasComp")
                or (
                    page_check.get("pathname") != f"/c/{target_conv_id}"
                    if target_conv_id
                    else (page_check.get("users") or 0) > 0
                )
            )
            if needs_nav:
                _emit("navigate", f"Opening {desired_url}...")
                s.send("Page.navigate", {"url": desired_url}, timeout=6.0)
                ok = False
                for _ in range(35):
                    time.sleep(0.3)
                    try:
                        ok = bool(
                            s.evaluate(
                                f"document.readyState !== 'loading' && !!{COMPOSER_JS} && !location.search.includes('temporary-chat')",
                                timeout=2.5,
                                await_promise=False,
                            )
                        )
                        if ok:
                            break
                    except Exception:
                        pass
                if not ok:
                    raise ChatGPTWebError(
                        "ChatGPT Web composer did not load. Check that you are signed in inside Codex Web GPT Launcher.",
                        502,
                    )

            # Snapshot existing images & baseline counts BEFORE sending
            known_srcs = _snapshot_known_images(get_chatgpt_targets())
            base = s.evaluate(
                f"""(() => {{
                  const el = {COMPOSER_JS};
                  if (!el) return {{ ok: false }};
                  const baseTurns = document.querySelectorAll({json.dumps(TURN_SEL_SHARED)}).length;
                  const baseUsers = document.querySelectorAll({json.dumps(USER_SEL_SHARED)}).length;
                  el.focus();
                  const sel = window.getSelection();
                  if (sel) {{
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    sel.removeAllRanges();
                    sel.addRange(range);
                  }}
                  document.execCommand('delete', false);
                  return {{ ok: true, baseTurns, baseUsers }};
                }})()""",
                timeout=3.5,
                await_promise=False,
            )
            if not isinstance(base, dict) or not base.get("ok"):
                raise ChatGPTWebError("Could not focus ChatGPT Web composer input.", 502)

            # Paste reference images if provided
            if ref_data_urls:
                _emit("attach", f"Attaching {len(ref_data_urls)} reference image(s) to ChatGPT Web...")
                s.evaluate(
                    f"""(() => {{
                      const el = {COMPOSER_JS};
                      if (!el) return false;
                      el.focus();
                      const imgDataUrls = {json.dumps(ref_data_urls)};
                      const dtImg = new DataTransfer();
                      for (let idx = 0; idx < imgDataUrls.length; idx++) {{
                        const du = imgDataUrls[idx];
                        const m = du.match(/^data:([^;]+);base64,(.+)$/);
                        if (!m) continue;
                        const mime = m[1];
                        const ext = (mime.split('/')[1] || 'png').replace('jpeg', 'jpg');
                        const bin = atob(m[2]);
                        const u8 = new Uint8Array(bin.length);
                        for (let k = 0; k < bin.length; k++) u8[k] = bin.charCodeAt(k);
                        dtImg.items.add(new File([u8], 'ref-' + (idx + 1) + '.' + ext, {{ type: mime }}));
                      }}
                      el.dispatchEvent(new ClipboardEvent('paste', {{ clipboardData: dtImg, bubbles: true, cancelable: true }}));
                      return true;
                    }})()""",
                    timeout=8.0,
                    await_promise=False,
                )
                time.sleep(0.9)

            _emit("send", "Sending image prompt to ChatGPT Web...")
            inserted = s.evaluate(
                f"""(() => {{
                  const el = {COMPOSER_JS};
                  if (!el) return {{ ok: false, len: 0 }};
                  if (document.activeElement !== el) el.focus();
                  const sel = window.getSelection();
                  if (sel) {{
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    range.collapse(false);
                    sel.removeAllRanges();
                    sel.addRange(range);
                  }}
                  const text = {json.dumps(full_prompt)};
                  const applied = document.execCommand('insertText', false, text);
                  let len = (el.textContent || '').trim().length;
                  if (!len) {{
                    const dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    el.dispatchEvent(new ClipboardEvent('paste', {{ clipboardData: dt, bubbles: true, cancelable: true }}));
                    len = (el.textContent || '').trim().length;
                  }}
                  return {{ ok: len > 0 || applied, len }};
                }})()""",
                timeout=4.0,
                await_promise=False,
            )
            if not isinstance(inserted, dict) or not inserted.get("len"):
                try:
                    s.send("Input.insertText", {"text": full_prompt}, timeout=3.0)
                except Exception:
                    pass

            # Wait for send button to become enabled AND any attachment upload spinner to finish
            wait_iters = 75 if ref_data_urls else 15
            for _ in range(wait_iters):
                try:
                    ready = s.evaluate(
                        f"""(() => {{
                          const el = {COMPOSER_JS};
                          const form = el?.closest('form') || document;
                          const uploading = !!form.querySelector('[role="progressbar"], [aria-busy="true"], [data-testid*="loading" i], [data-testid*="upload" i] svg.animate-spin');
                          if (uploading) return false;
                          const btn = form.querySelector('[data-testid="send-button"], button[type="submit"], #composer-submit-button')
                            || document.querySelector({json.dumps(SEND_SEL_SHARED)});
                          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
                        }})()""",
                        timeout=2.0,
                        await_promise=False,
                    )
                    if ready:
                        break
                except Exception:
                    pass
                time.sleep(0.2 if ref_data_urls else 0.08)

            def _trigger_submit() -> None:
                clicked = False
                try:
                    clicked = bool(
                        s.evaluate(
                            f"""(() => {{
                              const el = {COMPOSER_JS};
                              const form = el?.closest('form') || document;
                              const btn = form.querySelector('[data-testid="send-button"], button[type="submit"], #composer-submit-button')
                                || document.querySelector({json.dumps(SEND_SEL_SHARED)});
                              if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') {{
                                btn.focus();
                                btn.click();
                                return true;
                              }}
                              return false;
                            }})()""",
                            timeout=2.5,
                            await_promise=False,
                        )
                    )
                except Exception:
                    clicked = False
                if not clicked:
                    try:
                        s.evaluate(f"({COMPOSER_JS})?.focus()", timeout=2.0, await_promise=False)
                        s.send(
                            "Input.dispatchKeyEvent",
                            {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r"},
                            timeout=2.5,
                        )
                        s.send(
                            "Input.dispatchKeyEvent",
                            {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                            timeout=2.5,
                        )
                    except Exception:
                        pass

            _trigger_submit()

            sent = False
            base_turns = int(base.get("baseTurns") or 0)
            base_users = int(base.get("baseUsers") or 0)
            for i in range(25):
                time.sleep(0.1)
                try:
                    chk = s.evaluate(
                        f"""({{
                          busy: {BUSY_JS},
                          turns: document.querySelectorAll({json.dumps(TURN_SEL_SHARED)}).length,
                          users: document.querySelectorAll({json.dumps(USER_SEL_SHARED)}).length
                        }})""",
                        timeout=2.5,
                        await_promise=False,
                    )
                    if isinstance(chk, dict) and (
                        chk.get("busy") or int(chk.get("turns") or 0) > base_turns or int(chk.get("users") or 0) > base_users
                    ):
                        sent = True
                        break
                except Exception:
                    pass
                if i in (7, 15):
                    _trigger_submit()

            if not sent:
                raise ChatGPTWebError("Failed to submit image prompt to ChatGPT Web.", 502)

            _emit("drawing", "ChatGPT Web is generating the image (typically 20–50 seconds)...")
            deadline = time.monotonic() + max(30.0, float(timeout_sec))
            text_only_idle = 0
            ready_img_ticks = 0
            captured_conv_id = target_conv_id
            last_status = ""

            while time.monotonic() < deadline:
                time.sleep(0.9)
                try:
                    st = s.evaluate(
                        f"""(() => {{
                          const busy = {BUSY_JS};
                          const convId = (location.pathname.match(/^\\/c\\/([a-f0-9-]{{20,}})/i) || [])[1] || '';
                          const turns = [...document.querySelectorAll({json.dumps(TURN_SEL_SHARED)})];
                          const lastTurn = turns[turns.length - 1] || null;
                          const statusNodes = lastTurn ? [...lastTurn.querySelectorAll('[data-streaming-response-status], [data-testid^="cot-v5"]')] : [];
                          const statusText = statusNodes.map(n => (n.innerText || '').trim()).filter(Boolean).join(' · ').slice(0, 140);
                          const mds = lastTurn ? [...lastTurn.querySelectorAll('.markdown, [class*="MarkdownRoot"], [data-markdown-text-style="assistant-message"]')].filter(n => !n.closest('[data-user-message-bubble], [data-streaming-response-status], [data-testid^="cot-v5"]') && n.getAttribute('data-markdown-text-tone') !== 'tertiary') : [];
                          const text = mds.map(m => (m.innerText || '').trim()).filter(Boolean).join('\\n\\n').slice(0, 600);
                          const hasGenBox = !!(lastTurn && lastTurn.querySelector('[class*="generated-image"], img[alt^="Generated image"], img[alt*="Đã tạo hình ảnh"]'));
                          const allImgs = {LIST_IMAGES_EXPR};
                          return {{ busy, convId, turnCount: turns.length, statusText, text, hasGenBox, allImgs }};
                        }})()""",
                        timeout=6.0,
                        await_promise=False,
                    )
                except Exception:
                    st = None

                if not isinstance(st, dict):
                    continue

                if st.get("convId"):
                    captured_conv_id = str(st["convId"])

                status_text = str(st.get("statusText") or "").strip()
                if status_text and status_text != last_status:
                    last_status = status_text
                    _emit("drawing", status_text)

                all_imgs = st.get("allImgs") or []
                new_imgs = [
                    im
                    for im in all_imgs
                    if isinstance(im, dict)
                    and im.get("src")
                    and str(im["src"]) not in known_srcs
                    and im.get("complete")
                    and (int(im.get("w") or 0) >= 256 or str(im.get("alt") or "").startswith("Generated image"))
                ]

                full_res_ready = any(int(im.get("w") or 0) >= 512 and im.get("complete") for im in new_imgs)
                if full_res_ready:
                    ready_img_ticks += 1
                else:
                    ready_img_ticks = 0

                if new_imgs and (not st.get("busy") or ready_img_ticks >= 2):
                    _emit("download", "Image generated, downloading full-resolution output...")
                    saved_paths: list[str] = []
                    primary_mime = "image/png"
                    primary_size = 0
                    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                    base_stem, requested_ext = os.path.splitext(output_path)

                    # Take the latest generated image(s)
                    for idx, cand in enumerate(reversed(new_imgs)):
                        fetched = _fetch_image_from_target(ws_url, str(cand["src"]))
                        if not fetched:
                            continue
                        mime, raw = fetched
                        real_ext, real_mime = _detect_ext_from_bytes(raw, fallback_mime=mime)
                        target_ext = requested_ext if requested_ext.lower() in (".png", ".jpg", ".jpeg", ".webp") else real_ext
                        # If actual bytes are webp/jpg and user asked for .png, keep actual extension so MIME matches
                        if real_ext != target_ext.lower() and not (real_ext == ".jpg" and target_ext.lower() == ".jpeg"):
                            target_ext = real_ext
                        out_fp = f"{base_stem}{target_ext}" if idx == 0 else f"{base_stem}_{idx + 1}{target_ext}"
                        with open(out_fp, "wb") as f:
                            f.write(raw)
                        saved_paths.append(out_fp)
                        if idx == 0:
                            primary_mime = real_mime
                            primary_size = len(raw)
                        # Only need the primary image unless multiple were returned in this turn
                        if len(saved_paths) >= 4:
                            break

                    if saved_paths:
                        return {
                            "status": "ok",
                            "engine": "chatgpt-web-image",
                            "output_path": saved_paths[0],
                            "filename": os.path.basename(saved_paths[0]),
                            "images": saved_paths,
                            "mime": primary_mime,
                            "size_bytes": primary_size,
                            "aspect_ratio": aspect_ratio,
                            "reference_images_used": len(ref_data_urls),
                            "chatgpt_conversation_id": captured_conv_id,
                            "assistant_text": str(st.get("text") or ""),
                        }

                has_new_turn = int(st.get("turnCount") or 0) > base_turns
                if has_new_turn and not st.get("busy") and not st.get("hasGenBox") and not new_imgs and st.get("text"):
                    text_only_idle += 1
                    if text_only_idle >= 3:
                        raise ChatGPTWebError(
                            f"ChatGPT Web returned text without an image: {st.get('text')}",
                            422,
                        )
                else:
                    text_only_idle = 0

            raise ChatGPTWebError("Timed out waiting for ChatGPT Web image generation.", 504)


# ---------------------------------------------------------------------------
# CLI Utility for Local Testing
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatGPT Web Image Generation Backend (CDP)")
    parser.add_argument("--status", action="store_true", help="Inspect ChatGPT Web CDP & login status")
    parser.add_argument("--launch", action="store_true", help="Launch Codex Web GPT / Chrome CDP browser")
    parser.add_argument("--prompt", default="", help="Image generation prompt")
    parser.add_argument("--aspect-ratio", default="16:9", help="Aspect ratio (16:9, 1:1, 9:16, 4:3, 3:4)")
    parser.add_argument("--image", action="append", default=[], help="Optional reference image path (repeatable)")
    parser.add_argument("--output", default="", help="Output image path (default: outputs/chatgpt_img_<ts>.png)")
    parser.add_argument("--conversation-id", default="", help="Optional ChatGPT /c/<id> to continue editing")
    args = parser.parse_args()

    if args.launch:
        print(json.dumps(launch_chatgpt_browser(), ensure_ascii=False, indent=2))
        return 0

    if args.status or not args.prompt:
        print(json.dumps(get_chatgpt_account_info(force=True), ensure_ascii=False, indent=2))
        return 0

    out_path = args.output or os.path.join(DEFAULT_OUTPUTS_DIR, f"chatgpt_img_{int(time.time())}.png")
    res = generate_chatgpt_image(
        prompt=args.prompt,
        output_path=out_path,
        aspect_ratio=args.aspect_ratio,
        image_files=args.image,
        conversation_id=args.conversation_id,
        on_status=lambda stage, msg: print(f"[{stage}] {msg}", file=sys.stderr, flush=True),
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
