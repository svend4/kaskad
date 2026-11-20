"""Remember the user's name and say it back: an example of observe() and session memory."""
import re

from kaskad.text import language

PRIORITY = 600
LEVEL = 3

_INTRO = re.compile(r"\b(?:меня зовут|ich hei(?:ß|ss)e|my name is)\s+([^\W\d_][^\W\d_-]*)", re.IGNORECASE)
_QUESTION = re.compile(r"\b(?:как меня зовут|wie hei(?:ß|ss)e ich|what(?:'s| is) my name)\b", re.IGNORECASE)

_HELLO = {"ru": "Приятно познакомиться, {name}.", "de": "Freut mich, {name}.", "en": "Nice to meet you, {name}."}
_NAME = {"ru": "Вас зовут {name}.", "de": "Sie heißen {name}.", "en": "Your name is {name}."}
_NO_NAME = {"ru": "Вы ещё не сказали, как вас зовут.", "de": "Sie haben mir Ihren Namen noch nicht gesagt.",
            "en": "You haven't told me your name yet."}


def observe(text, memory):
    found = _INTRO.search(text)
    if found:
        name = found.group(1)
        memory["name"] = name[:1].upper() + name[1:]


def respond(text, memory):
    lang = language(text)
    if _INTRO.search(text) and memory.get("name"):
        return {"reply": _HELLO[lang].format(name=memory["name"]), "confidence": 0.9}
    if _QUESTION.search(text):
        if memory.get("name"):
            return {"reply": _NAME[lang].format(name=memory["name"]), "confidence": 0.9}
        return {"reply": _NO_NAME[lang], "confidence": 0.9}
    return None
