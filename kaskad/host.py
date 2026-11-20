"""The assistant as other programs see it: one call per user line.

Assistant.ask(text, session) runs the modules, decides whether the rules really knew the answer,
hands the question to a language model when they did not, carries out the chosen action and keeps
each session's memory. It returns a dict:

    reply       the text to say or show
    module      who answered: a module name, "llm" for the language model, None for nobody
    level       the assistant level of that module (1 for the language model)
    action      a structured action for the host to carry out, or None
    confidence  0..1 when the module gave one, else None
    unknown     True when the rules did not know: no answer, an "unknown" answer or low confidence
    escalated   True when the language model answered instead
    acted       True when on_action ran without an error
    skipped     modules left out this turn, with the reason (error, timeout, still busy)
    warnings    problems that did not stop the turn
    ms          time for the whole call

The modules run under a lock; the language model and the action run outside it, so one slow
answer does not hold up other sessions.
"""
import threading
import time

from . import arbiter as default_arbiter
from .runtime import Runtime
from .sessions import SessionStore
from .text import language

UNKNOWN = {"ru": "Не знаю.", "de": "Das weiß ich nicht.", "en": "I don't know."}


class Assistant:
    def __init__(self, modules, escalate=None, on_action=None, store=None, arbiter=None,
                 unknown_threshold=0.5, turn_timeout=2.0, history_limit=20, unknown_reply=None):
        self.runtime = Runtime(modules, turn_timeout=turn_timeout, history_limit=history_limit)
        self.escalate = escalate
        self.on_action = on_action
        self.store = store if store is not None else SessionStore()
        self.arbiter = arbiter or default_arbiter.choose
        self.unknown_threshold = unknown_threshold
        self.unknown_reply = unknown_reply or UNKNOWN
        self._lock = threading.Lock()

    @property
    def modules(self):
        return [module.name for module in self.runtime.modules]

    @property
    def errors(self):
        return list(self.runtime.errors)

    def _unknown_text(self, text):
        if isinstance(self.unknown_reply, str):
            return self.unknown_reply
        return self.unknown_reply.get(language(text)) or self.unknown_reply.get("en") or "?"

    def ask(self, text, session="default"):
        start = time.perf_counter()
        warnings = []
        with self._lock:
            memory = self.store.load(session)
            history = [dict(turn) for turn in memory.get("turns") or [] if isinstance(turn, dict)]
            turn = self.runtime.turn(text, memory, self.arbiter)

        confidence = turn["confidence"]
        unknown = bool(turn["module"] is None or turn["unknown"]
                       or (confidence is not None and confidence < self.unknown_threshold))
        if turn["priority"] is not None and turn["priority"] >= default_arbiter.SAFETY:
            unknown = False  # the safety band's answer is final: it is never handed on
        result = {"reply": turn["reply"] or self._unknown_text(text), "module": turn["module"],
                  "level": turn["level"], "action": turn["action"], "confidence": confidence,
                  "unknown": unknown, "escalated": False, "acted": False,
                  "skipped": turn["skipped"], "warnings": warnings}

        if unknown and self.escalate is not None:
            try:
                answer = self.escalate(text, history)
            except Exception as error:  # noqa: BLE001 - the rules' reply stands
                warnings.append("escalation failed: %s: %s" % (type(error).__name__, error))
                answer = None
            if isinstance(answer, str) and answer.strip():
                result.update(reply=answer.strip(), module="llm", level=1, action=None,
                              confidence=None, escalated=True)

        if result["action"] and self.on_action is not None:
            try:
                self.on_action(result["action"])
                result["acted"] = True
            except Exception as error:  # noqa: BLE001
                warnings.append("action failed: %s: %s" % (type(error).__name__, error))

        memory["turns"][-1].update(bot=result["reply"], module=result["module"])
        with self._lock:
            try:
                self.store.save(session, memory)
            except (TypeError, ValueError) as error:
                warnings.append("memory not saved: %s" % error)
        result["ms"] = round((time.perf_counter() - start) * 1000, 1)
        return result
