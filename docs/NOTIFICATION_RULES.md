# Import/Export: Importance-Based Notification Rule

**Problem statement addressed:** *improve the part of the MVP most related to
import/export so that it notifies users only when an event meets clear
importance or urgency rules.*

---

## 1. The problem

Detection is cheap; notification is expensive.

A 10-minute run produces a solution every second. The anomaly detectors will
happily raise an event on a large fraction of those. Forwarding all of it
guarantees one outcome: the operator stops reading, and the one event that
mattered scrolls past unnoticed. An alerting system that cries wolf is worse
than none, because it manufactures false confidence that somebody is watching.

So the rule is **not** "notify on anomaly". It is "notify when an event clears
an explicit, written-down bar", and every decision — fired *or* suppressed —
records why.

---

## 2. Architecture

```
   light curve
        |
   features.py      33 named features: periodicity, morphology,
        |           photometry, stability
   anomaly.py       threshold rules + robust drift detection  -> Event
        |
  importance.py     scores each Event, applies the policy     -> NotificationDecision
        |
   session.py       export/import archive; import problems re-enter as Events
```

Detection answers *what changed*. Importance answers *does anyone need to know*.
They are separate modules because they have different lifetimes: what counts as
anomalous is a property of the physics; what counts as worth interrupting
someone for is a property of the operator's situation.

---

## 3. The importance model

Two independent axes, because they answer different questions.

| Axis | Question | Drives |
|---|---|---|
| **Importance** | How much does this matter? | Whether to notify at all |
| **Urgency** | How soon must someone act? | Channel routing, quiet-hour override |

Importance is a product of four factors:

```
importance = severity_weight x (0.35 + 0.65 x confidence)
                             x max(novelty, persistence)
                             x kind_weight
```

| Factor | Source | Rationale |
|---|---|---|
| `severity_weight` | detector | Super-linear (0.10 → 1.00). A clipped exposure outranks a drifting baseline no matter how confident either is. The gap from `high` to `critical` must not be crossable by confidence alone. |
| `confidence` | detector | A 4.1σ excursion should not shout as loudly as a 40σ one. Floored at 0.35 so a low-confidence *critical* is not silenced. |
| `novelty` | engine | `0.55^n`, floored at 0.15. The tenth "baseline falling" carries almost nothing the first did not. |
| `persistence` | engine | `1 - exp(-streak/6)`. A condition that will not go away is a different thing from a transient. |
| `kind_weight` | policy | Per-site tuning without touching code. |

**Novelty and persistence combine as `max`, not a product.** An event deserves
attention if it is *either* new *or* entrenched. Multiplying them would suppress
both the first occurrence (no streak yet) and the hundredth (no novelty left) —
precisely backwards. The intended tension is that novelty falls while
persistence rises: a one-off blip is suppressed by low persistence; a sustained
fault is briefly suppressed as novelty decays, then escalated as persistence
overtakes it.

---

## 4. Decision gates

Applied in this order. The order is part of the contract.

1. **Muted kind** → never fires, whatever it scores.
2. **`always_notify_severity`** (default `critical`) → fires, bypassing cooldown, quiet hours and rate limit.
3. **Severity floor** → below `min_severity`, suppressed.
4. **Importance threshold** → below it, suppressed.
5. **Cooldown** → same kind within `cooldown_s`, suppressed.
6. **Quiet hours** → suppressed unless urgency ≥ `quiet_override_urgency`.
7. **Rate limit** → hard ceiling per window, so a pathological stream cannot flood the channel whatever the scores say.

**Escalation** runs before the severity gate, so a persistent `low` can climb
past a `medium` floor rather than being permanently invisible.

---

## 5. Policies

Three presets, because the right bar genuinely differs by context.

| Preset | Threshold | Cooldown | Floor | Use |
|---|---|---|---|---|
| `bench` | 0.35 | 30 s | low | You are sitting in front of it |
| `unattended` | 0.60 | 300 s | medium | Only worth interrupting a recording for |
| `import` | 0.50 | 0 s | low | Strict on integrity, silent on physics |

Policies are JSON, round-trip through `Policy.to_json` / `Policy.from_file`, and
reject unknown keys rather than ignoring them. The policy is written into every
session manifest — a notification history you cannot reproduce is not evidence.

---

## 6. Import/export

A session archive is a directory:

```
session_<id>/
    manifest.json     schema version, provenance, policy, SHA-256 checksums
    lightcurve.csv    t, flux, background, saturated
    features.json     the 33-feature vector
    events.jsonl      every event, one JSON object per line
    decisions.jsonl   every decision, with its reasoning
```

**Import emits events rather than exceptions** wherever it can still return
usable data. A truncated file is worth flagging loudly but is often still
analysable; refusing to load it helps nobody. Only an unreadable or
schema-incompatible archive raises.

Import checks, all verified by the test suite:

| Condition | Kind | Severity |
|---|---|---|
| SHA-256 mismatch | `import_integrity_failed` | critical |
| File missing | `import_integrity_failed` | critical |
| Unparseable rows | `import_truncated` | high |
| Row count ≠ manifest | `import_truncated` | critical if >10 % missing |
| Span shorter than expected | `import_truncated` | high |
| Non-monotonic timestamps | `import_schema_invalid` | critical |
| Incompatible schema | `import_schema_invalid` | critical → **raises** |

Import problems flow through the *same* engine as live events. One bar, one
audit trail, whatever the source.

---

## 7. Measured behaviour

The property that matters is the ratio of the two error types.

| Scenario | Runs | Result |
|---|---|---|
| Healthy run, 8 seeds | 8 | **0 false alarms** |
| Spin-down injected (64 s → 80 s at t=350 s), 8 seeds | 8 | **8/8 detected** |
| Suppression on a faulted run | — | 26 events → 8 notified (69 % suppressed) |
| Test suite | — | **44/44 checks pass** |

Two false-alarm sources were found and fixed during development, both by the
tests rather than by inspection:

**Near-constant history makes everything significant.** A feature that has been
almost perfectly flat has an almost-zero MAD, so the z-score explodes on the
first bit of ordinary noise. Fixed by requiring *both* an n-MAD exceedance *and*
a minimum fractional change — the z-score asks "is this unusual?", the fraction
asks "is it big enough to care about?", and you need both.

**Glint count is a bad change detector.** `n_glints` is a small integer, so a
noise-induced flip from 1 to 2 is a 100 % change that no fractional floor can
distinguish from a real one. It remains in the feature vector as a descriptor —
it is genuinely informative — but morphology change is now tracked on
`duty_cycle`, which is continuous and bounded.

Also fixed: morphology features now return nothing when the window holds fewer
than 4 rotations. Folding 2 cycles produces a profile dominated by whatever the
noise did on those two passes. A missing feature is visible; a fabricated one is
not.

---

## 8. Usage

```bash
# analyse a recording, gate notifications, export an archive
python pipeline/analyse.py --csv data/serial/run01.csv --expect 64.0 \
       --policy bench --export data/sessions/run01

# re-analyse an archive (integrity checked on the way in)
python pipeline/analyse.py --session data/sessions/run01

# notifications only, for scripting; exit code 2 if anything high/critical fired
python pipeline/analyse.py --csv run.csv --quiet

# live, with features and notifications in the dashboard
python pipeline/live.py --source 0 --window 120 --expect 64.0 --policy bench

# tests
python pipeline/test_notify.py -v
```

---

## 9. Deliberate limits

* Notification **delivery** is console/file only. No email, Slack or webhook —
  the decision logic is the hard part and is what the problem statement asks
  for; a delivery adapter is a thin layer on `NotificationDecision`.
* `quiet_hours` uses local machine time, with no timezone handling.
* The importance score is a product of hand-set weights, not learned. With no
  labelled corpus of "events an operator actually wanted", a fitted model would
  be a guess wearing a lab coat. The weights are explicit and adjustable, which
  is the honest version.
* Cooldowns use an injected clock, so batch analysis runs on *data* time while
  live runs on wall-clock. Without this, replaying a 10-minute recording in 3
  seconds would suppress everything as "1 s elapsed".
