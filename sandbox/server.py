"""Loopback HTTP sandbox for the experimental context-menu prototype.

The sandbox is deliberately boring on the host side: it serves a fixed set of
bundled static files and a small same-origin JSON bridge to one shared
``MenuController`` per browser session. It never imports ``pymxs``/``max_host``,
never browses the filesystem, and never accepts scripts or profile paths.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import math
import secrets
import threading
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..core.catalog import ACTIONS
from ..core.controller import MenuController
from ..core.contracts import ContextLevel, MenuEvent
from ..core.profile import default_profile, dump_profile, load_profile
from ..core.simulated import SimulatedHost
from ..core import simulated as simulated_module

LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 64 * 1024
SESSION_COOKIE = "sandbox_session"
ALLOWED_EVENT_KINDS = frozenset(
    {
        "trigger_down",
        "trigger_up",
        "pointer_move",
        "click_row",
        "key_down",
        "query_changed",
        "outside_click",
        "focus_lost",
        "cancel",
    }
)
CONTEXTS = tuple(level.value for level in ContextLevel)
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Only this exact allowlist is reachable. There is no path joining or listing.
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

# Fallback names only matter if the core simulator does not publish its own
# scenario names. They mirror the plan's required presets.
FALLBACK_SCENARIOS = (
    "no_selection",
    "primitive",
    "editable_poly_object",
    "editable_poly_vertex",
    "editable_poly_edge",
    "editable_poly_border",
    "editable_poly_polygon",
    "editable_poly_element",
    "editable_poly_vertex_empty",
    "editable_poly_polygon_empty",
    "multiple_objects",
    "active_edit_poly",
    "conflicting_mode",
    "changed_target",
    "deleted_target",
)


class SandboxError(Exception):
    """An HTTP-visible sandbox error with a status code."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)


def _jsonable(value: Any) -> Any:
    """Convert frozen core dataclasses/enums into plain JSON-compatible data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _phase_name(view: Any) -> str:
    phase = getattr(view, "phase", "closed")
    return str(getattr(phase, "value", phase))


def _level_name(value: Any) -> str | None:
    if value is None:
        return None
    name = str(getattr(value, "value", value))
    return name if name in CONTEXTS else None


def _action_metadata() -> dict[str, dict[str, Any]]:
    specs = ACTIONS.values() if isinstance(ACTIONS, Mapping) else ACTIONS
    catalog: dict[str, dict[str, Any]] = {}
    for spec in specs:
        action_id = getattr(spec, "id", None)
        if not action_id:
            continue
        catalog[str(action_id)] = {
            "id": str(action_id),
            "label": str(getattr(spec, "label", action_id)),
            "aliases": _jsonable(getattr(spec, "aliases", ())),
            "group": str(getattr(spec, "group", "Actions")),
            "allowed_levels": _jsonable(getattr(spec, "allowed_levels", ())),
            "repeatable": bool(getattr(spec, "repeatable", False)),
            "execution_kind": _jsonable(getattr(spec, "execution_kind", "action")),
        }
    return catalog


def _discover_scenarios(host: SimulatedHost) -> list[str]:
    for owner in (simulated_module, type(host), host):
        for attribute in ("SCENARIOS", "SCENARIO_NAMES", "scenario_names", "scenarios"):
            value = getattr(owner, attribute, None)
            if value is None:
                continue
            try:
                if isinstance(value, Mapping):
                    names = [str(key) for key in value]
                else:
                    names = [str(item) for item in value]
            except TypeError:
                continue
            if names:
                return names
    return list(FALLBACK_SCENARIOS)


def _pretty_scenario(name: str) -> str:
    cleaned = name.replace("_", " ").replace("-", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else name


@dataclass
class _BrowserSession:
    key: str
    profile: Any
    controller: MenuController
    last_sequence: int = 0


class SandboxServer:
    """In-process sandbox server. Bind address is always loopback."""

    def __init__(
        self,
        host: SimulatedHost | None = None,
        *,
        profile: Any | None = None,
        port: int = 0,
        static_dir: Path | None = None,
    ) -> None:
        self.host = host if host is not None else SimulatedHost()
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.port = int(port)
        self.static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
        self._seed_profile = profile.clone() if profile is not None else None
        self._sessions: dict[str, _BrowserSession] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._static_cache = self._load_static()
        self.actions = _action_metadata()
        self.scenario_names = _discover_scenarios(self.host)
        self.scenario = self.scenario_names[0] if self.scenario_names else ""
        self.context_scenarios: dict[str, str] = {}
        self._initialise_scenarios()

    # -- lifecycle -----------------------------------------------------

    def _initialise_scenarios(self) -> None:
        initial = self.scenario
        if initial:
            try:
                self.host.set_scenario(initial)
            except (KeyError, ValueError, TypeError):
                LOGGER.debug("simulator rejected initial scenario %r", initial)
        # Learn which scenario represents each context without touching geometry.
        discovered: dict[str, list[str]] = {}
        for name in self.scenario_names:
            try:
                self.host.set_scenario(name)
                snapshot = self.host.snapshot()
            except Exception:  # pragma: no cover - simulator-specific rejection
                continue
            level = _level_name(getattr(snapshot, "level", None))
            if level:
                discovered.setdefault(level, []).append(name)
        # Prefer the canonical editable-poly preset over incidental scenarios
        # such as "no_selection" that happen to report the same level.
        for level, names in discovered.items():
            preferred = next(
                (name for name in names if name == f"editable_poly_{level}"),
                names[0],
            )
            self.context_scenarios[level] = preferred
        if initial:
            try:
                self.host.set_scenario(initial)
            except (KeyError, ValueError, TypeError):
                pass

    def _load_static(self) -> dict[str, tuple[bytes, str]]:
        cache: dict[str, tuple[bytes, str]] = {}
        for route, (filename, content_type) in STATIC_FILES.items():
            target = self.static_dir / filename
            data = target.read_bytes()
            if filename == "index.html":
                data = data.replace(b"__SANDBOX_TOKEN__", self.token.encode("utf-8"))
            cache[route] = (data, content_type)
        return cache

    @property
    def url(self) -> str:
        if self._httpd is not None:
            address, port = self._httpd.server_address[:2]
        else:
            address, port = "127.0.0.1", self.port
        return f"http://{address}:{port}/"

    def start(self) -> "SandboxServer":
        if self._httpd is not None:
            return self
        httpd = _HTTPServer(("127.0.0.1", self.port), SandboxHandler)
        httpd.sandbox = self
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        return self

    def serve_forever(self) -> None:
        self.start()
        assert self._httpd is not None
        self._httpd.serve_forever(poll_interval=0.2)

    def start_background(self) -> "SandboxServer":
        """Start serving in a daemon thread; useful for tests and the CLI."""
        self.start()
        assert self._httpd is not None
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def __enter__(self) -> "SandboxServer":
        return self.start_background()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- sessions ------------------------------------------------------

    def new_session(self, key: str | None = None) -> _BrowserSession:
        with self.lock:
            key = key or secrets.token_urlsafe(18)
            profile = (
                self._seed_profile.clone()
                if self._seed_profile is not None
                else default_profile()
            )
            controller = MenuController(self.host, profile)
            session = _BrowserSession(key=key, profile=profile, controller=controller)
            self._sessions[key] = session
            return session

    def get_session(self, key: str | None) -> _BrowserSession:
        with self.lock:
            if key and key in self._sessions:
                return self._sessions[key]
            # Never adopt an unknown client-chosen key: generate a fresh one and
            # return it through Set-Cookie. This avoids session fixation.
            return self.new_session()

    def drop_session(self, key: str) -> None:
        with self.lock:
            session = self._sessions.pop(key, None)
            if session is not None:
                try:
                    view = session.controller.view()
                    if _phase_name(view) != "closed":
                        session.controller.handle(
                            MenuEvent(
                                session_id=view.session_id,
                                sequence=int(view.sequence) + 1,
                                kind="cancel",
                                time_ms=0.0,
                                key="SessionClosed",
                            )
                        )
                except Exception:  # pragma: no cover - controller contract should allow this
                    LOGGER.debug("controller cancel during session drop failed", exc_info=True)

    # -- state ---------------------------------------------------------

    def snapshot(self) -> Any:
        try:
            return self.host.snapshot()
        except Exception:
            LOGGER.debug("snapshot failed", exc_info=True)
            return None

    def scenario_payload(self) -> list[dict[str, Any]]:
        reverse = {name: level for level, name in self.context_scenarios.items()}
        return [
            {"name": name, "label": _pretty_scenario(name), "level": reverse.get(name)}
            for name in self.scenario_names
        ]

    def state_payload(self, session: _BrowserSession, view: Any) -> dict[str, Any]:
        return {
            "view": _jsonable(view),
            "profile": _jsonable(dump_profile(session.profile)),
            "actions": self.actions,
            "snapshot": _jsonable(self.snapshot()),
            "scenarios": self.scenario_payload(),
            "scenario": self.scenario,
            "context_scenarios": dict(self.context_scenarios),
            "simulated": True,
        }

    # -- operations ----------------------------------------------------

    def open_menu(self, session: _BrowserSession, payload: Mapping[str, Any]) -> dict[str, Any]:
        invocation = str(payload.get("invocation", "latched"))
        if invocation not in {"held", "latched"}:
            raise SandboxError("invocation must be 'held' or 'latched'")
        time_ms = _number(payload.get("time_ms", 0), "time_ms")
        if time_ms < 0 or time_ms > 10_000_000:
            raise SandboxError("time_ms is outside the accepted range")
        with self.lock:
            view = session.controller.open((0.0, 0.0), time_ms, invocation=invocation)
            session.last_sequence = int(getattr(view, "sequence", 0) or 0)
            return self.state_payload(session, view)

    def handle_event(self, session: _BrowserSession, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = payload.get("event")
        if not isinstance(data, Mapping):
            raise SandboxError("event must be an object")
        kind = str(data.get("kind", ""))
        if kind not in ALLOWED_EVENT_KINDS:
            raise SandboxError("unknown event kind")
        session_id = str(data.get("session_id", ""))
        if not session_id:
            raise SandboxError("event.session_id is required")
        raw_sequence = data.get("sequence")
        if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
            raise SandboxError("event.sequence must be an integer")
        sequence = raw_sequence
        if sequence <= 0:
            raise SandboxError("event.sequence must be positive")
        time_ms = _number(data.get("time_ms", 0), "time_ms")
        if time_ms < 0 or time_ms > 10_000_000:
            raise SandboxError("event.time_ms is outside the accepted range")
        x = _number(data.get("x", 0), "x")
        y = _number(data.get("y", 0), "y")
        if abs(x) > 100_000 or abs(y) > 100_000:
            raise SandboxError("event coordinates are outside the accepted range")
        action_id = data.get("action_id") or None
        text = data.get("text") or ""
        key = data.get("key") or ""
        raw_repeat = data.get("auto_repeat", False)
        if not isinstance(raw_repeat, bool):
            raise SandboxError("event.auto_repeat must be a boolean")
        auto_repeat = raw_repeat
        if action_id is not None and not isinstance(action_id, str):
            raise SandboxError("event.action_id must be a string or null")
        if not isinstance(text, str) or not isinstance(key, str):
            raise SandboxError("event text/key must be strings")
        if len(text) > 128 or len(key) > 64:
            raise SandboxError("event text/key is too long")

        with self.lock:
            view = session.controller.view()
            if _phase_name(view) == "closed":
                raise SandboxError("menu is closed", HTTPStatus.CONFLICT)
            if str(getattr(view, "session_id", "")) != session_id:
                raise SandboxError("stale menu session", HTTPStatus.CONFLICT)
            if sequence <= session.last_sequence:
                raise SandboxError("duplicate or stale event sequence", HTTPStatus.CONFLICT)
            session.last_sequence = sequence
            event = MenuEvent(
                session_id=session_id,
                sequence=sequence,
                kind=kind,
                time_ms=time_ms,
                x=x,
                y=y,
                action_id=action_id,
                text=text,
                key=key,
                auto_repeat=auto_repeat,
            )
            try:
                view = session.controller.handle(event)
            except (ValueError, KeyError) as error:
                raise SandboxError(str(error), HTTPStatus.CONFLICT) from error
            return self.state_payload(session, view)

    def set_scenario(self, session: _BrowserSession, payload: Mapping[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise SandboxError("scenario name is required")
        if name not in self.scenario_names:
            raise SandboxError("unknown scenario", HTTPStatus.NOT_FOUND)
        with self.lock:
            try:
                self.host.set_scenario(name)
            except (KeyError, ValueError, TypeError) as error:
                raise SandboxError(f"scenario rejected: {error}") from error
            self.scenario = name
            return self.state_payload(session, session.controller.view())

    def set_profile(self, session: _BrowserSession, payload: Mapping[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", "set"))
        with self.lock:
            try:
                if action == "reset":
                    profile = default_profile()
                elif action == "set":
                    profile = load_profile(payload.get("profile"))
                elif action == "import":
                    text = payload.get("text")
                    if not isinstance(text, str):
                        raise SandboxError("profile import requires a JSON string")
                    if len(text) > MAX_BODY_BYTES:
                        raise SandboxError("profile import is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    try:
                        document = json.loads(text)
                    except json.JSONDecodeError as error:
                        raise SandboxError(
                            f"profile rejected: invalid JSON: {error.msg} at line {error.lineno}"
                        ) from error
                    profile = load_profile(document)
                elif action == "export":
                    return self.state_payload(session, session.controller.view())
                else:
                    raise SandboxError("unknown profile action")
            except SandboxError:
                raise
            except (ValueError, TypeError, KeyError) as error:
                raise SandboxError(f"profile rejected: {error}") from error
            try:
                view = session.controller.set_profile(profile)
            except (ValueError, TypeError, KeyError) as error:
                raise SandboxError(f"profile rejected: {error}") from error
            session.profile = profile
            return self.state_payload(session, view)


def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise SandboxError(f"{name} must be a number") from error
    if not math.isfinite(number):
        raise SandboxError(f"{name} must be finite")
    return number


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    sandbox: SandboxServer

    def server_bind(self) -> None:
        super().server_bind()
        # Deliberately do not call socket.getfqdn(): reverse DNS is unnecessary.
        self.server_name = "127.0.0.1"


class SandboxHandler(BaseHTTPRequestHandler):
    """Same-origin JSON bridge plus bundled static assets."""

    server_version = "ContextMenuLab/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    sandbox: SandboxServer

    def __init__(self, request, client_address, server) -> None:
        self.sandbox = server.sandbox
        super().__init__(request, client_address, server)

    # -- logging -------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        LOGGER.debug("%s - %s", self.address_string(), format % args)

    # -- helpers -------------------------------------------------------

    def _host_ok(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return False
        try:
            parsed = urlsplit(f"//{host}")
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if hostname not in ALLOWED_HOSTS:
            return False
        bound_port = int(self.server.server_address[1])
        return port in (None, bound_port)

    def _origin_ok(self) -> bool:
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = self.headers.get("Host", "")
        expected = f"http://{host}"
        return origin.rstrip("/") == expected.rstrip("/")

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-Sandbox-Token", "")
        if not supplied:
            return False
        return hmac.compare_digest(supplied, self.sandbox.token)

    def _session(self) -> _BrowserSession:
        key = None
        raw = self.headers.get("Cookie")
        if raw:
            cookie = SimpleCookie()
            try:
                cookie.load(raw)
            except Exception:
                cookie = SimpleCookie()
            morsel = cookie.get(SESSION_COOKIE)
            if morsel is not None:
                key = morsel.value
        session = self.sandbox.get_session(key)
        self._pending_cookie = session.key
        return session

    def _send(self, status: int, body: bytes | None, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(int(status))
        if body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if body is not None and content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
        if getattr(self, "_pending_cookie", None):
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={self._pending_cookie}; Path=/; HttpOnly; SameSite=Strict",
            )
            self._pending_cookie = None
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send(status, body)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        # Errors can occur before a request body is consumed; do not reuse a
        # connection with unread bytes.
        self.close_connection = True
        self._json(int(status), {"error": message, **extra})

    def _read_json(self) -> Mapping[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise SandboxError("chunked bodies are not accepted", HTTPStatus.LENGTH_REQUIRED)
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise SandboxError("Content-Type must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise SandboxError("Content-Length is required", HTTPStatus.LENGTH_REQUIRED)
        try:
            length = int(raw_length)
        except ValueError as error:
            raise SandboxError("invalid Content-Length") from error
        if length <= 0:
            raise SandboxError("empty request body")
        if length > MAX_BODY_BYTES:
            raise SandboxError("request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(length)
        if len(body) != length:
            raise SandboxError("incomplete request body")
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SandboxError("body must be a UTF-8 JSON object") from error
        if not isinstance(document, Mapping):
            raise SandboxError("body must be a JSON object")
        return document

    # -- HTTP ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        try:
            if not self._host_ok():
                self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
                return
            path = urlsplit(self.path).path
            if path in STATIC_FILES:
                session = self._session()
                entry = self.sandbox._static_cache.get(path)
                if entry is None:
                    self._error(HTTPStatus.NOT_FOUND, "not found")
                    return
                body, content_type = entry
                self._send(HTTPStatus.OK, body, content_type)
                del session
                return
            if path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, None)
                return
            if path == "/api/state":
                self._session()
                self._api_state()
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except SandboxError as error:
            self._error(error.status, error.message)
        except Exception:
            LOGGER.exception("unhandled GET failure")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal sandbox error")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        try:
            if not self._host_ok():
                self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
                return
            if not self._origin_ok():
                self._error(HTTPStatus.FORBIDDEN, "cross-origin requests are not accepted")
                return
            if not self._token_ok():
                self._error(HTTPStatus.FORBIDDEN, "missing or invalid sandbox token")
                return
            session = self._session()
            path = urlsplit(self.path).path
            payload = self._read_json()
            with self.sandbox.lock:
                if path == "/api/open":
                    result = self.sandbox.open_menu(session, payload)
                elif path == "/api/event":
                    result = self.sandbox.handle_event(session, payload)
                elif path == "/api/scenario":
                    result = self.sandbox.set_scenario(session, payload)
                elif path == "/api/profile":
                    result = self.sandbox.set_profile(session, payload)
                else:
                    raise SandboxError("not found", HTTPStatus.NOT_FOUND)
            self._json(HTTPStatus.OK, result)
        except SandboxError as error:
            self._error(error.status, error.message)
        except Exception:
            LOGGER.exception("unhandled POST failure")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal sandbox error")

    def _api_state(self) -> None:
        session = self._session()
        with self.sandbox.lock:
            payload = self.sandbox.state_payload(session, session.controller.view())
        self._json(HTTPStatus.OK, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m max2025_menu_prototype.sandbox",
        description="Run the loopback-only simulated context-menu sandbox.",
    )
    parser.add_argument("--port", type=int, default=0, help="port to bind on 127.0.0.1 (default: choose one)")
    parser.add_argument("--verbose", action="store_true", help="log HTTP requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    server = SandboxServer(port=args.port)
    server.start()
    print("Context Menu Lab — simulated sandbox")
    print(f"Open: {server.url}")
    print("Loopback only. No browser window is opened automatically. Ctrl+C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping sandbox.")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
