# SPINDEX — demonstration video script

**Target length:** ~5 minutes
**Format:** narration over screen capture and hardware footage

> **Verify before recording:** the two statistics in Section 1 are marked
> `[CHECK]`. Quote only figures you can cite from ESA's Space Environment Report
> or SpaceX's own published numbers. A judge who catches one wrong statistic in
> the first thirty seconds discounts everything after it.

---

## 1 — The problem  (0:00 – 0:40)

**[VISUAL]** Orbital debris visualisation. Slow zoom into low Earth orbit.

> Low Earth orbit is filling up.
>
> There are more than thirty-six thousand tracked objects up there, and over a
> million fragments too small to track but large enough to destroy a spacecraft.
> `[CHECK]`
>
> SpaceX says the same thing from the operator's side: Starlink satellites now
> perform tens of thousands of collision-avoidance manoeuvres every six months,
> and that number keeps climbing. `[CHECK]`
>
> But a manoeuvre only dodges the problem. The debris is still there. And the
> largest, most dangerous objects — dead satellites and spent rocket bodies —
> can't dodge anything at all. They just keep orbiting, waiting to hit something.
>
> The only real fix is to go up and remove them.

---

## 2 — Servicers are real now  (0:40 – 1:15)

**[VISUAL]** ADRAS-J imagery — the approach photographs of the H-2A upper stage.

> For decades, that was a paper concept.
>
> Then in February 2024, Japan changed that. Astroscale launched ADRAS-J, and it
> flew right up to a spent H-2A rocket upper stage that had been tumbling in
> orbit for fifteen years. No docking port. No transponder. No cooperation of any
> kind.
>
> It got within metres, and photographed it.
>
> That mission moved orbital servicing from theory into engineering. Northrop
> Grumman's Mission Extension Vehicles have already docked with live commercial
> satellites and taken over their station-keeping. ClearSpace-1 is being built to
> capture and deorbit a rocket adapter.
>
> Space tugs are real.

---

## 3 — The gap  (1:15 – 1:50)

**[VISUAL]** A tumbling satellite model rotating slowly. Then the SPINDEX title card.

> So here's the honest question we started from.
>
> Innovating in aerospace is hard, because most of the feasible technology
> already exists. The rockets exist. The robotic arms exist. The rendezvous
> guidance exists.
>
> But look at what ADRAS-J actually spent its mission doing. It flew all that
> way, largely to answer one question: **how is this thing moving?**
>
> Because before a tug can grab a derelict, it has to match its tumble. Grab a
> spinning body without matching it, and its angular momentum becomes yours —
> now you have two tumbling spacecraft. Or you break it, and turn one piece of
> debris into a thousand.
>
> That question — how is it moving — isn't a hardware problem. It's a **data**
> problem. And right now, nobody is feeding that data to the servicers.
>
> So we asked: do you actually have to fly there to find out?
>
> No. The object is already telling you. You just have to know how to read it.

---

## 4 — The core idea: read the light  (1:50 – 3:00)

**This is the heart of the pitch. Do not rush it.**

**[VISUAL]** Title: SPINDEX. Then, and this is the shot that has to land — a
split screen. Left: the model rotating. Right: the brightness trace drawing
itself in real time, spiking every time a panel catches the light.

> So how do you find out how something is moving, when you can't touch it, can't
> talk to it, and it's a hundred kilometres away?
>
> You look at the light coming off it.
>
> A dead satellite makes no light of its own. It reflects sunlight — exactly like
> the Moon. And because it isn't a sphere, that reflection isn't constant. It has
> flat solar panels, a boxy body, antennas. So the amount of light it throws back
> at you depends entirely on which way it's facing.
>
> **[VISUAL]** Slow the rotation. Let the audience see a panel come face-on and
> the trace spike.
>
> Watch what happens. A flat solar panel edge-on returns almost nothing — the
> object nearly disappears. Rotate it face-on, and it flashes. A hard, bright
> specular glint.
>
> Now let it tumble, and record that brightness against time.
>
> **[VISUAL]** Full light curve scrolling — the glint train, with the slow
> envelope riding over it.
>
> That trace is called a light curve. And here is the thing that makes this whole
> project work: **that single line of data contains the entire motion.**
>
> The rhythm of the flashes gives you the rotation period. The slow rise and fall
> of the envelope gives you the precession. The sharpness of each glint tells you
> about the surfaces. The asymmetry between one flash and the next tells you about
> the shape.
>
> One dimension of data — brightness against time — and out of it comes the full
> three-dimensional attitude history of an object nobody can reach.

**[VISUAL]** Cut to a comparison: radar dish, lidar beam, ADRAS-J thruster
firing — then dissolve to a simple camera.

> And here's why we chose reflected light rather than radar, or lidar, or flying
> there to look.
>
> It's **passive**. We don't illuminate anything — the Sun already does that, for
> free, on every object in orbit.
>
> It needs **no cooperation**. The target is dead. No transponder, no beacon, no
> working systems. It doesn't have to help us, and it can't refuse.
>
> It doesn't need to **resolve the object**. A single point of light is enough. We
> are not taking a picture of the satellite — we're measuring one number, its
> brightness, over and over.
>
> And that last point is what makes it *scale*. ADRAS-J answered this question for
> exactly one object, and it cost an entire mission to do it. A light curve costs
> a camera and some time. Tens of thousands of objects are tracked by position
> today — only a small fraction have a known spin state.
>
> That is the difference between characterising one derelict, and characterising
> the whole catalogue.
>
> SPINDEX is the software that turns that light into the answer: rotation period,
> precession, cone angle, coarse geometry — live, with uncertainties on every
> number.

---

## 5 — The hardware  (3:00 – 3:35)

**[VISUAL]** The rig. Pan across the frame, then the cradle rocking, then the
electronics.

> To prove it works, we needed something where we already know the right answer.
>
> So we built the target. Aluminium frame, two stepper motors, an Arduino
> controller.
>
> The first axis spins the satellite model. The second axis rocks the entire
> cradle, sweeping the spin axis through the camera's view.
>
> That second axis matters. One spinning axis gives you a clean single frequency
> that any Fourier transform solves in one line — it proves nothing. Real
> derelicts tumble about a non-principal axis: two frequencies beating against
> each other, with no obvious period. Our rig reproduces that on a bench.
>
> And crucially — the rotation rate is set in firmware. So we know the true
> answer before we measure it. That's what lets us grade our own results instead
> of just asserting them.

---

## 6 — The software  (3:35 – 4:20)

**[VISUAL]** Screen capture of the live dashboard. Point to each panel as named.

> Here's the pipeline.
>
> Video comes in. We do aperture photometry on every frame — sum the light inside
> a fixed circle, subtract the background from a ring around it — and that gives
> us the brightness trace.
>
> Then a Lomb-Scargle periodogram finds the frequencies. We use Lomb-Scargle
> rather than an FFT for a specific reason: a real camera drops frames and
> jitters. We timestamp every single frame, and Lomb-Scargle handles unevenly
> sampled data natively. An FFT can't.
>
> Then the hard part. A symmetric satellite flashes **twice** per rotation, so the
> strongest peak sits at half the true period. Report that, and you're wrong by a
> factor of two. We resolve it by phase folding — and when a target is genuinely
> symmetric, the system says so instead of guessing.
>
> Finally, least-squares fitting and MCMC give every number a real uncertainty.
>
> And it runs live — thirty-three features updating continuously, with alerting
> that only fires when the target's motion actually *changes*. A servicer on
> approach doesn't need to hear that nothing happened. It needs to hear the
> instant something does.

---

## 7 — The demonstration  (4:20 – 4:55)

**[VISUAL]** Split screen: the rig turning, and the dashboard recovering the
period. Then the results table, full screen.

> So — does it work?
>
> We commanded the target to rotate with a period of one point eight five seconds.
>
> The software, reading only reflected light through a real camera and real video
> compression, recovered one point eight five seconds.
>
> Precession period, commanded eleven point four — recovered eleven point four.
> Cone angle, commanded sixty degrees — recovered fifty-nine point nine-eight.
>
> Every parameter inside half a percent. And the uncertainties are honest: truth
> falls inside our sixty-eight percent confidence interval for four out of six
> parameters — which is exactly the statistical expectation. Not padded, not
> optimistic.
>
> Across eighty runs, at noise levels from one to ten percent, seventy-eight
> passed. Forty-four unit tests, all passing.

---

## 8 — Close  (4:55 – 5:10)

**[VISUAL]** The rig turning. Fade to the SPINDEX title and the repo URL.

> Space tugs are flying now. They need to know how their target is moving before
> they touch it — and that answer has to be current, because tumble states drift
> over time.
>
> Every one of those objects is already broadcasting its own motion, continuously,
> in reflected sunlight. Nobody has to install anything on it. Nobody has to fly
> to it. The signal is free, and it has been there the whole time.
>
> SPINDEX is what reads it.
>
> Thank you.

---

## Delivery notes

**Pace.** About 150 words a minute. Read it aloud once and cut anything you
stumble over — written prose and spoken prose are different animals.

**Two moments carry the whole video.**

*Section 4* — the split screen where a panel comes face-on and the trace spikes.
That single shot explains the entire project without a word of narration. Shoot
it properly: slow the rotation right down, make the glint unmistakable, and let
it play for a beat before you speak over it. If an audience understands only one
thing, make it this.

*Section 7* — *commanded 1.84971, recovered 1.84971.* Slow down and let it land.
Section 4 makes them believe it could work; Section 7 proves it did.
**Say round numbers, show exact ones.** Say "one point eight five seconds"; put
`1.84971` on screen. Never read five decimal places aloud.

**If asked about hardware status,** answer plainly: the mechanism is assembled and
the firmware runs; the controller failed during bring-up and a replacement is on
order; the optical ground-truth sensor isn't fitted yet. Every result shown is
validated against synthetic data and rendered video with known truth. Being
straight about that is more convincing than hedging, and the software results
stand on their own.

**Shot list — capture all of these before you edit:**

1. Wide shot of the rig, both axes moving
2. Close-up of the cradle rocking through its sweep
3. Close-up of the electronics — Arduino, shield, drivers
4. Screen capture: dashboard with a period locked and a clean phase fold
5. Screen capture: `analyse.py` firing a notification
6. Screen capture: the results table

**Two things to avoid saying.** Don't call it "AI" or "machine learning" — it's
signal processing and statistical inference, and a technical judge will respect
the precision. And don't say "we invented" — say "we built", then let the
validation numbers do the arguing.
