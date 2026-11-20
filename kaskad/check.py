"""Control questions: run them after every change to see that nothing broke.

    python -m kaskad.check checks.json [--modules DIR] [--record FILE | --compare FILE]

checks.json holds {"checks": [...]}. A check is one question with expectations, or
{"steps": [...]} for a conversation whose steps share one session. Expectations, all optional:

    "module": "base"          which module must answer
    "contains": "§ 29"        text the reply must contain (or a list of such texts)
    "action": {...} or null   the action the reply must carry
    "unknown": true           whether the assistant must admit it does not know
    "level": 4                which level must answer

The language model is off while checking, so "unknown" stays visible. --record saves every reply
to a snapshot file; --compare lists every reply that differs from a saved snapshot.
Exit code 1 when a check fails or the snapshot differs.
"""
import argparse
import json
import os
import sys

from .host import Assistant


def _dump(value):
    return json.dumps(value, ensure_ascii=False)


def _problems(step, result):
    problems = []
    if "module" in step and result["module"] != step["module"]:
        problems.append("module %r, expected %r" % (result["module"], step["module"]))
    pieces = step.get("contains") or []
    for piece in [pieces] if isinstance(pieces, str) else pieces:
        if piece.casefold() not in result["reply"].casefold():
            problems.append("reply lacks %r" % piece)
    if "action" in step and result["action"] != step["action"]:
        problems.append("action %s, expected %s" % (_dump(result["action"]), _dump(step["action"])))
    if "unknown" in step and result["unknown"] != step["unknown"]:
        problems.append("unknown is %s, expected %s" % (result["unknown"], step["unknown"]))
    if "level" in step and result["level"] != step["level"]:
        problems.append("level %r, expected %r" % (result["level"], step["level"]))
    return problems


def run(assistant, checks):
    """[(question, problems, result)] for every step, in order."""
    outcome = []
    for number, check in enumerate(checks, 1):
        session = "check-%d" % number
        assistant.store.forget(session)
        for step in check.get("steps", [check]):
            result = assistant.ask(step["ask"], session)
            outcome.append((step["ask"], _problems(step, result), result))
    return outcome


def snapshot(outcome):
    return [{"ask": ask, "reply": r["reply"], "module": r["module"], "action": r["action"], "unknown": r["unknown"]}
            for ask, _, r in outcome]


def compare(old, new):
    """[(old, new)] for every position where two snapshots differ."""
    pairs = [(old[i] if i < len(old) else None, new[i] if i < len(new) else None)
             for i in range(max(len(old), len(new)))]
    return [(a, b) for a, b in pairs if a != b]


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="kaskad.check", description="Run control questions.")
    parser.add_argument("checks", help='JSON file with {"checks": [...]}')
    parser.add_argument("--modules", action="append", metavar="DIR", help="folder with modules (default ./modules)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--record", metavar="FILE", help="save every reply to this snapshot file")
    group.add_argument("--compare", metavar="FILE", help="compare every reply with this snapshot file")
    args = parser.parse_args(argv)

    with open(args.checks, encoding="utf-8") as fh:
        checks = json.load(fh)["checks"]
    assistant = Assistant(args.modules or [os.path.join(os.getcwd(), "modules")])
    for name, error in assistant.errors:
        print("module %s not loaded: %s" % (name, error))
    outcome = run(assistant, checks)
    failed = 0
    for ask, problems, result in outcome:
        print("%s  %s  [%s]" % ("FAIL" if problems else "PASS", ask, result["module"]))
        for problem in problems:
            print("        " + problem)
        failed += bool(problems)
    print("%d of %d passed" % (len(outcome) - failed, len(outcome)))

    current = snapshot(outcome)
    if args.record:
        with open(args.record, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2)
        print("snapshot saved to %s" % args.record)
    if args.compare:
        with open(args.compare, encoding="utf-8") as fh:
            differences = compare(json.load(fh), current)
        for old, new in differences:
            print("CHANGED  %s" % (new or old)["ask"])
            print("    was: %s" % (_dump(old) if old else "-"))
            print("    now: %s" % (_dump(new) if new else "-"))
        print("%d replies differ from %s" % (len(differences), args.compare))
        failed += bool(differences)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
