"""Tests for kaskad: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kaskad import Assistant, actions, check, llm  # noqa: E402
from kaskad.net import post_json  # noqa: E402
from kaskad.server import make_server  # noqa: E402
from kaskad.sessions import SessionStore  # noqa: E402

MODULES = os.path.join(ROOT, "modules")


def folder(test, **files):
    """A temporary modules folder holding the given sources (module name -> code)."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    for name, source in files.items():
        with open(os.path.join(tmp.name, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(source))
    return tmp.name


class FakeAPI:
    """A local HTTP server that records POST requests and answers with canned JSON per path."""

    def __init__(self, test, responses):
        self.requests = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                api.requests.append({"path": self.path, "body": json.loads(raw or b"null"),
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                payload = json.dumps(responses.get(self.path, {})).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RuntimeTests(unittest.TestCase):
    def test_priority_order_and_load_errors(self):
        path = folder(self, low="PRIORITY = 1\n", high="PRIORITY = 5\n", broken="raise RuntimeError('bad file')\n",
                      _helper="X = 1\n")
        assistant = Assistant(path)
        self.assertEqual(assistant.modules, ["high", "low"])
        self.assertEqual([name for name, _ in assistant.errors], ["broken"])

    def test_failing_module_is_skipped(self):
        path = folder(self, boom="def respond(text, memory):\n    raise RuntimeError('oops')\n",
                      ok="def respond(text, memory):\n    return 'ok'\n")
        result = Assistant(path).ask("hi")
        self.assertEqual(result["reply"], "ok")
        self.assertIn("RuntimeError: oops", result["skipped"][0]["reason"])

    def test_slow_module_times_out_and_is_not_restarted_while_busy(self):
        path = folder(self, slow="""
            import time
            PRIORITY = 10
            def respond(text, memory):
                time.sleep(1.0)
                return 'slow'
            """, quick="def respond(text, memory):\n    return 'quick'\n")
        assistant = Assistant(path, turn_timeout=0.2)
        start = time.perf_counter()
        first = assistant.ask("hi")
        self.assertLess(time.perf_counter() - start, 0.8)
        self.assertEqual(first["reply"], "quick")
        self.assertEqual(first["skipped"], [{"module": "slow", "phase": "respond", "reason": "timeout"}])
        second = assistant.ask("again")
        self.assertEqual(second["skipped"][0]["reason"], "still busy")

    def test_sequential_mode_without_threads(self):
        path = folder(self, ok="def respond(text, memory):\n    return 'ok'\n")
        self.assertEqual(Assistant(path, turn_timeout=None).ask("hi")["reply"], "ok")

    def test_memory_of_a_module_not_chosen_leaves_no_trace(self):
        path = folder(self, writer="""
            PRIORITY = 1
            def respond(text, memory):
                memory['x'] = 1
                return 'low'
            """, winner="PRIORITY = 9\ndef respond(text, memory):\n    return 'high'\n")
        store = SessionStore()
        result = Assistant(path, store=store).ask("hi", "s")
        self.assertEqual(result["reply"], "high")
        self.assertNotIn("x", store.load("s"))

    def test_safety_band_wins_and_is_never_handed_on(self):
        path = folder(self, safety="PRIORITY = 9000\ndef respond(text, memory):\n"
                                   "    return {'reply': 'help', 'confidence': 0.1}\n",
                      normal="PRIORITY = 100\ndef respond(text, memory):\n    return 'normal'\n")
        result = Assistant(path, escalate=lambda text, history: "llm").ask("hi")
        self.assertEqual((result["reply"], result["unknown"], result["escalated"]), ("help", False, False))

    def test_a_repeat_gives_way_but_the_same_question_still_gets_its_answer(self):
        path = folder(self, a="PRIORITY = 10\ndef respond(text, memory):\n    return 'A'\n",
                      b="PRIORITY = 5\ndef respond(text, memory):\n    return 'B'\n")
        assistant = Assistant(path)
        self.assertEqual([assistant.ask(t)["reply"] for t in ("x", "y", "z")], ["A", "B", "A"])
        alone = Assistant(folder(self, a="def respond(text, memory):\n    return 'A'\n"))
        self.assertEqual([alone.ask(t)["reply"] for t in ("x", "x")], ["A", "A"])


class HostTests(unittest.TestCase):
    def floor(self):
        return folder(self, floor="PRIORITY = -1000\ndef respond(text, memory):\n"
                                  "    return {'reply': 'no idea', 'unknown': True}\n")

    def test_unknown_goes_to_the_language_model_with_history(self):
        seen = []

        def escalate(text, history):
            seen.append((text, history))
            return "from llm"

        assistant = Assistant(self.floor(), escalate=escalate)
        assistant.ask("first", "s")
        result = assistant.ask("second", "s")
        self.assertEqual((result["reply"], result["module"], result["level"]), ("from llm", "llm", 1))
        self.assertTrue(result["unknown"] and result["escalated"])
        self.assertEqual(seen[1][1], [{"user": "first", "bot": "from llm", "module": "llm"}])

    def test_a_failing_language_model_leaves_the_rules_reply(self):
        def escalate(text, history):
            raise OSError("offline")

        result = Assistant(self.floor(), escalate=escalate).ask("hi")
        self.assertEqual((result["reply"], result["escalated"]), ("no idea", False))
        self.assertIn("escalation failed: OSError: offline", result["warnings"])

    def test_low_confidence_counts_as_unknown(self):
        path = folder(self, maybe="def respond(text, memory):\n    return {'reply': 'maybe', 'confidence': 0.3}\n")
        self.assertTrue(Assistant(path).ask("hi")["unknown"])
        self.assertFalse(Assistant(path, unknown_threshold=0.2).ask("hi")["unknown"])

    def test_nobody_answers(self):
        result = Assistant(folder(self, mute="def respond(text, memory):\n    return None\n")).ask("Как дела?")
        self.assertEqual((result["reply"], result["module"], result["unknown"]), ("Не знаю.", None, True))

    def test_actions_are_carried_out(self):
        path = folder(self, lamp="def respond(text, memory):\n"
                                 "    return {'reply': 'on', 'action': {'service': 'light.turn_on'}}\n")
        done = []
        result = Assistant(path, on_action=done.append).ask("light")
        self.assertTrue(result["acted"])
        self.assertEqual(done, [{"service": "light.turn_on"}])

        def broken(action):
            raise OSError("no route")

        result = Assistant(path, on_action=broken).ask("light")
        self.assertFalse(result["acted"])
        self.assertIn("action failed: OSError: no route", result["warnings"])

    def test_sessions_survive_a_restart(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "sessions.sqlite")
        first = SessionStore(path)
        Assistant(MODULES, store=first).ask("my name is Anna", "call-1")
        first.close()
        second = SessionStore(path)
        self.addCleanup(second.close)
        self.assertEqual(Assistant(MODULES, store=second).ask("what's my name?", "call-1")["reply"], "Your name is Anna.")

    def test_memory_that_is_not_json_is_reported(self):
        path = folder(self, setter="def respond(text, memory):\n    memory['tags'] = {1, 2}\n    return 'ok'\n")
        result = Assistant(path).ask("hi")
        self.assertTrue(any(w.startswith("memory not saved") for w in result["warnings"]))


class ExampleTests(unittest.TestCase):
    def test_control_questions_pass(self):
        with open(os.path.join(ROOT, "checks.json"), encoding="utf-8") as fh:
            checks = json.load(fh)["checks"]
        outcome = check.run(Assistant(MODULES), checks)
        self.assertEqual([(ask, problems) for ask, problems, _ in outcome if problems], [])

    def test_snapshot_comparison_finds_a_changed_reply(self):
        outcome = check.run(Assistant(MODULES), [{"ask": "включи свет на кухне"}, {"ask": "выключи свет"}])
        old = check.snapshot(outcome)
        new = [dict(item) for item in old]
        new[1]["reply"] = "changed"
        self.assertEqual(len(check.compare(old, new)), 1)
        self.assertEqual(check.compare(old, old), [])


class AdapterTests(unittest.TestCase):
    def test_redact_masks_contact_data_but_keeps_dates_amounts_and_norms(self):
        text = ("Mail max@example.org, Tel. +49 351 1234567 oder 0176-12345678 oder +49 (0)351 7654321, "
                "IBAN DE89 3704 0044 0532 0130 00, Bescheid vom 20.11.2026, § 29 SGB IX, 1.234,56 EUR, "
                "Az. S 6 SO 58/26 ER, 01069 Dresden")
        clean = llm.redact(text)
        for secret in ("max@example.org", "1234567", "12345678", "7654321", "DE89"):
            self.assertNotIn(secret, clean)
        self.assertEqual(clean.count("[phone]"), 3)
        for kept in ("20.11.2026", "§ 29 SGB IX", "1.234,56", "S 6 SO 58/26 ER", "01069 Dresden"):
            self.assertIn(kept, clean)

    def test_anthropic_request(self):
        history = [{"user": "mail me at a@b.de", "bot": "ok"}]
        url, headers, body = llm.anthropic_request("и что дальше?", history, "claude-haiku-5-5", "key", "https://x/")
        self.assertEqual(url, "https://x/v1/messages")
        self.assertEqual((headers["x-api-key"], headers["anthropic-version"]), ("key", "2023-06-01"))
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "assistant", "user"])
        self.assertEqual(body["messages"][0]["content"], "mail me at [email]")

    def test_openai_request(self):
        url, headers, body = llm.openai_request("hi", [], "local", "", "http://127.0.0.1:8080/v1")
        self.assertEqual(url, "http://127.0.0.1:8080/v1/chat/completions")
        self.assertNotIn("authorization", headers)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_both_adapters_against_a_local_fake_server(self):
        api = FakeAPI(self, {"/v1/messages": {"content": [{"type": "text", "text": "claude ok"}]},
                             "/v1/chat/completions": {"choices": [{"message": {"content": "local ok"}}]}})
        self.assertEqual(llm.anthropic(base_url=api.url, api_key="k")("Tel 0351 1234567?", []), "claude ok")
        self.assertEqual(llm.openai_compatible(base_url=api.url + "/v1", model="m")("hi", []), "local ok")
        self.assertEqual(api.requests[0]["body"]["messages"][-1]["content"], "Tel [phone]?")
        self.assertEqual(api.requests[0]["headers"]["x-api-key"], "k")

    def test_home_assistant_call(self):
        url, headers, body = actions.home_assistant_request(
            {"service": "light.turn_on", "area": "kitchen"}, "http://ha:8123/", "token")
        self.assertEqual((url, body), ("http://ha:8123/api/services/light/turn_on", {"area_id": "kitchen"}))
        self.assertEqual(headers["authorization"], "Bearer token")
        self.assertEqual(actions.home_assistant_request({"service": "light.turn_off"}, "http://ha", "")[2],
                         {"entity_id": "all"})
        api = FakeAPI(self, {})
        actions.home_assistant(api.url, "t")({"service": "light.turn_off", "area": "bedroom"})
        self.assertEqual((api.requests[0]["path"], api.requests[0]["body"]),
                         ("/api/services/light/turn_off", {"area_id": "bedroom"}))
        with self.assertRaises(ValueError):
            actions.home_assistant_request({"service": "nonsense"}, "http://ha", "")


class ServerTests(unittest.TestCase):
    def test_http_endpoint(self):
        server = make_server(Assistant(MODULES), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        result = post_json(base + "/ask", {"content-type": "application/json"},
                           {"text": "включи свет на кухне", "session": "t"}, 5)
        self.assertEqual(result["action"], {"service": "light.turn_on", "area": "kitchen"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + "/health", timeout=5) as response:
            self.assertIn("base", json.loads(response.read())["modules"])
        for data, status in ((b"not json", 400), (b"x" * (65 * 1024), 413), (b'{"text": ""}', 400)):
            request = urllib.request.Request(base + "/ask", data=data, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(request, timeout=5)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()


if __name__ == "__main__":
    unittest.main()
