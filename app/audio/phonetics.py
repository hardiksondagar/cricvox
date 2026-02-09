"""
Phonetic pronunciation dictionary for cricket player names.
Maps player names to phonetically-friendly spellings for TTS engines.
"""

PHONETICS_MAP: dict[str, str] = {
    # Indian players
    "Rohit Sharma": "Rohit Sharma",
    "Yashasvi Jaiswal": "Yashasvi Jice-wall",
    "Virat Kohli": "Virat Koh-lee",
    "Suryakumar Yadav": "Surya-kumar Yadav",
    "Rishabh Pant": "Rishabh Punt",
    "Hardik Pandya": "Hardik Pund-ya",
    "Ravindra Jadeja": "Ravindra Juh-day-juh",
    "Axar Patel": "Axar Puh-tel",
    "Kuldeep Yadav": "Kul-deep Yadav",
    "Jasprit Bumrah": "Jasprit Boom-rah",
    # Australian players
    "Mitchell Starc": "Mitchell Stark",
    "Josh Hazlewood": "Josh Hazel-wood",
    "Pat Cummins": "Pat Cummins",
    "Glenn Maxwell": "Glenn Maxwell",
    "Adam Zampa": "Adam Zam-pah",
    "Marcus Stoinis": "Marcus Stoy-nis",
}


def apply_phonetics(text: str) -> str:
    """
    Replace player names in the commentary text with phonetic versions.
    Only replaces exact name matches to avoid partial replacements.
    """
    result = text
    for name, phonetic in PHONETICS_MAP.items():
        if name != phonetic:
            result = result.replace(name, phonetic)
    return result
