# SPINDEX

### **Spin** **D**etermination by **INDEX** of Reflected Light

**A live guidance feed for robotic spacecraft—often called "space tugs" or servicers(are actively approaching, docking with, and modifying the orbits of dead or dying satellites) that have to catch something already
spinning.**

SPINDEX watches a target's reflected brightness and continuously solves for how
it is tumbling — rotation period, precession, cone angle, and coarse geometry —
from that light alone. No transponder. No cooperation from the target. No prior
knowledge of what it is.

---

## Why anything needs this

Robotic servicers — **space tugs** — are no longer theoretical. Astroscale's
ADRAS-J flew to a spent Japanese upper stage in 2024. Northrop Grumman's Mission
Extension Vehicles have already docked with live commercial satellites and taken
over their station-keeping. ClearSpace-1 is being built to capture and deorbit a
rocket adapter. These vehicles approach dead or dying satellites, physically
grapple them, and change their orbits.

**Every one of those missions has to solve the same problem first: the target is
tumbling, and the tug has to match it before it can touch it.**

That is a genuinely dangerous manoeuvre. Approach a tumbling derelict with the
wrong spin solution and you get one of:

- **Momentum transfer** — grapple a body whose rotation you have not matched and
  its angular momentum becomes yours. If it exceeds what your wheels and
  thrusters can dump, you now have two tumbling spacecraft instead of one.
- **Fragmentation** — a mistimed contact on a fragile, decades-old structure
  breaks it apart, creating thousands of new debris objects. That is the precise
  opposite of the mission's purpose.
- **Propellant exhaustion** — matching an unexpected tumble costs fuel that was
  budgeted for the rest of the mission.

And spin states are not static. They evolve through eddy-current damping against
Earth's magnetic field, internal energy dissipation, solar radiation pressure and
gravity-gradient torque. **A characterisation from three years ago is stale by
the time the tug arrives.**

So the servicer needs two things: a spin solution early enough to design the
capture mechanism around, and a *live, continuously updated* one during terminal
approach to confirm the target is still doing what the plan assumed.

**SPINDEX produces that feed.**

---

## How it works

A derelict emits no light of its own. It is metal and composite reflecting
sunlight, exactly as the Moon does. Because it is not spherical — solar panels, a
boxy bus, antennas — the fraction of light it directs toward an observer depends
on its instantaneous orientation. A flat solar panel turning face-on produces a
sharp specular glint; the same panel edge-on returns almost nothing.

As the body rotates, that fraction varies. Recording brightness against time
produces a **light curve** whose rhythm, harmonic content and profile shape
encode the rotation rate, the orientation of the spin axis, and coarse
information about the reflecting geometry.

SPINDEX inverts that: brightness in, spin state out, updated live, with
uncertainties on every number.

The measurement needs nothing but a camera and a light source. That is what makes
it cheap enough to run against many targets, and simple enough to run onboard an
approaching servicer rather than only from the ground.

---

## The validation problem, and the answer to it

Point this at a real derelict and it will produce a spin period. **Nobody can
tell you whether that number is right**, because the true rotation of an
uncooperative dead satellite is exactly the thing nobody knows. The claim is
unfalsifiable — which makes it worthless as evidence, however good the software
is.

So SPINDEX is validated against a target whose motion is **commanded**. The
rotation rate is set in firmware, the geometry is set by design, and the
inversion is scored against a value that was known before the measurement began.

That is the difference between demonstrating that software produces an answer and
demonstrating that the answer is *correct*. Every result below is scored against
a known truth.

---

## What actually got built

A nine-stage inversion pipeline plus a real-time monitor, developed and validated
entirely against synthetic data before any hardware existed — which is what let
the science get finished while parts were still in transit.

```
  forward model  ->  rendered frames  ->  H.264 video
                                              |
                                       ffmpeg extraction  (+ CFR audit)
                                              |
                                       aperture photometry
                                              |
                                       Lomb-Scargle period search
                                              |
                                       harmonic resolution (P vs P/2)
                                              |
                                       least squares  ->  emcee posteriors
                                              |
                                       scored against commanded truth
```

The **live path** is the one that matters operationally: camera in, spin solution
out, continuously, with nine panels of diagnostics and an importance-gated
notification system that tells an operator when the target's state has *changed*
— a spin-up, a spin-down, a shift in the tumble — rather than burying them in a
stream of routine updates.

That distinction is the whole point of a guidance feed. A servicer on terminal
approach does not need to be told the target is still doing what it was doing
five seconds ago. It needs to be told the instant it stops.

---

## Results

Every number below was measured by the code in this repository, fitted to a
light curve that went through rendering, H.264 compression, `ffmpeg` extraction
and aperture photometry. The forward model never saw any of that.

**Commanded:** spin period 1.84971 s, precession 11.40820 s, cone angle 60°.

| | Model A (symmetric) | Model B (asymmetric) |
|---|---|---|
| Spin period **P_A** | 1.84971 ± 0.00000 s (−0.000 %) | 1.84971 ± 0.00000 s (−0.000 %) |
| Precession **P_B** | 11.40822 ± 0.00017 s (+0.000 %) | 11.40816 ± 0.00015 s (−0.000 %) |
| **Cone angle** | 60.019 ± 0.056° (+0.031 %) | 59.976 ± 0.034° (−0.041 %) |
| **Panel:bus area ratio** | 0.19105 ± 0.00024 (+0.539 %) | 0.09524 ± 0.00010 (+0.237 %) |
| Photometry fidelity (Pearson *r*) | 0.999559 | 0.999702 |
| χ²_red | 3.29 | 1.55 |
| Truth inside 68 % interval | 4/6 | 4/6 |

4 of 6 is the expectation for a well-calibrated posterior — the intervals are
neither optimistic nor padded.

**Robustness:** 78/80 across noise 1–10 %, five random seeds, both target models.
The two failures are 60 s recordings at 10 % noise; every 120 s case passes,
which is the minimum recording length the design specifies earning its place.

---

## The interesting part: P versus P/2

A symmetric satellite glints **twice** per revolution, so the strongest peak in
the periodogram sits at `2/P`, not `1/P`. Report that peak and your period is
half the truth. Resolving it is mandatory, not a refinement.

The standard fix — fold the curve at `P` and at `2P`, see which is
self-consistent — is not sufficient, and finding out why was the most
interesting part of this build:

**One test is not enough.** With realistic bus proportions the dominant peak
lands at `4/P`, not `2/P`. The test has to climb repeatedly until doubling stops
paying.

**The envelope contaminates the test.** Because the spin axis is also rocking,
consecutive rotations differ in *height*. Compared bin by bin they always look
different, so a naive test says "these differ" every round and doubles the period
forever. Each round now divides out a boxcar one fold-window wide first.

**The null must be empirical.** Measured against photon errors alone, every pair
of half-cycles looks significantly different — samples in one phase bin come from
many rotations at many rock angles and scatter for a *physical* reason too. The
test uses the empirical standard error, floored at the photon error.

The result: Model A correctly reports that it can only constrain `P_A/2` — a
symmetric target is genuinely degenerate, and saying so is the right answer.
Model B's asymmetry breaks the degeneracy and the walk recovers the full period
from photometry alone.

---

## Import/Export: Importance-Based Notification Rule

Detection is cheap; notification is expensive. A 10-minute run produces a
solution every second, and forwarding every anomaly guarantees the operator stops
reading — so the one event that mattered scrolls past unnoticed.

The rule is therefore **not** "notify on anomaly" but *"notify when an event
clears an explicit, written-down bar"*, and every decision records why.

```
importance = severity x confidence x max(novelty, persistence) x kind_weight
```

Novelty and persistence combine as `max`, not a product: an event matters if it
is *either* new *or* entrenched. Multiplying would suppress both the first
occurrence (no streak yet) and the hundredth (no novelty left) — precisely
backwards.

Seven ordered gates: mute → always-notify → severity floor → threshold →
cooldown → quiet hours → rate limit. Urgency is a separate axis driving channel
routing and quiet-hour overrides.

**Session archives** carry SHA-256 checksums, schema version, provenance and the
policy that produced their decisions. Import verifies all of it and raises events
for tampering, truncation, non-monotonic timestamps and schema drift — gated
through the same engine as live events. One bar, one audit trail.

| Scenario | Result |
|---|---|
| Healthy runs, 8 seeds | **0 false alarms** |
| Injected spin-down, 8 seeds | **8/8 detected** |
| Faulted run | 26 events → 8 notified (69 % suppressed) |
| Test suite | **44/44 pass** |

Full design note: [`docs/NOTIFICATION_RULES.md`](docs/NOTIFICATION_RULES.md)

---

## Live monitor — the guidance feed

```bash
python pipeline/live.py --source 0 --window 120
```

This is the part a servicer would actually consume: a spin solution that updates
continuously as the target is observed, rather than a report written after the
fact.

Nine panels updating in real time: camera with aperture overlay, scrolling light
curve, big period readout, periodogram with harmonic markers, phase fold, period
history, pixel-level histogram, live feature table, importance-gated notification
feed, and a scrolling activity log.

Every solution carries a false-alarm probability, and the readout **greys itself
out when the period is not statistically significant**. A guidance feed that
cannot say "I don't know yet" is more dangerous than no feed at all — a servicer
acting on a confident-looking wrong number is exactly the failure this is meant
to prevent.

**The time base is different from the recorded pipeline, deliberately.** A locked
60 fps recording is uniform by construction, so frame index *is* the clock. A
webcam is not: it drops frames and USB scheduling jitters arrival times. Every
live sample is stamped with `time.perf_counter()` at grab time and Lomb-Scargle
is fed real timestamps — which is exactly why Lomb-Scargle is the right tool and
an FFT is not.

Works with a webcam, an OBS Virtual Camera (phone via USB — see
[`docs/OBS_SETUP.md`](docs/OBS_SETUP.md)), a recorded file, or a fully synthetic
source needing no camera at all.

---

## Quick start

```bash
pip install -r requirements.txt

# full chain, synthetic -> video -> photometry -> period, scored against truth
python pipeline/run_pipeline.py --model B

# stages 7-9: least squares + MCMC posteriors against the measured curve
python pipeline/validate.py --model B --from-photometry

# live monitor
python pipeline/live.py --list-devices
python pipeline/live.py --source 0 --window 120

# features + anomalies + importance-gated notifications + archive
python pipeline/analyse.py --csv data/serial/run01.csv --expect 1.8 \
       --export data/sessions/run01

# tests
python pipeline/test_notify.py      # 44 checks
python pipeline/test_recovery.py    # 80-case robustness sweep
```

No hardware needed for any of the above.

---

## Repository layout

| Path | Contents |
|---|---|
| `pipeline/` | The SPINDEX inversion chain — 20 modules, [detailed map](pipeline/README.md) |
| `firmware/` | Arduino sketches (Uno + CNC Shield V3, TMC2209/A4988) |
| `docs/` | Build specification, OBS setup guide, notification design note |
| `results/` | Reports and scored comparisons from actual runs |
| `data/` | Generated frames, videos, archives (git-ignored — rebuildable) |

**Key modules:** `forward_model.py` (box-wing reflection), `photometry.py`
(aperture photometry), `period.py` (Lomb-Scargle + harmonic walk),
`inversion.py` (least squares + emcee), `features.py` (33 features),
`importance.py` (notification rules), `session.py` (checksummed archives),
`live.py` + `dashboard.py` (real-time monitor).

---

## What this does not do

Stating limits plainly is part of the specification. An instrument whose limits
are declared is more credible than one whose limits are discovered.

- **No real orbital targets.** Atmospheric seeing, target faintness and tracking
  are outside this build.
- **No absolute radiometry.** Camera response is non-linear and only
  approximately linearised; recovered areas are *relative*, not calibrated.
- **Not a general shape solver.** The forward model is a parameterised box-wing,
  appropriate for artificial satellites, not full convex inversion.
- **Panel area is systematics-limited, not noise-limited.** Its formal interval
  is ~0.1 % but it sits 0.24–0.54 % high, at 2.2σ and 4.3σ. Periods and cone
  angle come back essentially exact; the amplitude-derived quantity does not.
  Quote it with a systematic term.
- **Hardware bring-up is incomplete.** The mechanism is assembled and the
  firmware is written and compiles, but the controller failed during bring-up and
  a replacement is on order. The optical ground-truth sensor is not fitted, so
  the commanded step rate — not an independent channel — is currently the
  reference. It cannot detect a stalled motor.

---

## Things the tests caught that reading the code would not

**A wrong photometry gain made χ² meaningless, and the error rescaling hid it.**
The intensity-to-photon gain was a hardcoded guess, wrong by 113×. The recovered
*parameters* were unaffected, but the error bars were nonsense and χ²_red came
out at 174.9. Because the fitter rescales errors by √χ²_red, the posteriors still
looked plausible and coverage still looked right — the symptom was entirely
absorbed. Fixed: 174.9 → 1.55.

**Exposure scaling silently destroyed the photometry.** The target initially sat
at 0.2 % of full well, so the measurement was mostly 8-bit rounding — a 15.7 %
residual with no other symptom. Fixed by setting the exposure so the peak pixel
lands at 88 % of full scale. Residual fell to 1.2 %.

**Gamma, measured rather than asserted.** Disabling response linearisation drops
Pearson *r* from 0.9997 to 0.9574 while Spearman only moves 0.9980 → 0.9708, and
the period still lands at 0.0095 %. Rank preserved, linearity destroyed — the
signature of a monotonic distortion, and confirmation that period survives it
while amplitude does not.

**A near-constant feature history makes everything look significant.** An almost
flat feature has an almost-zero MAD, so the z-score explodes on ordinary noise.
Fixed by requiring both an n-MAD exceedance *and* a minimum fractional change.

---

## Name

**SPINDEX** — **Spin** **D**etermination by **INDEX** of Reflected Light.

The "index" is the light curve itself: a one-dimensional trace of brightness
against time that indexes the full three-dimensional attitude history of a body
nobody can reach, touch, or ask.

---

*Built against the SDC-001 Rev B specification, included in `docs/`. Section
references throughout the code point to that document.*
