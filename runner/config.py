"""Central configuration constants.

Per docs/ExternalRunner.md §2.2 and docs/LivingRequirements.md §6 FR4: these
constants are the ONLY place where the numeric policy lives. Any change here
must be accompanied by a matching update to the normative specifications.
"""

from __future__ import annotations

from typing import Final

RevisionGap: Final[list[int]] = [2, 5, 11, 25]
"""Revise#k due-date offsets in business days.

Revise#1 is due +2bd after the preceding Sub-task closure, #2 at +5bd, #3 at
+11bd, #4 at +25bd. Per docs/LivingRequirements.md §5 cadence policy.
"""

RevisionTarget: Final[dict[str, int]] = {"Easy": 2, "Medium": 3, "Hard": 4}
"""Unit Difficulty → number of successful Revise iterations before T4 auto-Pause."""

RevisionTargetDefault: Final[int] = 2
"""Fallback RevisionTarget when Difficulty is missing at Unit creation.

Applied by Rule 1 (docs/ExternalRunner.md §4.1) when the Difficulty field is
null, missing, or not in {Easy, Medium, Hard}. Equivalent to Easy; prevents
silent transition failures. An audit-comment note is appended to the Unit
when the fallback fires.
"""

StaleDays: Final[int] = 90
"""Rule 4 / T9 threshold in calendar days.

Units with no Sub-task activity in StaleDays are candidates for the weekly
stale scan. Per docs/LivingRequirements.md §5.2 T9.
"""
