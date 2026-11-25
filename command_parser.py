"""
command_parser.py
------------------
Turns raw recognized speech text into a structured command:
    {"action": "on" | "off", "leds": [1, 2, 3]}

Handles:
    "turn on led 1"
    "turn off led 2"
    "turn on led 1 and led 2"
    "turn off led 2 and led 3"
    "turn on all leds"
    "turn off all leds"

Also tolerates common speech-recognition quirks: missing spaces
("led1"), spelled-out numbers ("led one"), and "lead/let/red" as
mis-hearings of "led".
"""

import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3,
    "won": 1,   # common mis-hearing of "one"
    "to": 2, "too": 2,  # common mis-hearings of "two"
    "tree": 3, "free": 3,  # common mis-hearings of "three"
}

VALID_LEDS = {1, 2, 3}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    # Normalize common mis-hearings of "led" from offline STT engines
    # "LED" spoken as letters. Vosk splits it unpredictably: "l e d",
    # "el e d", "el e ee d", "el ee dee"... Any run of letter sounds that
    # starts with l/el and ends with d/dee becomes "led".
    text = re.sub(r"\b(?:el|l)\.?(?:\s+(?:ee|e|el|l)\.?)*\s+(?:dee|d)\b\.?", "led", text)
    text = re.sub(r"\b(leads|leds|lids|lits|lads)\b", "leds", text)
    text = re.sub(r"\b(lead|let|red|ed|lid|lit|lad)\b", "led", text)
    return text


def parse_command(text: str):
    """
    Returns {"action": "on"/"off", "leds": [ints]} or None if no
    valid command could be parsed from the text.
    """
    if not text:
        return None

    text = _normalize(text)

    # Determine action -- check "off" first since "turn off" also
    # contains no overlap risk, but order matters if phrasing varies.
    if re.search(r"\bturn\s+off\b|\bswitch\s+off\b", text):
        action = "off"
    elif re.search(r"\bturn\s+on\b|\bswitch\s+on\b", text):
        action = "on"
    else:
        return None

    # "all" case
    if "all" in text:
        return {"action": action, "leds": sorted(VALID_LEDS)}

    # Collect every number said after "on"/"off": digits or number words.
    # Handles "led one and led two", "led two and three", "one and two".
    after = re.split(r"\b(?:on|off)\b", text, maxsplit=1)[-1]
    leds = set()
    for tok in re.findall(r"\d+|[a-z]+", after):
        if tok.isdigit():
            leds.add(int(tok))
        elif tok in NUMBER_WORDS:
            leds.add(NUMBER_WORDS[tok])
    leds = sorted(l for l in leds if l in VALID_LEDS)

    if not leds:
        return None

    return {"action": action, "leds": leds}


_CANONICAL = {"won": "one", "to": "two", "too": "two", "tree": "three", "free": "three"}


def clean_transcript(text: str) -> str:
    """Human-readable version of what Vosk heard, e.g.
    'turn off el e ee d won and two' -> 'turn off led one and two'."""
    if not text:
        return ""
    text = _normalize(text)
    parts = re.split(r"\b(on|off)\b", text, maxsplit=1)
    if len(parts) == 3:
        head, act, tail = parts
        tail = re.sub(r"\b(won|too|to|tree|free)\b", lambda m: _CANONICAL[m.group(1)], tail)
        text = f"{head}{act}{tail}"
    return re.sub(r"\s+", " ", text).strip()