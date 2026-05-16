"""Smoke test: avatar selection + mood transitions.

Verifies:
  - "Hairy Monkey" → monkey template.
  - "Sneaky Fox" → fox template.
  - Unknown names fall back deterministically to one of the pool templates.
  - Mood faces change (eyes differ between neutral and scared).
  - MoodTracker flips after frustrated/happy text input.
"""

from __future__ import annotations

import sys

from term.avatar import Avatar, render, template_for
from term.mood import MoodTracker


def main() -> int:
    # 1. Keyword routing.
    assert template_for("Hairy Monkey") == "monkey"
    assert template_for("the monkey patrol") == "monkey"
    assert template_for("Sneaky Fox") == "fox"
    assert template_for("Sir Robot McRobotface") == "robot"
    assert template_for("Spooky Ghost") == "ghost"
    print("  keyword routing ok")

    # 2. Fallback is deterministic but spread across pool.
    fb = {template_for(f"unknown-{i}") for i in range(20)}
    assert len(fb) > 1, f"fallback pool too narrow: {fb}"
    # Same name twice → same template.
    assert template_for("anonymous") == template_for("anonymous")
    print(f"  fallback pool variety: {sorted(fb)}")

    # 3. Mood face differs.
    neutral = render("Hairy Monkey", "neutral")
    scared = render("Hairy Monkey", "scared")
    happy = render("Hairy Monkey", "happy")
    assert neutral.art != scared.art
    assert neutral.art != happy.art
    assert scared.art != happy.art
    # Eyes change between moods (the character pattern differs).
    assert "o   o" in neutral.art, neutral.art
    assert "O   O" in scared.art, scared.art
    assert "^   ^" in happy.art, happy.art
    print("  mood face renders distinct eyes")

    # 4. Mood tracker reacts to text.
    m = MoodTracker()
    assert m.current_mood() == "neutral"
    for _ in range(3):
        m.observe_user_text("ugh this is so broken wtf")
    assert m.current_mood() == "frustrated", m.scores
    print(f"  frustrated detection ok: scores={m.scores}")

    m2 = MoodTracker()
    for _ in range(3):
        m2.observe_user_text("thanks, that was perfect")
    assert m2.current_mood() == "happy", m2.scores
    print(f"  happy detection ok: scores={m2.scores}")

    # 5. Confused detection.
    m3 = MoodTracker()
    for _ in range(3):
        m3.observe_user_text("what does this even do??")
    assert m3.current_mood() == "confused", m3.scores
    print(f"  confused detection ok: scores={m3.scores}")

    # 6. Cheerful from objective signal.
    m4 = MoodTracker()
    for _ in range(2):
        m4.observe_handoff_success()
    assert m4.current_mood() == "cheerful", m4.scores
    print(f"  cheerful (handoff signal) ok: scores={m4.scores}")

    # 7. Avatar art for each mood — print samples for eyeballing.
    print("\n  -- samples --")
    for mood in ("neutral", "happy", "frustrated", "scared", "sleepy"):
        a = render("Hairy Monkey", mood)
        print(f"  {mood}:")
        for line in a.art.splitlines():
            print(f"    {line}")
        print()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
