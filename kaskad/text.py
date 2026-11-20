"""Small text helpers for modules that match words themselves, in any language."""
import re

_WORD = re.compile(r"\w+")
_GERMAN = {"ich", "du", "das", "die", "der", "im", "wie", "was", "ist", "mach", "mache", "bitte",
           "licht", "nicht", "und", "heiße", "heisse"}


def words(text):
    """The lower-cased words of the text."""
    return _WORD.findall(text.casefold())


def language(text):
    """"ru", "de" or "en": a rough guess, good enough to pick the language of a reply."""
    folded = text.casefold()
    if re.search("[а-яё]", folded):
        return "ru"
    if re.search("[äöüß]", folded) or _GERMAN & set(words(folded)):
        return "de"
    return "en"


def has(stem, text_words, joined=None):
    """True when a word starts with stem.

    Stems of up to three letters must be whole words ("an", "aus", "on"), and a stem with a space
    is looked for as a phrase ("living room", "bis wann").
    """
    if " " in stem:
        return stem in (joined if joined is not None else " ".join(text_words))
    if len(stem) <= 3:
        return stem in text_words
    return any(word.startswith(stem) for word in text_words)


def has_any(stems, text_words, joined=None):
    return any(has(stem, text_words, joined) for stem in stems)


def matches(groups, text_words):
    """True when every group of stems has at least one hit in the words."""
    joined = " ".join(text_words)
    return all(has_any(group, text_words, joined) for group in groups)


def pick(variants, lang):
    """The variant for lang, else the English one, else any."""
    return variants.get(lang) or variants.get("en") or next(iter(variants.values()), "")
