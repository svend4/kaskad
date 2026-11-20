"""Find the modules and run one turn through them.

Derived from bot/runtime.py of PyBot (https://github.com/huggingface/pybot), Apache License 2.0.
Changed from the original:
* modules are plain .py files loaded from one or more folders, not from a package;
* respond() may return a dict with "reply", "action", "confidence" and "unknown", not only text;
* every respond() call runs in its own daemon thread under one deadline per turn, so a slow or
  hung module is skipped instead of holding up the turn, and a module still busy is not started
  again; with turn_timeout=None the calls run one after another in the calling thread;
* the arbiter is passed in, the turn reports skipped modules and its duration, history is capped.

A module is a .py file whose name does not start with "_". It may define:

    PRIORITY = 0        higher goes first; 9000 and up is the safety band, below 0 the floor
    LEVEL = 3           the assistant level it answers on (reported, not used for choosing)
    observe(text, memory)            remember something; never replies
    respond(text, memory) -> answer  a string, a dict {"reply": ...}, or None to pass
    after(text, reply, memory)       runs once the reply is chosen

Each respond() works on its own copy of memory, and the chosen module's copy becomes the memory.
memory["turns"] is shared and written only by the runtime. A module that fails is skipped.
"""
import collections
import copy
import importlib.util
import os
import sys
import threading
import time

Module = collections.namedtuple("Module", "name priority level code")


def load_modules(folders):
    """([Module], [(name, error)]), highest PRIORITY first, then by name."""
    loaded, errors, seen = [], [], set()
    for folder in folders:
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            errors.append((folder, "not a folder"))
            continue
        if folder not in sys.path:
            sys.path.append(folder)  # lets modules import helper files whose names start with "_"
        for filename in sorted(os.listdir(folder)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            name = filename[:-3]
            if name in seen:
                errors.append((name, "duplicate module name"))
                continue
            try:
                spec = importlib.util.spec_from_file_location("kaskad_modules." + name, os.path.join(folder, filename))
                code = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(code)
            except Exception as error:  # noqa: BLE001 - a broken module must not stop the others
                errors.append((name, "%s: %s" % (type(error).__name__, error)))
                continue
            seen.add(name)
            priority = getattr(code, "PRIORITY", 0)
            if isinstance(priority, bool) or not isinstance(priority, (int, float)):
                priority = 0
            loaded.append(Module(name, priority, getattr(code, "LEVEL", 3), code))
    loaded.sort(key=lambda module: (-module.priority, module.name))
    return loaded, errors


def _copy(memory):
    """A module's own copy of memory; memory["turns"] stays shared."""
    try:
        return {key: value if key == "turns" else copy.deepcopy(value) for key, value in memory.items()}
    except Exception:  # noqa: BLE001 - a value that cannot be copied: share memory instead
        return memory


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def _reason(error):
    return "%s: %s" % (type(error).__name__, error)


def candidate(module, answer):
    """The answer as a candidate dict, or None when the module passed or answered nonsense."""
    action, confidence, unknown = None, None, False
    if isinstance(answer, dict):
        reply = answer.get("reply")
        action = answer.get("action") if isinstance(answer.get("action"), dict) else None
        confidence = _number(answer.get("confidence"))
        unknown = bool(answer.get("unknown", False))
    else:
        reply = answer
    if not isinstance(reply, str) or not reply.strip():
        return None
    return {"module": module.name, "priority": module.priority, "level": module.level,
            "reply": reply.strip(), "action": action, "confidence": confidence, "unknown": unknown}


class _Job:
    """One respond() call in a daemon thread, so a hung module never blocks the process exit."""

    def __init__(self, module, memory, function, text):
        self.module, self.memory = module, memory
        self.result, self.error = None, None
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(function, text), daemon=True,
                                        name="kaskad-" + module.name)

    def _run(self, function, text):
        try:
            self.result = function(text, self.memory)
        except Exception as error:  # noqa: BLE001 - reported as a skipped module
            self.error = error
        finally:
            self.done.set()

    def start(self):
        self._thread.start()
        return self

    def run_here(self, function, text):
        self._run(function, text)
        return self


class Runtime:
    def __init__(self, folders, turn_timeout=2.0, history_limit=20):
        if isinstance(folders, str):
            folders = [folders]
        self.modules, self.errors = load_modules(folders)
        self.turn_timeout = turn_timeout
        self.history_limit = history_limit
        self._busy = {}

    def _hook(self, name, args, skipped):
        for module in self.modules:
            function = getattr(module.code, name, None)
            if callable(function):
                try:
                    function(*args)
                except Exception as error:  # noqa: BLE001
                    skipped.append({"module": module.name, "phase": name, "reason": _reason(error)})

    def _respond(self, text, memory, skipped):
        jobs = []
        for module in self.modules:
            respond = getattr(module.code, "respond", None)
            if not callable(respond):
                continue
            busy = self._busy.get(module.name)
            if busy is not None:
                if not busy.done.is_set():
                    skipped.append({"module": module.name, "phase": "respond", "reason": "still busy"})
                    continue
                del self._busy[module.name]
            job = _Job(module, _copy(memory), respond, text)
            jobs.append(job.run_here(respond, text) if self.turn_timeout is None else job.start())
        if self.turn_timeout is not None:
            deadline = time.perf_counter() + self.turn_timeout
            for job in jobs:
                job.done.wait(max(0.0, deadline - time.perf_counter()))
        return jobs

    def turn(self, text, memory, arbiter):
        """One turn. Updates memory in place and returns what happened."""
        start = time.perf_counter()
        skipped = []
        self._hook("observe", (text, memory), skipped)

        candidates, copies = [], []
        for job in self._respond(text, memory, skipped):
            name = job.module.name
            if not job.done.is_set():
                self._busy[name] = job
                skipped.append({"module": name, "phase": "respond", "reason": "timeout"})
            elif job.error is not None:
                skipped.append({"module": name, "phase": "respond", "reason": _reason(job.error)})
            else:
                found = candidate(job.module, job.result)
                if found is not None:
                    candidates.append(found)
                    copies.append(job.memory)

        chosen = None
        if candidates:
            index = 0
            try:
                picked = arbiter(text, memory, [dict(c) for c in candidates])
                if isinstance(picked, int) and not isinstance(picked, bool) and 0 <= picked < len(candidates):
                    index = picked
            except Exception as error:  # noqa: BLE001 - a broken arbiter falls back to the first candidate
                skipped.append({"module": "arbiter", "phase": "choose", "reason": _reason(error)})
            chosen = candidates[index]
            if copies[index] is not memory:
                memory.clear()
                memory.update(copies[index])

        reply = chosen["reply"] if chosen else None
        self._hook("after", (text, reply, memory), skipped)
        turns = memory.get("turns")
        if not isinstance(turns, list):
            turns = memory["turns"] = []
        turns.append({"user": text, "bot": reply, "module": chosen["module"] if chosen else None})
        if self.history_limit and len(turns) > self.history_limit:
            del turns[:-self.history_limit]

        result = dict(chosen) if chosen else {"module": None, "priority": None, "level": None, "reply": None,
                                               "action": None, "confidence": None, "unknown": True}
        result.update(candidates=candidates, skipped=skipped, ms=round((time.perf_counter() - start) * 1000, 1))
        return result
