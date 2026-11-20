"""The floor: answers only when nobody else does, and says plainly that it does not know.

Its answer is marked "unknown", which is what lets the host hand the question to a language model.
"""
from kaskad.text import language

PRIORITY = -1000
LEVEL = 3

_REPLY = {"ru": "Этого я пока не знаю.", "de": "Das weiß ich noch nicht.", "en": "I don't know that yet."}


def respond(text, memory):
    return {"reply": _REPLY[language(text)], "unknown": True}
