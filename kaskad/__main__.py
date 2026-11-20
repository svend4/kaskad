"""Command line for kaskad.

    python -m kaskad                       chat in the terminal (/reset, /exit)
    python -m kaskad -p "вопрос" -v        one reply with details, then exit
    echo "вопрос" | python -m kaskad       one reply per input line
    python -m kaskad --serve 8765          JSON endpoint on 127.0.0.1:8765
    python -m kaskad.check checks.json     control questions
"""
import argparse
import json
import os
import sys

from . import actions, llm
from .host import Assistant
from .sessions import SessionStore


def build(args):
    escalate = {"anthropic": llm.anthropic, "openai": llm.openai_compatible}.get(args.llm)
    return Assistant(
        args.modules or [os.path.join(os.getcwd(), "modules")],
        escalate=escalate() if escalate else None,
        on_action=actions.home_assistant() if args.home_assistant else None,
        store=SessionStore(args.sessions) if args.sessions else None,
        unknown_threshold=args.threshold,
    )


def details(result):
    parts = ["module: %s" % result["module"], "level: %s" % result["level"]]
    if result["confidence"] is not None:
        parts.append("confidence: %.2f" % result["confidence"])
    if result["action"]:
        parts.append("action: %s" % json.dumps(result["action"], ensure_ascii=False))
    if result["unknown"]:
        parts.append("unknown")
    if result["escalated"]:
        parts.append("escalated")
    parts.append("%s ms" % result["ms"])
    lines = ["[" + " | ".join(parts) + "]"]
    lines += ["[skipped %s: %s]" % (item["module"], item["reason"]) for item in result["skipped"]]
    lines += ["[warning: %s]" % warning for warning in result["warnings"]]
    return "\n".join(lines)


def answer(assistant, text, session, verbose):
    result = assistant.ask(text, session)
    print(result["reply"], flush=True)
    if verbose:
        print(details(result), file=sys.stderr, flush=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # a console without Cyrillic must not crash the bot
    parser = argparse.ArgumentParser(prog="kaskad",
                                     description="Rule-first assistant with a language model as the last level.")
    parser.add_argument("-p", "--prompt", help="answer one message and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="show module, level, action and timing")
    parser.add_argument("--modules", action="append", metavar="DIR",
                        help="folder with modules (repeatable; default ./modules)")
    parser.add_argument("--sessions", metavar="FILE", help="SQLite file for session memory (default: in memory)")
    parser.add_argument("--session", default="cli", help="session id (default: cli)")
    parser.add_argument("--llm", choices=["none", "anthropic", "openai"], default="none",
                        help="language model for questions the rules do not know")
    parser.add_argument("--home-assistant", action="store_true",
                        help="send actions to Home Assistant (KASKAD_HA_URL, KASKAD_HA_TOKEN)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="below this confidence an answer counts as unknown (default 0.5)")
    parser.add_argument("--serve", type=int, metavar="PORT", help="serve POST /ask on this port")
    parser.add_argument("--host", default="127.0.0.1", help="address for --serve (default 127.0.0.1)")
    args = parser.parse_args(argv)

    assistant = build(args)
    for name, error in assistant.errors:
        print("module %s not loaded: %s" % (name, error), file=sys.stderr)
    if not assistant.modules:
        print("no modules found; pass --modules DIR", file=sys.stderr)
        return 2

    if args.serve is not None:
        from .server import make_server
        server = make_server(assistant, args.host, args.serve, quiet=not args.verbose)
        print("kaskad on http://%s:%d (POST /ask, GET /health)" % (args.host, server.server_address[1]),
              file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    if args.prompt is not None:
        answer(assistant, args.prompt, args.session, args.verbose)
        return 0

    if not sys.stdin.isatty():
        for line in sys.stdin:
            if line.strip():
                answer(assistant, line.rstrip("\r\n"), args.session, args.verbose)
        return 0

    print("kaskad: %s. /reset clears the conversation, /exit quits." % ", ".join(assistant.modules))
    while True:
        try:
            text = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        command = text.strip().lower()
        if command in ("/exit", "/quit"):
            return 0
        if command == "/reset":
            assistant.store.forget(args.session)
            print("(conversation cleared)")
        elif command:
            answer(assistant, text, args.session, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
