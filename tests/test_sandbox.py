"""Unit checks for the loopback sandbox (stdlib only, localhost only)."""

from __future__ import annotations

import ast
import http.client
import http.cookiejar
import json
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from max2025_menu_prototype.core.profile import default_profile, dump_profile
from max2025_menu_prototype.sandbox.server import MAX_BODY_BYTES, SandboxServer

try:
    from . import checkout_support
except ImportError:  # discovered as a top-level test module
    import checkout_support

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"


class Client:
    """Cookie-keeping test client for one sandbox server."""

    def __init__(self, server: SandboxServer) -> None:
        self.server = server
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.token = server.token

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        url = self.server.url.rstrip("/") + path
        request = urllib.request.Request(url, data=body, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            try:
                body = error.read()
            finally:
                error.close()
            return error.code, dict(error.headers), body

    def get(self, path: str, headers: dict[str, str] | None = None):
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        payload,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ):
        if raw:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json"}
        token_value = self.token if token is None else token
        if token_value:
            merged["X-Sandbox-Token"] = token_value
        merged.update(headers or {})
        status, response_headers, raw_body = self.request("POST", path, body, merged)
        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            data = {"raw": raw_body.decode("utf-8", "replace")}
        return status, response_headers, data

    def state(self):
        status, headers, body = self.get("/api/state")
        return status, headers, json.loads(body.decode("utf-8"))


class SandboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = SandboxServer(port=0)
        self.server.start_background()
        self.addCleanup(self.server.stop)
        self.client = Client(self.server)
        status, _, _ = self.client.get("/")
        self.assertEqual(status, 200)

    # -- helpers -------------------------------------------------------

    def open_view(self, invocation: str = "latched", scenario: str | None = None):
        if scenario is not None:
            status, _, data = self.client.post("/api/scenario", {"name": scenario})
            self.assertEqual(status, 200, data)
        status, _, data = self.client.post(
            "/api/open", {"invocation": invocation, "time_ms": 1000}
        )
        self.assertEqual(status, 200, data)
        return data["view"]

    def send(self, view, kind: str, **fields):
        event = {
            "session_id": view["session_id"],
            "sequence": int(view["sequence"]) + 1,
            "kind": kind,
            "time_ms": fields.pop("time_ms", 1500),
            "x": fields.pop("x", 0),
            "y": fields.pop("y", 0),
            "action_id": fields.pop("action_id", None),
            "text": fields.pop("text", ""),
            "key": fields.pop("key", ""),
            "auto_repeat": fields.pop("auto_repeat", False),
        }
        event.update(fields)
        status, _, data = self.client.post("/api/event", {"event": event})
        return status, data

    def raw_get(self, path: str, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        try:
            connection.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host or f"127.0.0.1:{self.server.port}")
            connection.endheaders()
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.headers), body
        finally:
            connection.close()


class StaticAndSecurityTests(SandboxTestCase):
    def test_ephemeral_loopback_server_and_assets(self) -> None:
        self.assertEqual(self.server._httpd.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.server.port, 0)

        status, headers, body = self.client.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"SIMULATED", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        token = re.search(rb'name="sandbox-token" content="([^"]+)"', body)
        self.assertIsNotNone(token)
        self.assertEqual(token.group(1).decode(), self.server.token)

        status, headers, script = self.client.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b"renderMenu", script)
        self.assertIn(b"SandboxClient", script)

        status, headers, style = self.client.get("/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])

        status, headers, icon = self.client.get("/favicon.svg")
        self.assertEqual(status, 200)
        self.assertIn("image/svg", headers["Content-Type"])
        self.assertIn(b"<svg", icon)

    def test_static_allowlist_rejects_traversal_and_unknown_paths(self) -> None:
        for path in (
            "/../core/controller.py",
            "/static/../server.py",
            "/core/controller.py",
            "/api/../api/state",
            "/%2e%2e/core/controller.py",
            "/sandbox/server.py",
        ):
            status, _, _ = self.raw_get(path)
            self.assertEqual(status, 404, path)

    def test_token_and_origin_validation(self) -> None:
        status, _, _ = self.client.post("/api/open", {"invocation": "latched", "time_ms": 1}, token="")
        self.assertEqual(status, 403)
        status, _, _ = self.client.post("/api/open", {"invocation": "latched", "time_ms": 1}, token="wrong")
        self.assertEqual(status, 403)

        good_origin = f"http://127.0.0.1:{self.server.port}"
        status, _, _ = self.client.post(
            "/api/open",
            {"invocation": "latched", "time_ms": 1},
            headers={"Origin": good_origin},
        )
        self.assertEqual(status, 200)
        self.client.post("/api/event", {"event": {}})  # closed menu, harmless validation error

        status, _, data = self.client.post(
            "/api/open",
            {"invocation": "latched", "time_ms": 1},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn("cross-origin", data["error"])

        status, _, data = self.client.post(
            "/api/open",
            {"invocation": "latched", "time_ms": 1},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)

    def test_host_header_is_restricted_to_loopback(self) -> None:
        status, _, _ = self.raw_get("/", host="evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.raw_get("/", host=f"localhost:{self.server.port}")
        self.assertEqual(status, 200)

    def test_request_body_limits_and_content_type(self) -> None:
        status, _, data = self.client.post(
            "/api/profile",
            {"action": "import", "text": "x" * (MAX_BODY_BYTES + 1)},
        )
        self.assertEqual(status, 413)
        self.assertIn("too large", data["error"])

        status, _, data = self.client.post("/api/profile", "{not json", raw=True)
        self.assertEqual(status, 400)

        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        try:
            connection.putrequest("POST", "/api/open")
            connection.putheader("Host", f"127.0.0.1:{self.server.port}")
            connection.putheader("X-Sandbox-Token", self.server.token)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()

        status, _, _ = self.client.post(
            "/api/open",
            {"invocation": "latched", "time_ms": 1},
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 415)


class EventFlowTests(SandboxTestCase):
    def test_full_open_hover_release_flow(self) -> None:
        view = self.open_view("held", scenario="editable_poly_vertex")
        self.assertEqual(view["phase"], "held")
        self.assertEqual(view["context"], "vertex")
        self.assertIsNone(view["armed_id"])

        status, moved = self.send(view, "pointer_move", x=100, y=100, action_id="transform.move")
        self.assertEqual(status, 200, moved)
        self.assertEqual(moved["view"]["armed_id"], "transform.move")

        status, released = self.send(
            moved["view"], "trigger_up", x=100, y=100, time_ms=1400
        )
        self.assertEqual(status, 200, released)
        final = released["view"]
        self.assertEqual(final["phase"], "closed")
        self.assertEqual(final["last_result"]["status"], "simulated")
        self.assertEqual(final["last_result"]["action_id"], "transform.move")
        self.assertTrue(any(entry["action"] == "transform.move" for entry in final["log"]))
        self.assertTrue(self.server.host.launch_log)
        self.assertTrue(
            self.server.host.launch_log[-1]["detail"].startswith("SIMULATED")
        )

    def test_neutral_release_cancels_and_tap_latches(self) -> None:
        view = self.open_view("held", scenario="editable_poly_vertex")
        status, moved = self.send(view, "pointer_move", x=100, y=100, action_id="transform.move")
        self.assertEqual(status, 200)
        status, released = self.send(moved["view"], "trigger_up", x=2, y=2, time_ms=1500)
        self.assertEqual(status, 200)
        self.assertEqual(released["view"]["phase"], "closed")
        self.assertEqual(self.server.host.launch_log, [])

        view = self.open_view("held", scenario="editable_poly_vertex")
        status, tapped = self.send(view, "trigger_up", x=100, y=100, time_ms=1100)
        self.assertEqual(status, 200)
        self.assertEqual(tapped["view"]["phase"], "latched")
        self.assertIsNone(tapped["view"]["armed_id"])
        self.assertIsNone(tapped["view"]["highlighted_id"])
        self.assertIn("latched", tapped["view"]["status"])
        self.assertEqual(self.server.host.launch_log, [])

        # A latched tap still commits only through an explicit click.
        status, clicked = self.send(tapped["view"], "click_row", action_id="transform.move")
        self.assertEqual(status, 200)
        self.assertEqual(clicked["view"]["phase"], "closed")
        self.assertEqual(clicked["view"]["last_result"]["status"], "simulated")
        self.assertEqual(self.server.host.launch_log[-1]["action_id"], "transform.move")

    def test_launch_place_does_not_arm(self) -> None:
        view = self.open_view("held", scenario="editable_poly_vertex")
        self.assertIsNone(view["armed_id"])
        self.assertIsNone(view["highlighted_id"])

    def test_sequence_session_and_event_validation(self) -> None:
        view = self.open_view("latched", scenario="editable_poly_vertex")
        event = {
            "session_id": view["session_id"],
            "sequence": int(view["sequence"]) + 1,
            "kind": "pointer_move",
            "time_ms": 10,
            "x": 100,
            "y": 100,
            "action_id": "transform.move",
        }
        status, _, first = self.client.post("/api/event", {"event": dict(event)})
        self.assertEqual(status, 200)
        status, _, duplicate = self.client.post("/api/event", {"event": dict(event)})
        self.assertEqual(status, 409)
        self.assertIn("duplicate", duplicate["error"])

        status, _, stale = self.client.post(
            "/api/event", {"event": {**event, "session_id": "not-the-session", "sequence": 999}}
        )
        self.assertEqual(status, 409)

        for kind in ("launch", "trigger", "", "trigger_downX"):
            status, _, data = self.client.post(
                "/api/event",
                {"event": {**event, "kind": kind, "sequence": 999}},
            )
            self.assertEqual(status, 400, kind)

        status, _, data = self.client.post(
            "/api/event",
            {"event": {**event, "sequence": 0}},
        )
        self.assertEqual(status, 400)
        for bad_sequence in (1.5, True, "2", None):
            status, _, data = self.client.post(
                "/api/event",
                {"event": {**event, "sequence": bad_sequence}},
            )
            self.assertEqual(status, 400, bad_sequence)
        status, _, data = self.client.post(
            "/api/event",
            {"event": {**event, "sequence": 5, "auto_repeat": "yes"}},
        )
        self.assertEqual(status, 400)
        status, _, data = self.client.post(
            "/api/event",
            {"event": {**event, "sequence": 5, "x": 10_000_000}},
        )
        self.assertEqual(status, 400)
        status, _, data = self.client.post(
            "/api/event",
            {"event": {**event, "sequence": 6, "text": "x" * 200}},
        )
        self.assertEqual(status, 400)

        self.send(first["view"], "cancel", key="Escape")
        status, _, data = self.client.post(
            "/api/event", {"event": {**event, "sequence": 7}}
        )
        self.assertEqual(status, 409)
        self.assertIn("closed", data["error"])

    def test_scenario_updates_and_context_catalog(self) -> None:
        status, _, data = self.client.state()
        self.assertEqual(status, 200)
        names = [item["name"] for item in data["scenarios"]]
        self.assertGreaterEqual(len(names), 15)
        self.assertIn("editable_poly_border", names)
        self.assertEqual(set(data["context_scenarios"]), {
            "object", "vertex", "edge", "border", "polygon", "element"
        })
        self.assertEqual(data["context_scenarios"]["polygon"], "editable_poly_polygon")

        status, _, data = self.client.post("/api/scenario", {"name": "primitive"})
        self.assertEqual(status, 200)
        self.assertEqual(data["scenario"], "primitive")
        self.assertEqual(data["snapshot"]["target_kind"], "other")

        status, _, data = self.client.post("/api/scenario", {"name": "not_a_scenario"})
        self.assertEqual(status, 404)
        status, _, data = self.client.post("/api/scenario", {"name": ""})
        self.assertEqual(status, 400)

    def test_stale_context_while_open_blocks_launch(self) -> None:
        view = self.open_view("latched", scenario="editable_poly_vertex")
        self.assertEqual(view["phase"], "latched")
        launches_before = len(self.server.host.launch_log)

        status, _, data = self.client.post("/api/scenario", {"name": "changed_target"})
        self.assertEqual(status, 200)
        self.assertEqual(data["view"]["phase"], "latched")

        status, data = self.send(data["view"], "click_row", action_id="transform.move")
        self.assertEqual(status, 200, data)
        final = data["view"]
        self.assertEqual(final["phase"], "closed")
        self.assertEqual(final["last_result"]["status"], "blocked")
        self.assertEqual(len(self.server.host.launch_log), launches_before)

    def test_click_disabled_row_explains_and_keeps_menu(self) -> None:
        view = self.open_view("latched", scenario="no_selection")
        disabled = next(row for row in view["rows"] if not row["enabled"])
        status, data = self.send(view, "click_row", action_id=disabled["action_id"])
        self.assertEqual(status, 200)
        self.assertEqual(data["view"]["phase"], "latched")
        self.assertEqual(data["view"]["last_result"], None)
        self.assertIn("Select", data["view"]["status"] + disabled["reason"])
        self.assertEqual(self.server.host.launch_log, [])


class ProfileApiTests(SandboxTestCase):
    def test_profile_round_trip_reorder_hide_reset(self) -> None:
        status, _, state = self.client.state()
        profile = state["profile"]
        object_entries = profile["contexts"]["object"]
        object_entries[0], object_entries[1] = object_entries[1], object_entries[0]
        hidden_id = object_entries[2]["id"]
        object_entries[2]["visible"] = False
        profile["tap_ms"] = 300
        profile["neutral_px"] = 20

        status, _, data = self.client.post("/api/profile", {"action": "set", "profile": profile})
        self.assertEqual(status, 200, data)
        applied = data["profile"]
        self.assertEqual(applied["tap_ms"], 300)
        self.assertEqual(applied["neutral_px"], 20)
        self.assertEqual(applied["contexts"]["object"][0]["id"], object_entries[0]["id"])
        self.assertFalse(
            next(item for item in applied["contexts"]["object"] if item["id"] == hidden_id)["visible"]
        )

        status, _, data = self.client.post("/api/profile", {"action": "reset"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["profile"], dump_profile(default_profile()))

    def test_malformed_profile_import_leaves_active_profile_intact(self) -> None:
        status, _, state = self.client.state()
        active_before = state["profile"]

        cases = [
            "{not json",
            json.dumps({"schema_version": 2}),
        ]
        for text in cases:
            status, _, data = self.client.post("/api/profile", {"action": "import", "text": text})
            self.assertEqual(status, 400, text)
            self.assertIn("profile rejected", data["error"])

        invalid = json.loads(json.dumps(active_before))
        invalid["contexts"]["object"][0]["id"] = "not.a.real.action"
        status, _, data = self.client.post(
            "/api/profile", {"action": "import", "text": json.dumps(invalid)}
        )
        self.assertEqual(status, 400)

        invalid = json.loads(json.dumps(active_before))
        invalid["tap_ms"] = 99
        status, _, data = self.client.post(
            "/api/profile", {"action": "import", "text": json.dumps(invalid)}
        )
        self.assertEqual(status, 400)

        invalid = json.loads(json.dumps(active_before))
        invalid["contexts"]["object"][0]["id"] = "model.cap"
        status, _, data = self.client.post(
            "/api/profile", {"action": "import", "text": json.dumps(invalid)}
        )
        self.assertEqual(status, 400)

        status, _, state = self.client.state()
        self.assertEqual(state["profile"], active_before)

    def test_profile_replacement_while_open_cancels(self) -> None:
        view = self.open_view("latched", scenario="editable_poly_vertex")
        self.assertEqual(view["phase"], "latched")
        status, _, data = self.client.post("/api/profile", {"action": "reset"})
        self.assertEqual(status, 200)
        self.assertEqual(data["view"]["phase"], "closed")
        self.assertIn("Profile replaced", data["view"]["status"])

    def test_unknown_profile_actions_are_rejected(self) -> None:
        status, _, data = self.client.post("/api/profile", {"action": "execute"})
        self.assertEqual(status, 400)
        self.assertIn("unknown profile action", data["error"])

        status, _, state = self.client.state()
        extra = json.loads(json.dumps(state["profile"]))
        extra["command"] = "print(1)"
        status, _, data = self.client.post("/api/profile", {"action": "set", "profile": extra})
        self.assertEqual(status, 400)


class BoundaryTests(unittest.TestCase):
    def test_sandbox_never_imports_max_or_reference_modules(self) -> None:
        code = (
            "import sys;"
            "import max2025_menu_prototype.sandbox.server;"
            "assert 'pymxs' not in sys.modules;"
            "assert 'qtmax' not in sys.modules;"
            "assert not any(m.startswith('max2025_menu_prototype.max_host') for m in sys.modules);"
            "print('clean')"
        )
        result = checkout_support.run_python(code, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_source_has_no_reference_or_host_imports(self) -> None:
        forbidden = ("pymxs", "qtmax", "modeler", "max_host")
        for path in SANDBOX.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertFalse(
                        any(part in name for part in forbidden),
                        f"{path} imports {name!r}",
                    )

    def test_qt_package_import_is_lazy(self) -> None:
        code = (
            "import sys;"
            "import max2025_menu_prototype.qt_ui as q;"
            "assert 'PySide6' not in sys.modules;"
            "assert q.__all__ == ['MenuPopup'];"
            "print('lazy')"
        )
        result = checkout_support.run_python(code, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lazy", result.stdout)

    def test_javascript_has_no_state_machine_duplicate_markers(self) -> None:
        source = (SANDBOX / "static" / "app.js").read_text(encoding="utf-8")
        # The browser sends normalized events; it must not own phase decisions.
        self.assertIn("postEventDirect", source)
        self.assertIn("pointer_move", source)
        self.assertNotIn("function commitAction", source)
        self.assertNotIn("launch(action_id", source)


if __name__ == "__main__":
    unittest.main()
