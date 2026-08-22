"""Commanded-motion constants for SDC-001, derived from Rev B Appendix A.3.

Everything the analysis treats as "truth" traces back to a firmware number,
so the derivation is written out here rather than hard-coded as a period.
"""

from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------- drivetrain
FULL_STEPS_PER_REV = 200      # 1.8 deg stepper
MICROSTEPPING = 16            # MS1 = MS2 = HIGH on the TMC2209
USTEPS_PER_MOTOR_REV = FULL_STEPS_PER_REV * MICROSTEPPING   # 3200

AXIS_A_RATIO = 1.0            # direct drive to the target hub
AXIS_B_RATIO = 3.0            # GT2 60T driven by 20T

USTEPS_PER_AXIS_A_REV = USTEPS_PER_MOTOR_REV * AXIS_A_RATIO   # 3200
USTEPS_PER_AXIS_B_REV = USTEPS_PER_MOTOR_REV * AXIS_B_RATIO   # 9600
AXIS_B_DEG_PER_USTEP = 360.0 / USTEPS_PER_AXIS_B_REV          # 0.0375

# ------------------------------------------------------------ firmware setting
RATE_A_USTEPS_S = 1730.0
RATE_B_USTEPS_S = 561.0

# Amplitude measured from centre. Appendix A.3's "3,200 microsteps" is the
# total peak-to-peak travel for +/-60 deg, so the amplitude is half of it.
# See the header of firmware/spin_testbed/spin_testbed.ino.
SWEEP_AMP_USTEPS = 1600.0

# ------------------------------------------------------------------- commanded
P_A = USTEPS_PER_AXIS_A_REV / RATE_A_USTEPS_S             # 1.8497 s
P_B = 4.0 * SWEEP_AMP_USTEPS / RATE_B_USTEPS_S            # 11.408 s
CONE_ANGLE_DEG = SWEEP_AMP_USTEPS * AXIS_B_DEG_PER_USTEP  # 60.0

# ---------------------------------------------------------------------- optics
PHASE_ANGLE_DEG = 30.0        # Rev B section 8.1, lamp-to-target-to-lens
FPS = 60.0
DEFAULT_DURATION_S = 120.0    # Rev B section 5.2 minimum


@dataclass(frozen=True)
class Truth:
    """Commanded values the inversion is scored against."""

    p_spin: float = P_A
    p_prec: float = P_B
    cone_deg: float = CONE_ANGLE_DEG
    phase_deg: float = PHASE_ANGLE_DEG

    def summary(self) -> str:
        return (
            f"P_A = {self.p_spin:.4f} s   P_B = {self.p_prec:.4f} s   "
            f"cone = {self.cone_deg:.1f} deg   ratio P_B/P_A = "
            f"{self.p_prec / self.p_spin:.4f}"
        )


TRUTH = Truth()


if __name__ == "__main__":
    print(TRUTH.summary())
    print(f"Axis A step rate check : {USTEPS_PER_AXIS_A_REV / P_A:.1f} usteps/s")
    print(f"Axis B step rate check : {4 * SWEEP_AMP_USTEPS / P_B:.1f} usteps/s")
