"""Default arbiter: pick one candidate per turn with a few plain rules.

Candidates arrive highest PRIORITY first. The rules, in order:
1. the safety band (PRIORITY 9000 and up) answers whenever it speaks;
2. a real answer beats one marked "unknown";
3. among real answers, one that only repeats the bot's previous reply is passed over while another
   one says something else; the same question asked twice still gets the same answer twice;
4. then the highest PRIORITY wins, then the higher confidence (none given counts as 1.0), then
   the earlier candidate.

Write your own with the same signature and pass it as Assistant(arbiter=...).
"""
SAFETY = 9000


def choose(text, memory, candidates):
    """Index of the chosen candidate."""
    for index, found in enumerate(candidates):
        if found["priority"] >= SAFETY:
            return index
    every = list(range(len(candidates)))
    known = [i for i in every if not candidates[i]["unknown"]] or every
    turns = memory.get("turns") or []
    last = turns[-1].get("bot") if turns and isinstance(turns[-1], dict) else None
    fresh = [i for i in known if candidates[i]["reply"] != last] or known

    def rank(i):
        found = candidates[i]
        confidence = 1.0 if found["confidence"] is None else found["confidence"]
        return (found["priority"], confidence, -i)

    return max(fresh, key=rank)
