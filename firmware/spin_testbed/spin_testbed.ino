/*
 * SDC-001 — Optical Determination of Space-Debris Spin State
 * Subsystem 1 firmware: two-axis target simulator + ground-truth channel
 *
 * Arduino Uno R3 + CNC Shield V3, two TMC2209 in the X and Y sockets,
 * 1/16 microstepping (MS1 = MS2 = HIGH), 3200 microsteps per motor revolution.
 *
 *   Axis A  X socket   STEP D2  DIR D5   direct drive, target spin
 *   Axis B  Y socket   STEP D3  DIR D6   3:1 GT2 belt, cradle rock
 *   ENABLE  D8, active LOW (shield common)
 *   Slot sensor OUT    D9 (X+ limit header), 5 V logic
 *
 * Defaults per SDC-001 Rev B §5.2 / Appendix A.3:
 *   Axis A  1730 microsteps/s  -> P_A = 3200 / 1730 = 1.8497 s
 *   Axis B   561 microsteps/s  -> P_B = 4 * 1600 / 561 = 11.41 s
 *
 * SWEEP AMPLITUDE — read this before changing it.
 *   Appendix A.3 gives "Axis B sweep, +/-60deg = 3,200 microsteps (120deg / 0.0375deg)".
 *   The 3200 there is the TOTAL peak-to-peak travel, so the amplitude measured
 *   from centre is 1600 microsteps. At 561 microsteps/s the triangle period is
 *   4 * 1600 / 561 = 11.41 s, which is the P_B the spec calls for.
 *   Setting this to 3200 rocks the cradle +/-120deg and stretches the flexing
 *   cable service loop past what §5.1 permits in place of a slip ring. It is
 *   reachable at runtime with "S3200" but is not the compiled default.
 *
 *   The spec's own "842 steps/s" figure in Appendix A.3 is an arithmetic slip;
 *   its stated derivation (2 * 3200 microsteps per 11.4 s cycle) yields 561.
 *
 * The main loop does four things only: step A, step B, poll D9, sip one serial
 * byte. Anything added here shows up as jitter in the light curve.
 */

#include <AccelStepper.h>

// ---------------------------------------------------------------- pin map
const uint8_t PIN_A_STEP = 2;
const uint8_t PIN_A_DIR  = 5;
const uint8_t PIN_B_STEP = 3;
const uint8_t PIN_B_DIR  = 6;
const uint8_t PIN_ENABLE = 8;   // active LOW
const uint8_t PIN_SLOT   = 9;   // IR slot sensor, LM393 digital OUT

// ------------------------------------------------------------- motion defaults
const float   DEFAULT_RATE_A     = 1730.0f;  // microsteps/s, continuous
const float   DEFAULT_RATE_B     =  561.0f;  // microsteps/s, triangular
const long    DEFAULT_SWEEP_AMP  =  1600L;   // microsteps from centre = +/-60deg

// Hard ceiling on the sweep, and the value the operator asked for.
//
// +/-1600 microsteps is the +/-60deg mechanical limit of Rev B section 5.1 and
// is what the compiled default uses, so an unattended first power-up cannot
// drive the cradle into the posts. The ceiling is set to 3200 because that
// amplitude was explicitly requested: send "S3200" to reach it at runtime.
//
// Know what changes at 3200: the cradle sweeps +/-120deg, twice the travel the
// flexing service loop was sized for, and the triangle period becomes
// 4 * 3200 / 561 = 22.8 s rather than the 11.4 s P_B in section 5.2. Check the
// cable loom by hand through a full sweep with the motors released before
// running it that way.
const long    SWEEP_AMP_MAX      =  3200L;

// AccelStepper on an ATmega328P sustains roughly 4000 steps/s summed across
// axes (Rev B §5.3). 1730 + 561 = 2291 leaves comfortable margin.
const float   RATE_MAX           = 3000.0f;

// ------------------------------------------------------------------- state
AccelStepper axisA(AccelStepper::DRIVER, PIN_A_STEP, PIN_A_DIR);
AccelStepper axisB(AccelStepper::DRIVER, PIN_B_STEP, PIN_B_DIR);

float rateA    = DEFAULT_RATE_A;
float rateB    = DEFAULT_RATE_B;
long  sweepAmp = DEFAULT_SWEEP_AMP;
int   sweepDir = +1;          // current travel direction of Axis B
// Starts HALTED. Axis B takes its power-on position as the centre of the
// sweep, so a board that boots straight into motion will rock +/-60 deg from
// wherever the cradle happens to be parked -- into the posts if it was left at
// an extreme. Centre the cradle, send Z, then R. See the bring-up note below.
bool  running  = false;

uint8_t  slotPrev = LOW;      // previous D9 sample, for rising-edge detection
uint32_t pulseCount = 0;

// Serial command accumulator. Filled one byte per loop iteration so that a
// burst of host input cannot stall the step loop.
char cmdBuf[16];
uint8_t cmdLen = 0;

// ------------------------------------------------------------------- setup
void setup() {
  pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_ENABLE, HIGH);      // drivers released until configured

  pinMode(PIN_SLOT, INPUT);            // LM393 module drives the line actively

  axisA.setMaxSpeed(RATE_MAX);
  axisB.setMaxSpeed(RATE_MAX);
  axisA.setSpeed(rateA);
  axisB.setSpeed(rateB * sweepDir);
  axisB.setCurrentPosition(0);         // centre of sweep; see CENTRE note below

  Serial.begin(115200);

  digitalWrite(PIN_ENABLE, LOW);       // engage both drivers
  slotPrev = digitalRead(PIN_SLOT);

  banner();
}

// -------------------------------------------------------------------- loop
//
// Four actions, nothing else. runSpeed() returns immediately when the next
// step is not yet due, so both axes are serviced every pass.
void loop() {
  if (running) {
    axisA.runSpeed();
    if (rateB > 0.0f) axisB.runSpeed();

    // Triangular sweep: reverse at each limit. Checked after stepping so the
    // reversal happens on the pass that reaches the limit, not one later.
    long posB = axisB.currentPosition();
    if (sweepDir > 0 && posB >= sweepAmp) {
      sweepDir = -1;
      axisB.setSpeed(-rateB);
    } else if (sweepDir < 0 && posB <= -sweepAmp) {
      sweepDir = +1;
      axisB.setSpeed(rateB);
    }
  }

  pollSlotSensor();
  pollSerial();
}

// ------------------------------------------------- ground-truth pulse channel
//
// One rising edge per revolution of Axis A. micros() is printed raw; the host
// differences successive values to get the true revolution period. Serial.write
// is interrupt-driven and the 12-odd bytes emitted once every 1.85 s never fill
// the 64-byte TX buffer, so this does not block. Never call Serial.flush() here.
inline void pollSlotSensor() {
  uint8_t s = digitalRead(PIN_SLOT);
  if (s == HIGH && slotPrev == LOW) {
    uint32_t ts = micros();
    pulseCount++;
    Serial.print(F("P "));
    Serial.println(ts);
  }
  slotPrev = s;
}

// -------------------------------------------------------- serial command port
//
// One byte per loop iteration. Commands are line-terminated, case-insensitive:
//
//   A<rate>   Axis A rate, microsteps/s          e.g. A1730
//   B<rate>   Axis B rate, microsteps/s          e.g. B561; B0 parks Axis B
//   S<amp>    Axis B sweep amplitude from centre  e.g. S1600. Refused above
//             SWEEP_AMP_MAX; warns above the +/-60 deg service-loop limit.
//   R         run
//   H         halt (drivers stay engaged, holding position)
//   E0 / E1   drivers disengaged / engaged
//   Z         zero the Axis B position counter at the current position
//   ?         report current configuration
//
inline void pollSerial() {
  if (!Serial.available()) return;

  char c = Serial.read();
  if (c == '\r') return;

  if (c == '\n') {
    cmdBuf[cmdLen] = '\0';
    if (cmdLen) handleCommand();
    cmdLen = 0;
    return;
  }

  if (cmdLen < sizeof(cmdBuf) - 1) cmdBuf[cmdLen++] = c;
}

void handleCommand() {
  char key = cmdBuf[0];
  if (key >= 'a' && key <= 'z') key -= 32;
  const char *arg = cmdBuf + 1;

  switch (key) {
    case 'A': {
      float v = atof(arg);
      if (v <= 0.0f || v > RATE_MAX) { Serial.println(F("ERR rate A")); return; }
      rateA = v;
      axisA.setSpeed(rateA);
      Serial.print(F("OK A ")); Serial.println(rateA, 2);
      break;
    }
    case 'B': {
      float v = atof(arg);
      // B0 parks Axis B. The cradle is heavy and, if it is not balanced about
      // the Axis B shaft, the tilt motor fights gravity through the whole
      // sweep -- which shows up as vibration in the frame and, worse, as a
      // rate error periodic at exactly the frequency being measured
      // (Rev B section 6.2). Being able to run Axis A alone is what lets you
      // tell a spin problem from a cradle problem.
      if (v == 0.0f) {
        rateB = 0.0f;
        axisB.setSpeed(0.0f);
        Serial.println(F("OK B parked (Axis A only)"));
        break;
      }
      if (v < 0.0f || v > RATE_MAX) { Serial.println(F("ERR rate B")); return; }
      rateB = v;
      axisB.setSpeed(rateB * sweepDir);
      Serial.print(F("OK B ")); Serial.println(rateB, 2);
      break;
    }
    case 'S': {
      long v = atol(arg);
      if (v <= 0) { Serial.println(F("ERR sweep")); return; }
      if (v > SWEEP_AMP_MAX) {
        // Refuse rather than clamp silently: a sweep past the ceiling is a
        // mechanical problem, not a parameter choice.
        Serial.print(F("ERR sweep > ")); Serial.println(SWEEP_AMP_MAX);
        return;
      }
      sweepAmp = v;
      Serial.print(F("OK S ")); Serial.println(sweepAmp);
      if (v > 1600L) {
        Serial.print(F("WARN sweep +/-"));
        Serial.print(v * 360.0f / 9600.0f, 1);
        Serial.println(F(" deg exceeds the +/-60 deg service-loop limit"));
      }
      break;
    }
    case 'R': running = true;  Serial.println(F("OK run"));  break;
    case 'H': running = false; Serial.println(F("OK halt")); break;
    case 'E':
      digitalWrite(PIN_ENABLE, arg[0] == '0' ? HIGH : LOW);
      Serial.print(F("OK EN ")); Serial.println(arg[0] == '0' ? F("off") : F("on"));
      break;
    case 'Z':
      // CENTRE: release the drivers (E0), rock the cradle to visual centre by
      // hand, re-engage (E1), then Z. The sweep is symmetric about wherever
      // this counter reads zero, so an uncentred Z biases the whole run.
      axisB.setCurrentPosition(0);
      sweepDir = +1;
      axisB.setSpeed(rateB);
      Serial.println(F("OK zero B"));
      break;
    case '?': banner(); break;
    default:  Serial.println(F("ERR cmd"));
  }
}

void banner() {
  Serial.println(F("# SDC-001 target simulator"));
  Serial.print(F("# A ")); Serial.print(rateA, 2);
  Serial.print(F(" us/s  P_A ")); Serial.print(3200.0f / rateA, 4); Serial.println(F(" s"));
  Serial.print(F("# B ")); Serial.print(rateB, 2);
  Serial.print(F(" us/s  amp ")); Serial.print(sweepAmp);
  Serial.print(F("  P_B ")); Serial.print(4.0f * sweepAmp / rateB, 4); Serial.println(F(" s"));
  Serial.print(F("# pulses ")); Serial.println(pulseCount);
  Serial.print(F("# state  ")); Serial.println(running ? F("RUNNING") : F("HALTED"));
  Serial.println(F("# cmds: A<r> B<r> S<amp> R H E0 E1 Z ?"));
  if (!running) {
    Serial.println(F("# bring-up: E0, centre the cradle by hand, E1, Z, then R"));
  }
}
