# SDC-001 analysis pipeline

Stages 1–9, built and validated against synthetic data. No hardware required.

```bash
python pipeline/run_pipeline.py --model B            # stages 1-6 through a real .mov
python pipeline/run_pipeline.py --model B --reuse-frames   # re-measure, no re-render
python pipeline/validate.py --model B --from-photometry    # stages 7-9, honest test
python pipeline/validate.py --model B                      # stages 7-9, synthetic
python pipeline/test_recovery.py                     # period-recovery sweep, 80 cases
python pipeline/serial_log.py --port COM5 --duration 120 --expect 1.84971
```

Install everything with `pip install -r requirements.txt`. Core: `numpy`,
`scipy`, `pillow`, `matplotlib`, `emcee`, plus an ffmpeg binary (`imageio-ffmpeg`
ships one if your system has none). `opencv-python` is required for the live
monitor. Optional: `corner` for posterior corner plots, `pyserial` for Arduino
capture. `photutils` and `astropy` are named in the spec but are not needed — see
"Substitutions".

## Module map

| Stage | Module | What it does |
|---|---|---|
| 1 forward model | `forward_model.py` | Box-wing, Lambertian + Blinn-Phong specular, spin + triangular rock, 60 Hz |
| 1b render | `render_frames.py` | Light curve → PNG frames (photon noise, gamma, stray light) → CFR `.mov` |
| 2 extraction | `frames.py` | `ffmpeg -vsync 0`, fused with the constant-frame-rate audit |
| 3 photometry | `photometry.py` | Fixed circular aperture + sigma-clipped background annulus |
| 4 detrend | `run_pipeline.py` | Response linearisation, unit mean, linear drift removal |
| 5 period search | `period.py` | Generalised Lomb–Scargle + PDM as a shape-free cross-check |
| 6 harmonic | `period.py` | Sub-harmonic fold walk |
| 7 least squares | `inversion.py` | `scipy.optimize.least_squares` point estimate |
| 8 posteriors | `inversion.py` | `emcee`, medians and 16th/84th percentiles |
| 9 validation | `validate.py` | Truth table, residual plot, corner plot |
| features | `features.py` | 33 named features: periodicity, morphology, photometry, stability |
| anomaly | `anomaly.py` | Threshold rules + robust drift detection over the feature stream |
| notification | `importance.py` | Importance/urgency scoring, policy gates, audit trail |
| import/export | `session.py` | Checksummed session archives; integrity checks on import |
| batch analysis | `analyse.py` | Features + anomalies + notifications + archive, one CLI |
| live | `live.py` | Webcam / OBS / file capture, real-time photometry, rolling Lomb-Scargle |
| live UI | `dashboard.py` | Seven-panel live display |
| — | `serial_log.py` | Arduino slot-sensor capture → CSV → mean period ± sd |
| — | `config.py` | Commanded truth, derived from firmware step rates not hard-coded |
| — | `test_recovery.py` | Sweep over model, duration, noise, seed |
| — | `test_notify.py` | 44 checks over features, detectors, policy, archives |

## Results

Commanded: `P_A = 1.84971 s`, `P_B = 11.40820 s`, cone 60°, panel:bus area ratio
0.19002 (Model A) / 0.09501 (Model B). 120 s at 60 fps, 160×120 px, gamma 2.2,
through H.264 in a QuickTime container.

### Stages 1–6: period recovery

| | Model A | Model B |
|---|---|---|
| Stage 3 Pearson *r* vs input curve | 0.999559 | 0.999702 |
| Stage 3 residual | 1.88 % | 1.32 % |
| CFR audit | PASS | PASS |
| Repeat period found | 0.92484 s | 1.84969 s |
| Symmetry order detected | 2 | 1 |
| Recovered P_A | 1.84968 ± 0.00011 s (0.0015 %) | 1.84969 ± 0.00011 s (0.0010 %) |
| Recovered P_B | 11.40832 ± 0.00970 s (0.0010 %) | 11.41529 ± 0.01144 s (0.0621 %) |
| Harmonic resolved from photometry alone | No — genuinely degenerate | Yes |

### Stages 7–8: full inversion against the *measured* curve

Fitted to the light curve measured off the rendered video, not to the forward
model's own output.

| | Model A | Model B |
|---|---|---|
| chi2_red (least squares) | 3.287 | 1.547 |
| error rescale applied | 1.81 | 1.24 |
| **P_A** | 1.84971 ± 0.00000 s (−0.000 %) | 1.84971 ± 0.00000 s (−0.000 %) |
| **P_B** | 11.40822 ± 0.00017 s (+0.000 %) | 11.40816 ± 0.00015 s (−0.000 %) |
| **cone angle** | 60.019 ± 0.056 ° (+0.031 %) | 59.976 ± 0.034 ° (−0.041 %) |
| **panel:bus area ratio** | 0.19105 ± 0.00024 (+0.539 %) | 0.09524 ± 0.00010 (+0.237 %) |
| truth inside 68 % interval | 4/6 | 4/6 |

4 of 6 is the expectation for a well-calibrated posterior, so the intervals are
neither optimistic nor padded.

**The area ratio is systematics-limited, not noise-limited.** Its formal
interval is ~0.1 % but it sits 0.24–0.54 % high, at 2.2σ and 4.3σ. Periods and
cone angle are recovered essentially exactly; the amplitude-derived quantity is
not, which is what Rev B §3.2 predicts when it grades relative panel area as
recoverable only "approximately". Quote it with a systematic term, not the
formal error bar.

`test_recovery.py` passes 78/80 on the light-curve level across noise 1–10 %,
five seeds and both models. Both failures are 60 s recordings at 10 % noise;
every 120 s case passes — the §5.2 minimum recording length earning its place.

## Things found by testing that would not have been found by reading

**A wrong photometry gain made chi2 meaningless, and the error rescaling hid
it.** The gain converting intensity to photons was a hardcoded guess, wrong by
113×. Recovered *parameters* were unaffected, but the error bars were nonsense
and chi2_red came out at 174.9. Because `fit_least_squares` rescales errors by
sqrt(chi2_red), the posteriors still looked plausible and the coverage still
looked right — the symptom was entirely absorbed. `render_frames.py` now writes
`render_meta.json` with the true photon scale and `run_pipeline.py` reads it:
chi2_red 174.9 → 1.55, rescale 13.2 → 1.24. **Look at the raw chi2_red before
trusting an interval.** A factor near 1.2 is model error; a factor of 13 means
the errors themselves are wrong.

**Exposure scaling was silently destroying the photometry.** The target
initially sat at 0.2 % of full well, so the measurement was mostly 8-bit
rounding — 15.7 % residual with no other symptom. `render_sequence` now sets
`full_well` so the peak pixel lands at 88 % of full scale, which is §8.4's
"longest exposure that does not clip the glint peak" performed rather than
trusted. Residual fell to 1.2 %.

**Stage 3 is shot-noise limited.** Raising `--peak-photons` tenfold drops the
residual from 1.24 % to 0.44 %, a factor of 3.2 against √10 = 3.16. Disabling
drift or stray light moves it by under 0.15 points.

**Gamma, measured rather than asserted.** Rev B §8.4 claims period survives the
camera's non-linearity but amplitude does not. With `--no-linearise`, Pearson
*r* falls 0.9997 → 0.9574 while Spearman only moves 0.9980 → 0.9708, and P_A
still lands at 0.0095 %. Rank preserved, linearity destroyed — exactly the
signature of a monotonic distortion.

**A dropped slot-sensor edge biases the slope fit but not the mean.** The slope
fit counts edges, so a missed one reads a revolution as two: 1.8937 s against a
true 1.8497 s. The screened mean stays correct. `serial_log.py` detects the
rejection and tells you which number to trust.

## Design decisions worth knowing

**Scale and offset are profiled out, not sampled.** They enter the model
linearly, so they are solved exactly by a 2×2 least-squares at every likelihood
call. That drops the sampled space from 8 dimensions to 6 and removes the two
most degenerate directions from the posterior. Absolute panel area is degenerate
with lamp brightness and distance (§3.2), which is why the fitted quantity is a
ratio.

**Period bounds in the fit are deliberately tight.** Over 120 s the spin phase
advances through 65 cycles, so chi2 in `p_spin` is a comb of narrow minima one
cycle-slip apart. Opening the bounds does not find a better optimum, it finds a
different alias. The period search has already located the right tooth to ~1e-5;
the fit polishes it.

**The aperture is fixed, not recentred per frame.** A recentred aperture tracks
the brightness centroid, which moves toward whichever facet is glinting — it
would follow the signal and inject a spurious modulation at exactly the spin
frequency. `--recentre` exists for drift diagnosis and warns when used.

**Extraction and the CFR audit are one operation.** The whole time base is the
assumption that frames are evenly spaced — there is no GPS in this build because
the frame rate *is* the clock (§4.2). A variable-frame-rate recording makes every
period wrong with no other symptom, so `extract_frames` refuses to return frames
it has not checked against `duration × fps`.

**Harmonic resolution is a walk, not a single test.** The strongest peak is not
always at `2/P_A` — with these bus proportions it sits at `4/P_A` — so the test
must climb until doubling stops paying. And the precession envelope makes every
cycle a different height, so a naive comparison says "different" every round and
doubles forever; each round divides out a boxcar one fold-window wide first. The
stopping criterion uses the empirical standard error floored at the photon
error, corrected for sample correlation, and refuses to exceed baseline/4.

**Fitting synthetic data with the model that generated it is an inverse crime.**
`validate.py` says so in its own output and defaults you toward
`--from-photometry`.

## Substitutions from the spec

| Spec names | Used here | Why |
|---|---|---|
| `astropy` LombScargle | local NumPy GLS | Thirty lines of arithmetic; `gls(prefer_astropy=True)` cross-checks if installed |
| `photutils` | NumPy + Pillow | A circular sum and a clipped annulus median; `Aperture` mirrors the photutils API |
| OpenCV (offline photometry) | NumPy + Pillow | Not needed to read PNGs. OpenCV **is** used, and required, for live capture in `live.py` |
| `ffprobe` for duration | ffmpeg stderr parse | imageio-ffmpeg ships no ffprobe; the CFR audit is too important to skip |

## Three findings that affect the hardware

**1. Axis B sweep amplitude.** Appendix A.3's "±60° → 3,200 microsteps" is
*total* peak-to-peak travel, so the amplitude from centre is **1,600
microsteps**. ±3,200 rocks the cradle ±120°, twice the travel §5.1 relies on to
use a flexing service loop instead of a slip ring, and makes the triangle period
22.8 s rather than the 11.4 s of §5.2. Check: 4 × 1600 / 561 = 11.41 s = `P_B`.
The firmware compiles to 1600 and accepts up to 3200 at runtime via `S3200`,
warning when you exceed ±60°. The "842 steps/s" in the same table is an
arithmetic slip — its own derivation gives **561**, which is what the firmware
uses.

**2. A symmetric rock makes `P_B` degenerate**, which the spec does not mention.
Rock angles `+ε` and `−ε` present a near-identical aspect, so for a closed convex
body the envelope repeats every `P_B/2`. Model A shows it. Fix without new parts:
offset the Axis B zero so the rock runs −20° to +100° instead of ±60°, or tilt
the camera out of the rock's symmetry plane. The firmware `Z` command sets the
sweep centre, so this is a bench decision.

**3. The bus cross-section must not be square.** A square bus is 90°-symmetric
about the spin axis, putting dominant power at `4/P_A` and turning a two-way
harmonic ambiguity into a four-way one.

## Disk use and speed

A 120 s run writes ~7200 rendered PNGs, a ~41 MB `.mov` and ~7200 extracted
PNGs — roughly 300 MB per model under `data/`. Nothing downstream needs them
once `results/*_photometry.csv` exists.

`data/` currently sits inside OneDrive, which queues every one of those files for
cloud sync. The same 120 s run took 188 s on a clean tree and 3936 s once ~29,000
PNGs had accumulated — a 20× penalty. Move `data/` outside OneDrive or exclude it
from sync before running against real data. `--reuse-frames` avoids re-rendering
entirely when only the photometry changes: 14 s instead of an hour.

## Not yet built

Nothing from the requested scope. The remaining gaps are hardware-facing:
`serial_log.py`'s live capture path has been tested only on replayed and
synthetic pulse trains, since no board is connected, and the Arduino sketch has
not been compiled — there is no toolchain on this machine.

## Live monitor (`live.py`)

A bench aid that reads a camera, measures the target every frame, scrolls the
light curve and keeps a Lomb-Scargle period on screen. The recorded pipeline
above is unchanged and remains the instrument of record; this tells you within
seconds whether the target is framed, exposed and turning at roughly the right
rate — before you commit to a 120 s recording.

```bash
python pipeline/live.py                                     # webcam 0
python pipeline/live.py --source data/raw/synthetic_model_B.mov   # recorded fallback
python pipeline/live.py --source synthetic                  # no camera, known truth
python pipeline/live.py --roi 320 240 40 --window 60 --record run.csv
python pipeline/live.py --headless 60                       # no window, prints solutions
```

Three sources behind one interface: `WebcamSource`, `VideoFileSource` (the
fallback — plays a clip at its own frame rate so the whole live path is
exercised) and `SyntheticSource` (frames from the forward model, so the
on-screen number can be checked against a commanded value).

### Measured performance

| Source | Buffer | Recovered | Commanded | Error |
|---|---|---|---|---|
| `synthetic_model_B.mov` | 55 s @ 60 fps | 1.8502 s (peak 0.4625 s, ×4) | 1.84971 s | 0.027 % |
| `--source synthetic` | 75 s @ 30 fps | 1.8496 s (peak 0.4624 s, ×4) | 1.84971 s | 0.004 % |
| `--source synthetic` | 50 s @ 30 fps | 0.9248 s (×2) | — | locked to P_A/2 |

The 50 s row is the tool working, not failing: too few cycles to see the
asymmetry that separates P_A from P_A/2, so it stops one rung short. The
display shows the harmonic multiplier and the summary reports the fractional
lock explicitly rather than quoting a bogus 50 % error.

### Why the time base differs from the recorded pipeline

The recorded pipeline takes time from the frame index, because a locked 60 fps
recording is uniform by construction and §4.2 makes the frame rate the clock. A
webcam is not that — it drops frames and USB scheduling jitters arrival times.
Frame index over nominal fps is therefore *wrong* live, and wrong in a way that
shifts the recovered period. Every sample here is stamped with
`time.perf_counter()` at grab time and Lomb-Scargle is fed real timestamps,
which is exactly why LS is the right tool and an FFT is not. Measured cadence
and jitter are on screen; the summary warns if the measured rate differs from
nominal by more than 5 %.

### What the live path caught

**The spectrum has to be split into bands.** Taking the global periodogram
maximum makes a *spin-state* monitor report the rock period: the precession
envelope carries about 0.30 power against 0.13 for the spin, so the first
working version confidently displayed 11.35 s instead of 1.85 s. The headline
now comes from above `--split` (default 0.5 Hz), with the envelope reported
separately. `search()` splits the same way for the same reason.

**`flatten()` needs positive, unit-mean input.** Its first step divides by a
boxcar average; fed the zero-mean standardised series the periodogram uses, the
divisor oscillates through zero and the fold test silently returns nonsense — it
reported ×1 for a target the offline pipeline resolves to ×4. The live solver
now passes unit-mean flux into the walk, matching offline. A zero-scatter guard
was also added to `resolve_harmonic` for quantised live data.

**Auto-exposure could not be disabled on the camera in this machine** — the
property reads back −1.0 (unsupported) through DirectShow. The tool sets it,
reads it back, and shouts when the driver refuses, because a camera running AE
flattens exactly the modulation being measured (§8.4) and nothing downstream can
detect it. Take that warning seriously: if it appears, the light curve is
compromised regardless of what the period display says.

**The FAP is doing real work.** Pointed at a static scene the tool reports
periods, as any peak-finder will — but false-alarm probability climbs to ~0.88
and the number greys out. A confident-looking period with FAP above 0.01 means
nothing.

### Connecting a phone camera

See [docs/OBS_SETUP.md](../docs/OBS_SETUP.md) for the iPhone -> OBS ->
virtual camera route: requirements, step-by-step setup, and the two things OBS
changes that matter (the time base becomes OBS's clock, and exposure must be
locked on the phone because the virtual camera ignores exposure commands).

```bash
python pipeline/live.py --list-devices          # find the OBS Virtual Camera index
python pipeline/live.py --source 1 --width 1280 --height 720 --fps 60
```

### Limits

The window is a live view, not a measurement. It has no response linearisation,
no CFR audit, and no uncertainty on the period beyond the FAP. Use `--record` to
log timestamped samples and re-analyse offline for anything quotable.


## Import/Export: Importance-Based Notification Rule

Full design note: [docs/NOTIFICATION_RULES.md](../docs/NOTIFICATION_RULES.md).

Detection is cheap; notification is expensive. The rule is not "notify on
anomaly" but "notify when an event clears an explicit, written-down bar" — and
every decision, fired or suppressed, records why.

```bash
python pipeline/analyse.py --csv data/serial/run01.csv --expect 64.0        --policy bench --export data/sessions/run01
python pipeline/analyse.py --session data/sessions/run01   # integrity-checked
python pipeline/test_notify.py                             # 44 checks
```

Importance combines severity, confidence, novelty and persistence; urgency is a
separate axis driving channel routing and quiet-hour overrides. Novelty and
persistence combine as `max`, not a product: an event matters if it is *either*
new *or* entrenched, and multiplying would suppress both the first occurrence
and the hundredth.

| Scenario | Result |
|---|---|
| Healthy runs, 8 seeds | 0 false alarms |
| Spin-down injected, 8 seeds | 8/8 detected |
| Faulted run | 26 events → 8 notified (69 % suppressed) |
| Test suite | 44/44 pass |

Session archives carry SHA-256 checksums, schema version, provenance and the
policy that produced their decisions. Import verifies all of it and raises
events for tampering, truncation, non-monotonic timestamps and schema drift —
gated through the same engine as live events.

Two false-alarm sources were found by the tests, not by inspection: a
near-constant feature history makes any wobble look significant (fixed by
requiring a minimum fractional change as well as an n-MAD exceedance), and a
small-integer glint count quantises so a 1→2 flip reads as a 100 % change (fixed
by tracking `duty_cycle` instead).
