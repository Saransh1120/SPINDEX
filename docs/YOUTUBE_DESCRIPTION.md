# YouTube — title, description, chapters

## Title options

**Recommended:**
> SPINDEX — Reading a Dead Satellite's Motion from Reflected Light

Alternatives:
> SPINDEX — Determining Spacecraft Tumble from a Light Curve | Zeit v1.0
> How to Tell a Dead Satellite's Motion Using Only a Camera — SPINDEX

---

## Description

Copy everything between the lines into the YouTube description box.

---

Space tugs are already flying. Before one can grab a dead satellite, it has to
know exactly how that satellite is tumbling — and right now, nobody is supplying
that data.

SPINDEX solves it using nothing but the light the target reflects.

A dead satellite makes no light of its own. It reflects sunlight, exactly like
the Moon — and because it isn't a sphere, the amount of light it sends back
changes as it rotates. A solar panel edge-on nearly disappears; face-on, it
flashes. Record that brightness over time and the resulting "light curve"
contains the entire motion. SPINDEX reads it back out.

No transponder. No cooperation from the target. Just a camera.

WHAT'S DEMONSTRATED

A two-axis testbed spins a model satellite at a rate set in firmware, so the
true answer is known before the measurement is taken. The software sees only
reflected light through a real camera and real video compression.

Commanded rotation period:   1.84971 s
Recovered rotation period:   1.84971 s

Commanded precession period: 11.40820 s
Recovered precession period: 11.40816 s

Commanded cone angle:        60.000 degrees
Recovered cone angle:        59.976 degrees

Every parameter within half a percent. Truth falls inside the 68% credible
interval for 4 of 6 parameters — the statistical expectation, so the
uncertainties are calibrated rather than decorative.

HOW IT WORKS

- Aperture photometry on every frame — fixed circular aperture, annular
  background subtraction
- Generalised Lomb-Scargle periodogram, implemented from Zechmeister & Kuerster
  (2009). Chosen over an FFT because real cameras drop frames and jitter; every
  frame is timestamped and Lomb-Scargle handles uneven sampling natively
- Harmonic disambiguation by phase folding. A symmetric satellite flashes twice
  per rotation, so the strongest peak sits at half the true period — and when a
  target is genuinely degenerate, the system reports that instead of guessing
- Least-squares fitting plus MCMC (emcee) for real uncertainties on every number
- Live monitor: 33 features updating continuously, with importance-gated
  alerting that fires only when the target's motion actually changes

Built in Python with NumPy, SciPy, OpenCV, emcee and Matplotlib. Validated
across 80 runs at noise levels from 1% to 10% (78 passed), plus 44 unit tests.

CHAPTERS

0:00  The problem — orbital debris and dead satellites
0:40  ADRAS-J: servicers moved from theory to engineering
1:15  The gap — it's a data problem, not a hardware problem
1:50  The core idea: reading reflected light
3:00  The hardware — a testbed with a known answer
3:35  The software pipeline
4:20  Demonstration and results
4:55  Close

PROJECT

Source code and full technical documentation:
https://github.com/Sarthakk1407/SPINDEX

TEAM

Zeit v1.0
Sarthak Tyagi — Team Lead

Contact: teamzeit.office@gmail.com

SPINDEX — Spin Determination by INDEX of Reflected Light

#SpaceDebris #Aerospace #OrbitalDebris #SatelliteServicing #SignalProcessing
#Python #ComputerVision #SpaceTech #ADR #Astrodynamics

---

## Notes

**Chapters need 0:00 to work.** YouTube only generates a chapter list if the
first timestamp is `0:00` and there are at least three of them. Adjust the times
to match your final edit — if they drift, the chapters silently stop working.

**The first two lines are what people see** before clicking "show more". They are
written to work standalone, so keep them at the top.

**Contact address.** I used `teamzeit.office@gmail.com` rather than your personal
address. Swap or remove it as you prefer — but do not put a personal email in a
public description.

**Unlisted still means shareable.** Anyone with the link can view and forward it.
Treat the description as public: no personal phone numbers, no addresses, and
check the video itself for visible faces, badges or laptop screens with private
content.
