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
}

VALID_LEDS = {1, 2, 3}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    # Normalize common mis-hearings of "led" from offline STT engines
    text = re.sub(r"\b(lead|let|red)\b", "led", text)
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

    # Extract numeric LED references: "led 1", "led1", "led 2"
    leds = set(int(n) for n in re.findall(r"led\s*(\d+)", text))

    # Extract spelled-out LED references: "led one", "led two"
    for word, num in NUMBER_WORDS.items():
        if re.search(rf"\bled\s+{word}\b", text):
            leds.add(num)

    # Keep only valid LED ids (1-3 in this setup)
    leds = sorted(l for l in leds if l in VALID_LEDS)

    if not leds:
        return None

    return {"action": action, "leds": leds}
