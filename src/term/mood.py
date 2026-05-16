"""Per-session mood tracker.

Mood is a soft, decaying score across a few emotional axes, driven by:
  - what you type at the agents (keyword sentiment)
  - objective signals from term itself (subprocess exits, handoffs)

The current mood is whatever axis is loudest above a threshold; below
threshold, mood is neutral and no emoji is shown. Scores decay over time
so the avatar doesn't get stuck looking grumpy after one bad commit.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


# Sentiment cues. Whole-word matching so "construction" doesn't trip "ruction".
_FRUSTRATED = re.compile(
    r"(?:^|\W)(ugh+|wtf|fuck|fck|fuk|dammit|damnit|shit|sucks?|hate|"
    r"broken|stupid|wtf|jesus|christ|seriously)(?:$|\W)",
    re.I,
)
_HAPPY = re.compile(
    r"(?:^|\W)(thanks|thank you|great|perfect|love it|nice|awesome|"
    r"excellent|beautiful|sweet|amazing|brilliant|yes!|lgtm)(?:$|\W)",
    re.I,
)
_CONFUSED = re.compile(
    r"(\?\?+|wtf is|what does|how do i|i don'?t (?:get|understand)|"
    r"confused|huh\?)",
    re.I,
)


# Mood label → emoji shown after the avatar.
_MOOD_EMOJI: dict[str, str] = {
    "neutral":    "",
    "happy":      "✨",
    "frustrated": "😨",
    "confused":   "🤔",
    "stressed":   "😰",
    "sleepy":     "💤",
    "cheerful":   "😊",
}


# Threshold a score has to exceed before its mood is reported.
_THRESHOLD = 3.0
_DECAY_PER_SEC = 0.4


@dataclass
class MoodTracker:
    """Per-session score across emotional axes."""
    scores: dict[str, float] = field(default_factory=lambda: {
        "happy": 0.0,
        "frustrated": 0.0,
        "confused": 0.0,
        "stressed": 0.0,
        "sleepy": 0.0,
        "cheerful": 0.0,
    })
    last_decay: float = field(default_factory=time.monotonic)
    last_input: float = field(default_factory=time.monotonic)

    # --- inputs ----------------------------------------------------------

    def observe_user_text(self, text: str) -> None:
        """Called when the user submits a line of text into a pane."""
        self.last_input = time.monotonic()
        if _FRUSTRATED.search(text):
            self.scores["frustrated"] += 5
        if _HAPPY.search(text):
            self.scores["happy"] += 5
        if _CONFUSED.search(text):
            self.scores["confused"] += 3

    def observe_pane_exit(self, code: int) -> None:
        if code != 0:
            self.scores["stressed"] += 4

    def observe_handoff_success(self) -> None:
        self.scores["cheerful"] += 4

    # --- output ----------------------------------------------------------

    def _decay(self) -> None:
        now = time.monotonic()
        dt = now - self.last_decay
        self.last_decay = now
        if dt <= 0:
            return
        drop = dt * _DECAY_PER_SEC
        for k in self.scores:
            self.scores[k] = max(0.0, self.scores[k] - drop)

        # Sleepy if no input in a while (and nothing else dominant).
        idle = now - self.last_input
        if idle > 45 and self.scores["sleepy"] < 6:
            self.scores["sleepy"] += dt * 0.15

    def current_mood(self) -> str:
        self._decay()
        label, score = max(self.scores.items(), key=lambda kv: kv[1])
        return label if score >= _THRESHOLD else "neutral"

    def mood_emoji(self) -> str:
        return _MOOD_EMOJI.get(self.current_mood(), "")
