"""ASCII-art avatar for each session.

Each session gets a multi-line ASCII face. The face's expression (eyes +
mouth) reflects the current mood. The "head shape" is picked by keyword
match on the session name — "monkey" → a hairy head, "fox" → pointy
ears, etc., with a stable hash fallback so anonymous names still get a
distinctive look.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Mood → (eyes, mouth). Each pair has the same character width so the art
# doesn't shift when mood changes.

_MOOD_FACE: dict[str, tuple[str, str]] = {
    # mood          eyes    mouth
    "neutral":    ("o   o", " - "),
    "happy":      ("^   ^", "\\_/"),
    "cheerful":   ("^   ^", "\\_/"),
    "frustrated": ("X   X", " o "),
    "scared":     ("O   O", " o "),
    "stressed":   (">   <", "/-\\"),
    "confused":   ("@   o", " ? "),
    "sleepy":     ("-   -", " _ "),
}


# ---------------------------------------------------------------------------
# Head shapes. The {eyes} and {mouth} placeholders get filled per mood.
# Every template has the same line count (5) so swapping creatures doesn't
# shift layout.

_TEMPLATES: dict[str, str] = {
    # Monkey: tuft of fur on top, fuzzy cheeks.
    "monkey": """\
   /^~^~^\\
  ( {eyes} )
   )  {mouth}  (
    `---'
   /     \\   """,

    # Fox / wolf: pointy ears.
    "fox": """\
   /\\___/\\
  ( {eyes} )
   )  {mouth}  (
    `---'
   /     \\   """,

    # Cat / kitten: small triangle ears, whiskers.
    "cat": """\
   /\\   /\\
  ( {eyes} )
   )- {mouth} -(
    `---'
              """,

    # Robot: square head, antenna.
    "robot": """\
    | _ |
   [-----]
   [ {eyes} ]
   [ {mouth} ]
   [_____]   """,

    # Dragon: horns, fangs.
    "dragon": """\
   /\\ ^ /\\
  ( {eyes} )
   ) v{mouth}v (
    `---'
   /     \\   """,

    # Ghost: floaty.
    "ghost": """\
   _____
  /     \\
 |  {eyes}  |
 |  {mouth}  |
  ^^^^^^^   """,

    # Owl: round, big eyes.
    "owl": """\
   ,-, ,-,
   ( o o )
  ((     ))
  (( {mouth} ))
   ^^   ^^   """,

    # Bear / panda: rounded ears.
    "bear": """\
   (o   o)
  /  ___  \\
 |  ( {eyes} ) |
  \\  {mouth}  /
   `-----'   """,

    # Skull: spooky default-ish.
    "skull": """\
   _____
  / ___ \\
 |  {eyes}  |
 |  {mouth}  |
   \\===/    """,

    # Generic blob for anything unrecognized.
    "_generic": """\
   .-----.
  /       \\
 |  {eyes}  |
  \\  {mouth}  /
   `-----'   """,
}


# Keyword in session name → template id. First match wins; case-insensitive.
_KEYWORD_TO_TEMPLATE: list[tuple[str, str]] = [
    # monkey-ish
    ("monkey", "monkey"), ("ape", "monkey"), ("gorilla", "monkey"),
    # canids
    ("fox", "fox"), ("wolf", "fox"), ("dog", "fox"), ("puppy", "fox"),
    # felines
    ("kitten", "cat"), ("cat", "cat"), ("tiger", "cat"), ("lion", "cat"),
    # mechanical
    ("robot", "robot"), ("bot", "robot"), ("droid", "robot"), ("cyber", "robot"),
    # mythical
    ("dragon", "dragon"), ("wyrm", "dragon"), ("serpent", "dragon"),
    # spooky
    ("ghost", "ghost"), ("spirit", "ghost"), ("specter", "ghost"),
    ("skull", "skull"), ("death", "skull"), ("dead", "skull"),
    # owls / sleepy
    ("owl", "owl"), ("night", "owl"),
    # bears
    ("bear", "bear"), ("panda", "bear"),
]


_FALLBACK_TEMPLATES = ["fox", "owl", "bear", "monkey", "dragon", "ghost"]


@dataclass(frozen=True)
class Avatar:
    template_id: str
    art: str
    mood: str


def template_for(name: str) -> str:
    """Pick a template id for a session name. Deterministic."""
    lowered = name.lower()
    for word, tid in _KEYWORD_TO_TEMPLATE:
        if word in lowered:
            return tid
    if not name:
        return "_generic"
    h = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    return _FALLBACK_TEMPLATES[h % len(_FALLBACK_TEMPLATES)]


def render(name: str, mood: str = "neutral") -> Avatar:
    """Return the rendered ASCII art for a session at a given mood."""
    tid = template_for(name)
    template = _TEMPLATES.get(tid, _TEMPLATES["_generic"])
    eyes, mouth = _MOOD_FACE.get(mood, _MOOD_FACE["neutral"])
    art = template.format(eyes=eyes, mouth=mouth)
    return Avatar(template_id=tid, art=art, mood=mood)
