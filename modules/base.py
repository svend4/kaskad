"""Your own question-and-answer base and smart-home commands, in Russian, German and English.

Reads data/base.json, or the file named by KASKAD_DATA. Matching works on word stems (see
kaskad.text.has): every keyword group of an FAQ entry needs one hit, and a device command needs
a device word and a state word, with an optional area.
"""
import json
import os

from kaskad.text import has_any, language, matches, pick, words

PRIORITY = 500
LEVEL = 3

_PATH = os.environ.get("KASKAD_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "base.json")
with open(_PATH, encoding="utf-8") as _fh:
    _BASE = json.load(_fh)


def _device(text_words, joined, lang):
    for command in _BASE.get("devices", []):
        if not has_any(command["device"], text_words, joined):
            continue
        state = next((name for name, stems in command["states"].items() if has_any(stems, text_words, joined)), None)
        if state is None:
            continue
        area = next((name for name, stems in command.get("areas", {}).items()
                     if has_any(stems, text_words, joined)), None)
        where = pick(command["area_names"][area], lang) if area else pick(command["everywhere"], lang)
        return {"reply": pick(command["replies"][state], lang).format(area=where),
                "action": {"service": command["services"][state], "area": area},
                "confidence": 0.9}
    return None


def respond(text, memory):
    text_words = words(text)
    if not text_words:
        return None
    lang = language(text)
    found = _device(text_words, " ".join(text_words), lang)
    if found:
        return found
    for entry in _BASE.get("faq", []):
        if matches(entry["keywords"], text_words):
            return {"reply": pick(entry["answer"], lang), "confidence": 0.9}
    return None
