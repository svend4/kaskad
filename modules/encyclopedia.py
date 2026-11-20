"""Level 4: look the question up in a local encyclopedia before the assistant gives up.

An SQLite FTS5 full-text index from the standard library. By default it is built in memory from
the "encyclopedia" articles in data/base.json. For a real Wikipedia dump, build the same table
once into a file with build_index() and point KASKAD_ENCYCLOPEDIA at that file.
"""
import json
import os
import pathlib
import sqlite3

from kaskad.text import words

PRIORITY = -10  # after every module that knows the subject, before the floor
LEVEL = 4

_DATA = os.environ.get("KASKAD_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "base.json")
_STOP = {
    "what", "who", "the", "and", "how", "explain", "tell", "about", "does", "for", "you", "can",
    "что", "такое", "как", "это", "кто", "где", "расскажи", "про",
    "was", "ist", "der", "die", "das", "wie", "ein", "eine", "erkläre", "mir",
}


def build_index(path, pages):
    """Create or extend an FTS5 index at path from [(title, body)] and return the connection."""
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages "
               "USING fts5(title, body, tokenize='unicode61 remove_diacritics 2')")
    db.executemany("INSERT INTO pages (title, body) VALUES (?, ?)", pages)
    db.commit()
    return db


def _open():
    path = os.environ.get("KASKAD_ENCYCLOPEDIA")
    if path:
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False)
    with open(_DATA, encoding="utf-8") as fh:
        pages = [(page["title"], page["body"]) for page in json.load(fh).get("encyclopedia", [])]
    return build_index(":memory:", pages)


_DB = _open()


def respond(text, memory):
    found = [word for word in words(text) if len(word) > 2 and word not in _STOP]
    if not found:
        return None
    query = " OR ".join('"%s"*' % word for word in found)
    try:
        rows = _DB.execute("SELECT title, body FROM pages WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT 3",
                           (query,)).fetchall()
    except sqlite3.Error:
        return None
    for title, body in rows:
        in_title = any(word in title.casefold() for word in found)
        in_body = sum(1 for word in found if word in body.casefold())
        if in_title or in_body >= 2:  # one stray word in a long article is not an answer
            return {"reply": "%s [%s]" % (body, title), "confidence": 0.8 if in_title else 0.6}
    return None
