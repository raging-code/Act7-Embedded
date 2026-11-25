"""
command_parser.py
------------------
Turns raw recognized speech text into a structured command:
    {"action": "on" | "off", "leds": [1, 2, 3]}

Handles:
    "turn on led 1"            "turn off led 2"
    "turn on led 1 and led 2"  "turn on all leds"

Tolerates speech-recognition quirks and messy speech:
  * "LED" said as letters ("el e ee d", "l e d") or misheard (lead/let/red)
  * fillers / hesitations: "um", "uh", "hmmm", "[unk]" are ignored
  * stutters / repeats: "turn turn on", "l l e d", "one one" are collapsed
  * self-correction: "turn on ... turn off led one" -> the LAST command wins
  * mishearings of numbers: won -> one, too/to -> two, tree/free -> three
"""

import re

VALID_LEDS = {1, 2, 3}

# Always a number.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3,
    "won": 1, "tree": 3, "free": 3,
}
# Ambiguous: only a number right after "led" or "and" (otherwise it's
# probably just a hesitation Vosk mapped to "to").
AMBIGUOUS_NUMBER_WORDS = {"to": 2, "too": 2}

# Hesitation sounds to throw away completely.
FILLERS = {
    "um", "umm", "uh", "uhh", "uhm", "hmm", "hm", "hmmm", "hmmmm", "mm", "mmm",
    "mhm", "er", "erm", "err", "ah", "aah", "eh", "huh", "ha", "hah", "oh",
    "[unk]", "unk",
}

_CANONICAL = {"won": "one", "to": "two", "too": "two", "tree": "three", "free": "three"}

_LED_LETTERS = re.compile(
    r"\b(?:el|l)\.?(?:\s+(?:ee|e|el|l)\.?)*\s+(?:dee|d)\b\.?"
)


def _tokens(text: str) -> list:
    """Lower-case, drop fillers, fold LED spellings to 'led', collapse repeats."""
    text = (text or "").lower().replace("[unk]", " ")
    words = [w for w in re.findall(r"\d+|[a-z]+", text) if w not in FILLERS]
    text = " ".join(words)

    # "LED" spoken as letters: "l e d", "el e ee d", "l l e d", "el ee dee"...
    text = _LED_LETTERS.sub("led", text)
    text = re.sub(r"\b(leads|leds|lids|lits|lads)\b", "leds", text)
    text = re.sub(r"\b(lead|let|red|ed|lid|lit|lad)\b", "led", text)

    # Collapse stutters: "turn turn on" -> "turn on", "led led one" -> "led one"
    out = []
    for tok in text.split():
        if not out or out[-1] != tok:
            out.append(tok)
    return out


def _find_action(tokens: list):
    """Return (action, index just after the action phrase) of the LAST
    'turn/switch on/off', else of the last bare 'on'/'off'."""
    found = None
    for i in range(len(tokens) - 1):
        if tokens[i] in ("turn", "switch") and tokens[i + 1] in ("on", "off"):
            found = (tokens[i + 1], i + 2, i)
    if found:
        return found
    for i, tok in enumerate(tokens):
        if tok in ("on", "off"):
            found = (tok, i + 1, i)
    return found


def _numbers(tokens: list) -> list:
    leds = set()
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else ""
        if tok.isdigit():
            leds.add(int(tok))
        elif tok in NUMBER_WORDS:
            leds.add(NUMBER_WORDS[tok])
        elif tok in AMBIGUOUS_NUMBER_WORDS and prev in ("led", "leds", "and"):
            leds.add(AMBIGUOUS_NUMBER_WORDS[tok])
    return sorted(l for l in leds if l in VALID_LEDS)


def parse_command(text: str):
    """
    Returns {"action": "on"/"off", "leds": [ints]} or None if no
    valid command could be parsed from the text.
    """
    tokens = _tokens(text)
    if not tokens:
        return None

    found = _find_action(tokens)
    if not found:
        return None
    action, after_idx, _ = found
    after = tokens[after_idx:]

    if "all" in after or "everything" in after:
        return {"action": action, "leds": sorted(VALID_LEDS)}

    leds = _numbers(after)
    if not leds:  # e.g. "led one turn on" (number said before the action)
        leds = _numbers(tokens[: after_idx])
    if not leds:
        return None
    return {"action": action, "leds": leds}


def clean_transcript(text: str) -> str:
    """Human-readable version of what was heard, with fillers/stutters removed,
    e.g. 'um turn turn off el e ee d won and two' -> 'turn off led one and two'."""
    tokens = _tokens(text)
    if not tokens:
        return ""
    found = _find_action(tokens)
    if found and found[2] > 0:
        tokens = tokens[found[2]:]  # drop abandoned earlier attempts
    out = []
    seen_action = False
    for i, tok in enumerate(tokens):
        if tok in ("on", "off"):
            seen_action = True
        elif seen_action and tok in _CANONICAL:
            if tok in AMBIGUOUS_NUMBER_WORDS and (i == 0 or tokens[i - 1] not in ("led", "leds", "and")):
                continue
            tok = _CANONICAL[tok]
        out.append(tok)
    return " ".join(out)