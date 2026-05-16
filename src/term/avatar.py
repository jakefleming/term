"""ASCII-art avatars for agents (and sessions as a fallback).

The card in the sidebar shows a 5-line portrait. The face's expression
(eyes + mouth) reflects the current mood; the head shape and headgear
are picked by keyword match on the agent's display name — "commander"
gets an army helmet, "wizard" gets a pointy hat, "pirate" gets a
bandana, etc. — with a stable hash fallback so unknown names still
land on a distinctive look.

Every template is exactly 5 lines tall. Eyes are 5 chars wide
(`o   o`), mouth is 3 chars wide (` - `); all templates substitute
those into the same slot widths so the art doesn't shift on a mood
change.

Drawing convention: the head's left edge sits at column 2 and its
right edge at column 10 (the parentheses in `( {eyes} )`), so any
top-line decoration should live between columns 2 and 10 to look
"on the head."
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
    "frustrated": ("x   x", " o "),
    "scared":     ("O   O", " o "),
    "stressed":   (">   <", "/-\\"),
    "confused":   ("@   o", " ? "),
    "sleepy":     ("-   -", " _ "),
}


# ---------------------------------------------------------------------------
# Templates. Each is 5 lines. {eyes} (5 wide) and {mouth} (3 wide) get
# mood-substituted; the rest is decoration / head shape.

_TEMPLATES: dict[str, str] = {

    # --- creatures -----------------------------------------------------

    "_generic": """\
   .---.
  ( {eyes} )
   ) {mouth} (
    `---'
             """,

    "cat": """\
  /\\     /\\
  ( {eyes} )
   )-{mouth}-(
    `---'
             """,

    "fox": """\
  /\\__ __/\\
  ( {eyes} )
   ) {mouth} (
    `-v-'
             """,

    "monkey": """\
   /^~^~^\\
  ( {eyes} )
   )  {mouth}  (
    `---'
   /     \\   """,

    "robot": """\
   _|_|_
  [-----]
  [ {eyes} ]
  [ {mouth} ]
  [_____]   """,

    "dragon": """\
   /\\ V /\\
  ( {eyes} )
   ) v{mouth}v (
    `---'
   /     \\   """,

    "ghost": """\
   .---.
  /     \\
 (  {eyes}  )
 |  {mouth}  |
  ^^v^v^^   """,

    "owl": """\
   ,-' '-,
  ( ,-^-, )
  | {eyes} |
  |  {mouth}  |
   ^^   ^^  """,

    "bear": """\
  (_)   (_)
   /---\\
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "rabbit": """\
   /|   |\\
   ||   ||
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "skull": """\
   _____
  / ___ \\
 |  {eyes}  |
  \\  {mouth}  /
   `===`   """,

    "alien": """\
   o   o
   |   |
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "clown": """\
   .-=*=-.
  ( {eyes} )
   ) {mouth} (
    `-O-'
    /   \\   """,

    # --- headgear (the "personality" set) ------------------------------
    # All built on the standard head outline so the eyes/mouth slot
    # math stays identical; only line 0 changes.

    "commander": """\
   __=*=__
  ( {eyes} )
   ) {mouth} (
    `---'
   /=====\\  """,

    "knight": """\
   |||||||
  [ {eyes} ]
  [ {mouth} ]
   `-----'
   /=====\\  """,

    "wizard": """\
     /\\
    / *\\
   /__  \\
  ( {eyes} )
   ) {mouth} (""",

    "pirate": """\
   \\__+__/
  ( {eyes} )
   ) {mouth} (
    `---'
             """,

    "king": """\
   .vVv.
  ( {eyes} )
   ) {mouth} (
    `---'
             """,

    "ninja": """\
   #######
  [#{eyes}#]
   ) {mouth} (
    `---'
             """,

    "devil": """\
   \\,   ,/
    v   v
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "angel": """\
    (___)
   .-----.
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "chef": """\
   .---.
  /     \\
  '-----'
  ( {eyes} )
   ) {mouth} (""",

    "detective": """\
   ___ o
  /===/ \\
  ( {eyes} )
   ) {mouth} (
    `---'   """,

    "punk": """\
  /|/|/|/|
  ( {eyes} )
   ) {mouth} (
    `---'
             """,
}


# ---------------------------------------------------------------------------
# Keyword → template id. Substring match on the lowercased agent name;
# first match wins, so list more-specific keys first.

_KEYWORD_TO_TEMPLATE: list[tuple[str, str]] = [
    # --- headgear / personalities ---
    # Military / commander. "mander" catches names like "Codemander".
    ("commander", "commander"), ("mander", "commander"),
    ("captain", "commander"), ("general", "commander"),
    ("colonel", "commander"), ("officer", "commander"),
    ("soldier", "commander"), ("sergeant", "commander"),
    ("army", "commander"), ("major", "commander"),
    ("marine", "commander"), ("admiral", "commander"),

    # Knight / armor
    ("knight", "knight"), ("paladin", "knight"),
    ("squire", "knight"), ("templar", "knight"),
    ("crusader", "knight"),

    # Magic
    ("wizard", "wizard"), ("mage", "wizard"),
    ("sorcerer", "wizard"), ("witch", "wizard"),
    ("warlock", "wizard"), ("merlin", "wizard"),
    ("gandalf", "wizard"), ("magus", "wizard"),

    # Sea / pirate
    ("pirate", "pirate"), ("buccaneer", "pirate"),
    ("corsair", "pirate"), ("sailor", "pirate"),
    ("matey", "pirate"),

    # Royalty
    ("king", "king"), ("queen", "king"),
    ("prince", "king"), ("princess", "king"),
    ("royal", "king"), ("majesty", "king"),
    ("duke", "king"), ("duchess", "king"),

    # Stealth
    ("ninja", "ninja"), ("shinobi", "ninja"),
    ("assassin", "ninja"), ("stealth", "ninja"),
    ("shadow", "ninja"),

    # Devil
    ("devil", "devil"), ("demon", "devil"),
    ("satan", "devil"), ("imp", "devil"),
    ("hell", "devil"), ("daemon", "devil"),

    # Angel
    ("angel", "angel"), ("seraph", "angel"),
    ("cherub", "angel"), ("halo", "angel"),
    ("divine", "angel"),

    # Cook
    ("chef", "chef"), ("cook", "chef"),
    ("baker", "chef"),

    # Detective / sleuth (deerstalker + monocle)
    ("detective", "detective"), ("sherlock", "detective"),
    ("sleuth", "detective"), ("inspector", "detective"),

    # Punk
    ("punk", "punk"), ("rocker", "punk"),
    ("mohawk", "punk"),

    # --- creatures ---
    # Apes
    ("monkey", "monkey"), ("ape", "monkey"),
    ("gorilla", "monkey"), ("chimp", "monkey"),
    ("baboon", "monkey"),

    # Canids
    ("fox", "fox"), ("wolf", "fox"),
    ("dog", "fox"), ("puppy", "fox"),
    ("hound", "fox"), ("husky", "fox"),
    ("doberman", "fox"), ("coyote", "fox"),

    # Felines
    ("kitten", "cat"), ("cat", "cat"),
    ("tiger", "cat"), ("lion", "cat"),
    ("feline", "cat"), ("panther", "cat"),
    ("leopard", "cat"), ("cheetah", "cat"),
    ("lynx", "cat"),

    # Machines
    ("robot", "robot"), ("bot", "robot"),
    ("droid", "robot"), ("cyber", "robot"),
    ("android", "robot"), ("mech", "robot"),
    ("automa", "robot"),  # automaton, automatic

    # Reptiles / mythical
    ("dragon", "dragon"), ("wyrm", "dragon"),
    ("serpent", "dragon"), ("hydra", "dragon"),
    ("draco", "dragon"),

    # Spooky
    ("ghost", "ghost"), ("spirit", "ghost"),
    ("specter", "ghost"), ("spectre", "ghost"),
    ("phantom", "ghost"), ("wraith", "ghost"),
    ("haunt", "ghost"), ("boo", "ghost"),

    ("skull", "skull"), ("death", "skull"),
    ("dead", "skull"), ("corpse", "skull"),
    ("zombie", "skull"), ("reaper", "skull"),
    ("bones", "skull"),

    # Owl
    ("owl", "owl"), ("night", "owl"),
    ("hoot", "owl"),

    # Bear
    ("bear", "bear"), ("panda", "bear"),
    ("grizzly", "bear"), ("teddy", "bear"),
    ("ursa", "bear"),

    # Rabbit
    ("rabbit", "rabbit"), ("bunny", "rabbit"),
    ("hare", "rabbit"),

    # Aliens
    ("alien", "alien"), ("ufo", "alien"),
    ("space", "alien"), ("martian", "alien"),
    ("xenon", "alien"), ("zorg", "alien"),

    # Clown / fool
    ("clown", "clown"), ("jester", "clown"),
    ("fool", "clown"), ("joker", "clown"),
    ("buffoon", "clown"),
]


# Hash fallback: pick from a wide variety so anonymous names still get
# a memorable look. Includes a few headgear templates so random agents
# might land on a knight or a wizard.
_FALLBACK_TEMPLATES = [
    "fox", "owl", "bear", "monkey", "dragon", "ghost",
    "skull", "alien", "rabbit", "robot", "cat",
    "commander", "wizard", "pirate", "ninja", "knight",
]


@dataclass(frozen=True)
class Avatar:
    template_id: str
    art: str
    mood: str


def template_for(name: str) -> str:
    """Pick a template id for an agent (or session) name. Deterministic."""
    lowered = name.lower()
    for word, tid in _KEYWORD_TO_TEMPLATE:
        if word in lowered:
            return tid
    if not name:
        return "_generic"
    h = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    return _FALLBACK_TEMPLATES[h % len(_FALLBACK_TEMPLATES)]


def render(name: str, mood: str = "neutral") -> Avatar:
    """Return the rendered ASCII art for a name at a given mood."""
    tid = template_for(name)
    template = _TEMPLATES.get(tid, _TEMPLATES["_generic"])
    eyes, mouth = _MOOD_FACE.get(mood, _MOOD_FACE["neutral"])
    art = template.format(eyes=eyes, mouth=mouth)
    return Avatar(template_id=tid, art=art, mood=mood)
