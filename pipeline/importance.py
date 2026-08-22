"""Importance-based notification rules for the import/export subsystem.

THE PROBLEM
-----------
Detection is cheap and notification is expensive. A 10-minute run produces a
solution every second, and the anomaly detectors will happily emit an event on a
large fraction of them. Forwarding all of that to an operator guarantees exactly
one outcome: they stop reading it, and the one event that mattered scrolls past
unnoticed. An alerting system that cries wolf is worse than no alerting system,
because it manufactures false confidence that someone is watching.

So the rule is not "notify on anomaly". It is "notify when an event clears an
explicit, written-down bar for importance or urgency", and every decision --
fired or suppressed -- records why.

THE MODEL
---------
Two independent axes, because they answer different questions:

  IMPORTANCE  How much does this matter?   Drives whether to notify at all.
  URGENCY     How soon must someone act?   Drives which channel, and whether
                                           quiet hours are honoured.

Importance is a product of four factors, each in [0, 1] except the first:

  severity     From the detector. A clipped exposure outranks a drifting
               baseline no matter how confident either is.
  confidence   From the detector. A 4.1-sigma excursion should not page anyone
               as loudly as a 40-sigma one.
  novelty      First occurrence of a kind scores 1.0 and decays on repeats.
               The tenth "baseline falling" in a row carries almost no
               information the first did not.
  persistence  Rises with consecutive occurrences. This deliberately opposes
               novelty: a condition that will not go away is a different thing
               from a transient, and eventually earns attention even if each
               instance is individually unremarkable.

Novelty falling while persistence rises is the intended tension. A one-off blip
is suppressed by low persistence; a sustained fault is suppressed briefly by
falling novelty and then escalated as persistence overtakes it. Neither alone
produces sensible behaviour.

GUARANTEES
----------
1. Every decision is auditable: NotificationDecision records the score, the
   factors, the threshold, and a human-readable reason.
2. No event is silently dropped. Suppressed events are still recorded in the
   session archive; suppression affects notification only.
3. Deterministic: same event stream and same policy produce the same decisions.
   There is no randomness and no wall-clock dependence except cooldowns, which
   take an explicit clock for testability.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Callable

from anomaly import Event, SEVERITY_ORDER, SEVERITY_RANK

POLICY_VERSION = "1.0.0"

# Importance weight per severity. Deliberately super-linear: the gap between
# "high" and "critical" should not be crossable by confidence alone.
SEVERITY_WEIGHT = {
    "info": 0.10,
    "low": 0.25,
    "medium": 0.50,
    "high": 0.80,
    "critical": 1.00,
}

# How soon a human must act. Used for channel routing and quiet-hour override.
URGENCY_BY_KIND = {
    "saturation": "immediate",       # every frame recorded now is being ruined
    "no_modulation": "immediate",    # the target is effectively not being seen
    "frame_dropouts": "soon",
    "cadence_change": "soon",
    "period_change": "soon",         # the science result just changed
    "morphology_change": "soon",
    "profile_shape_change": "whenever",
    "rate_change": "soon",
    "insignificant_period": "whenever",
    "no_periodicity": "whenever",
    "low_snr": "whenever",
    "amplitude_change": "whenever",
    "flux_level_change": "whenever",
    "background_change": "whenever",
    "baseline_rising": "whenever",
    "baseline_falling": "whenever",
    "broad_peak": "whenever",
    "saturation_trace": "whenever",
    "import_schema_invalid": "immediate",
    "import_integrity_failed": "immediate",
    "import_truncated": "soon",
    "export_written": "whenever",
}

URGENCY_RANK = {"whenever": 0, "soon": 1, "immediate": 2}


@dataclass
class Policy:
    """The written-down bar. Everything the engine does is set here.

    Serialise with to_json / load with from_file so a run can record exactly
    which policy produced its notifications -- a decision you cannot reconstruct
    is not auditable.
    """

    version: str = POLICY_VERSION
    name: str = "default"

    # Fire only at or above this importance. The single most consequential dial.
    threshold: float = 0.45

    # Regardless of score, never notify below this severity...
    min_severity: str = "low"
    # ...and always notify at or above this one, bypassing cooldown and quiet
    # hours. Reserved for conditions that ruin data while you wait.
    always_notify_severity: str = "critical"

    # Seconds before the same kind may notify again.
    cooldown_s: float = 60.0
    # Consecutive occurrences before a repeating condition escalates one level.
    escalate_after: int = 5
    # Hard ceiling on notifications per window, so a pathological stream cannot
    # flood the channel no matter what the scores say.
    rate_limit: int = 12
    rate_window_s: float = 300.0

    # Novelty decays geometrically with each repeat of the same kind.
    novelty_decay: float = 0.55
    novelty_floor: float = 0.15
    # Persistence climbs towards 1.0 over this many consecutive occurrences.
    persistence_scale: float = 6.0

    # Per-kind multipliers, for site-specific tuning without touching code.
    kind_weights: dict = field(default_factory=dict)
    # Kinds never notified on, whatever they score.
    muted_kinds: list = field(default_factory=list)

    # Urgency at or above which quiet hours are ignored.
    quiet_hours: tuple | None = None          # e.g. (22, 7) local hours
    quiet_override_urgency: str = "immediate"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quiet_hours"] = list(self.quiet_hours) if self.quiet_hours else None
        return d

    def to_json(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p

    @classmethod
    def from_file(cls, path) -> "Policy":
        d = json.loads(Path(path).read_text())
        qh = d.get("quiet_hours")
        d["quiet_hours"] = tuple(qh) if qh else None
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(
                "unknown policy keys: {}. Known keys: {}".format(
                    sorted(unknown), sorted(known)))
        return cls(**{k: v for k, v in d.items() if k in known})


# Two ready-made policies, because the right bar genuinely differs by context.
PRESETS = {
    # Bench work: you are sitting in front of it and can act immediately.
    "bench": Policy(name="bench", threshold=0.35, cooldown_s=30.0,
                    rate_limit=20, min_severity="low"),
    # Unattended run: only things worth interrupting a recording for.
    "unattended": Policy(name="unattended", threshold=0.60, cooldown_s=300.0,
                         rate_limit=6, min_severity="medium",
                         escalate_after=3),
    # Archival import: strictness about data integrity, silence about physics.
    "import": Policy(name="import", threshold=0.50, cooldown_s=0.0,
                     rate_limit=50, min_severity="low",
                     muted_kinds=["baseline_rising", "baseline_falling",
                                  "broad_peak", "amplitude_change"]),
}


@dataclass
class NotificationDecision:
    """Why an event did or did not become a notification."""

    event: Event
    fired: bool
    importance: float
    urgency: str
    reason: str
    factors: dict = field(default_factory=dict)
    escalated_to: str = ""

    def to_dict(self) -> dict:
        return {
            "fired": self.fired,
            "importance": round(self.importance, 4),
            "urgency": self.urgency,
            "reason": self.reason,
            "factors": {k: round(v, 4) if isinstance(v, float) else v
                        for k, v in self.factors.items()},
            "escalated_to": self.escalated_to,
            "event": self.event.to_dict(),
        }

    def one_line(self) -> str:
        mark = "FIRED " if self.fired else "muted "
        return "{} imp={:.2f} {:<9} {:<22} {}".format(
            mark, self.importance, self.urgency, self.event.kind,
            self.event.message)


class NotificationEngine:
    """Applies a Policy to a stream of Events and decides what to surface.

    The clock is injected rather than read from time.time() directly, so the
    cooldown and rate-limit behaviour can be tested deterministically instead of
    with sleeps.
    """

    def __init__(self, policy: Policy | None = None,
                 clock: Callable[[], float] = time.time):
        self.policy = policy or Policy()
        self.clock = clock
        self._last_fired: dict[str, float] = {}
        self._streak: dict[str, int] = {}
        self._seen: dict[str, int] = {}
        self._recent_fires: list[float] = []
        self.decisions: list[NotificationDecision] = []

    # -------------------------------------------------------------- scoring

    def _novelty(self, kind: str) -> float:
        n = self._seen.get(kind, 0)
        return max(self.policy.novelty_floor, self.policy.novelty_decay ** n)

    def _persistence(self, kind: str) -> float:
        streak = self._streak.get(kind, 0)
        return 1.0 - math.exp(-streak / max(self.policy.persistence_scale, 1e-9))

    def score(self, event: Event) -> tuple[float, dict]:
        """Importance in [0, 1+], with the factors that produced it."""
        sev_w = SEVERITY_WEIGHT.get(event.severity, 0.25)
        conf = float(min(max(event.confidence, 0.0), 1.0))
        nov = self._novelty(event.kind)
        pers = self._persistence(event.kind)
        kind_w = float(self.policy.kind_weights.get(event.kind, 1.0))

        # Novelty and persistence combine as a max, not a product: an event is
        # worth surfacing if it is EITHER new OR entrenched. Multiplying them
        # would suppress both the first occurrence (no streak yet) and the
        # hundredth (no novelty left), which is precisely backwards.
        attention = max(nov, pers)
        importance = sev_w * (0.35 + 0.65 * conf) * attention * kind_w

        return importance, {
            "severity_weight": sev_w, "confidence": conf, "novelty": nov,
            "persistence": pers, "kind_weight": kind_w, "attention": attention,
        }

    # ------------------------------------------------------------- decision

    def _in_quiet_hours(self, urgency: str) -> bool:
        qh = self.policy.quiet_hours
        if not qh:
            return False
        if URGENCY_RANK.get(urgency, 0) >= URGENCY_RANK.get(
                self.policy.quiet_override_urgency, 2):
            return False
        hour = time.localtime(self.clock()).tm_hour
        start, end = qh
        return (start <= hour or hour < end) if start > end else (start <= hour < end)

    def _rate_limited(self, now: float) -> bool:
        w = self.policy.rate_window_s
        self._recent_fires = [t for t in self._recent_fires if now - t <= w]
        return len(self._recent_fires) >= self.policy.rate_limit

    def submit(self, event: Event) -> NotificationDecision:
        """Evaluate one event. Always returns a decision; never raises."""
        pol = self.policy
        now = self.clock()
        kind = event.kind
        urgency = URGENCY_BY_KIND.get(kind, "whenever")

        importance, factors = self.score(event)

        # Escalate a condition that will not clear. Done before the severity
        # gate so a persistent "low" can eventually cross a "medium" floor.
        escalated_to = ""
        streak = self._streak.get(kind, 0)
        if streak and streak % max(pol.escalate_after, 1) == 0:
            i = min(SEVERITY_RANK.get(event.severity, 0) + 1,
                    len(SEVERITY_ORDER) - 1)
            if SEVERITY_ORDER[i] != event.severity:
                escalated_to = SEVERITY_ORDER[i]
                event = replace(event, severity=escalated_to)
                importance, factors = self.score(event)

        def finish(fired, reason):
            d = NotificationDecision(event, fired, importance, urgency, reason,
                                     factors, escalated_to)
            self.decisions.append(d)
            self._seen[kind] = self._seen.get(kind, 0) + 1
            self._streak[kind] = self._streak.get(kind, 0) + 1
            if fired:
                self._last_fired[kind] = now
                self._recent_fires.append(now)
            return d

        # --- gates, in order. Order is part of the contract: an always-notify
        # severity must outrank cooldown, and muting must outrank everything.
        if kind in pol.muted_kinds:
            return finish(False, "kind is muted by policy")

        always = SEVERITY_RANK.get(event.severity, 0) >= SEVERITY_RANK.get(
            pol.always_notify_severity, 99)
        if always:
            return finish(True, "severity {} is always notified".format(event.severity))

        if SEVERITY_RANK.get(event.severity, 0) < SEVERITY_RANK.get(pol.min_severity, 0):
            return finish(False, "severity {} below policy floor {}".format(
                event.severity, pol.min_severity))

        if importance < pol.threshold:
            return finish(False, "importance {:.3f} below threshold {:.2f}".format(
                importance, pol.threshold))

        last = self._last_fired.get(kind)
        if last is not None and (now - last) < pol.cooldown_s:
            return finish(False, "within {:.0f}s cooldown for {} ({:.0f}s elapsed)"
                          .format(pol.cooldown_s, kind, now - last))

        if self._in_quiet_hours(urgency):
            return finish(False, "quiet hours and urgency is only '{}'".format(urgency))

        if self._rate_limited(now):
            return finish(False, "rate limit {}/{:.0f}s reached".format(
                pol.rate_limit, pol.rate_window_s))

        return finish(True, "importance {:.3f} >= {:.2f}, urgency {}".format(
            importance, pol.threshold, urgency))

    def submit_all(self, events) -> list[NotificationDecision]:
        return [self.submit(e) for e in events]

    def clear_streak(self, kind: str):
        """Call when a condition is observed to have cleared."""
        self._streak.pop(kind, None)

    def observe_absent(self, present_kinds):
        """Reset streaks for kinds not in this batch, so persistence means
        *consecutive* occurrences rather than a lifetime total."""
        present = set(present_kinds)
        for k in list(self._streak):
            if k not in present:
                self._streak[k] = 0

    @property
    def fired(self):
        return [d for d in self.decisions if d.fired]

    def stats(self) -> dict:
        total = len(self.decisions)
        fired = len(self.fired)
        return {"events": total, "notified": fired,
                "suppressed": total - fired,
                "suppression_rate": round(1 - fired / total, 4) if total else 0.0,
                "by_kind": {k: self._seen[k] for k in sorted(self._seen)}}
