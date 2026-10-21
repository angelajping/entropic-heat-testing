import argparse
import csv
import math
import re
import sys
import time
from datetime import datetime

from labjack import ljm

# ── NTC parameters ──────────────────────────────────────────────
R0 = 10_000         # nominal resistance at T0 (ohms)
T0 = 25.0           # nominal temperature (°C)
B = 3984            # B-coefficient (K) — check your datasheet
R_FIXED = 5000     # fixed resistor in the divider (ohms)

# ── Supply voltage ──────────────────────────────────────────────
# The T4 cannot read VS internally. Leave V_SUPPLY_CHANNEL = None to use the
# nominal constant, or jumper VS to a spare HV terminal (e.g. "AIN3") to
# measure it every scan — that cancels supply drift from the result.
# V_SUPPLY = 5.004
V_SUPPLY = 4.94
V_SUPPLY_CHANNEL = None     # e.g. "AIN3"

# ── Cell Voltage ─────────────────────────────────────────────────────
V_CELL_CHANNEL = "AIN0"  
V_CELL_MIN = 3.0            # datasheet lower limit (V)
V_CELL_MAX = 4.35           # datasheet upper limit (V)
# just to help check values make sense

# ── Thermistors ─────────────────────────────────────────────────────
SENSORS = [
    ("Cell Body Sensor           ", "AIN1"),
    ("Cell Tab Sensor            ", "AIN2"),
]

# ── Acquisition ─────────────────────────────────────────────────
INTERVAL_S = 1.0            # seconds between logged readings
RESOLUTION_INDEX = 5        # T4 command-response: 0 = auto, 1-5 valid. 5 = quietest
SETTLING_US = 1000.0        # explicit settling for the ~3.3 kΩ source impedance
SAMPLES_PER_READING = 8     # averaged per logged row; cheap at 1 Hz, helps 12-bit noise

# Readings this close to the rails are treated as an open/shorted sensor
# rather than a temperature. 0.005 flags anything outside 0.025-4.975 V,
# which on this divider is roughly below -55 °C or above +150 °C.
FAULT_FRACTION = 0.005

# ── T4 HV input model (only used with --compensate-input-z) ─────
R_INPUT = 1.3e6             # HV input impedance, ohms (datasheet typical)
V_INPUT_BIAS = 1.4          # voltage a floating HV input settles to

LABEL_W = 28                # console column width for channel names. pretty much only for looks.


def voltage_to_resistance(v_ain, v_supply, r_input=None):
    """Convert measured voltage to NTC resistance (low-side divider).

    Returns None for an open or shorted sensor.

    With r_input given, solves the node equation including the T4's finite
    HV input impedance:

        (Vs - v)/R_fixed = v/R_ntc + (v - V_bias)/R_input

    which rearranges to a closed form for R_ntc — no iteration needed.
    """
    lo = FAULT_FRACTION * v_supply
    hi = (1.0 - FAULT_FRACTION) * v_supply
    if not (lo < v_ain < hi):
        return None

    current_into_node = (v_supply - v_ain) / R_FIXED
    if r_input is not None:
        current_into_node -= (v_ain - V_INPUT_BIAS) / r_input

    if current_into_node <= 0:
        return None
    return v_ain / current_into_node

def resistance_to_temp_c(r_ntc):
    """Convert NTC resistance to temperature in °C (B-parameter equation)."""
    t0_k = T0 + 273.15
    inv_t = (1.0 / t0_k) + (1.0 / B) * math.log(r_ntc / R0)
    return (1.0 / inv_t) - 273.15

AIN1_V = 3.39
AIN1_T_REAL = 22.56 # C
AIN2_V = 3.41
AIN2_T_REAL = 22.05 # C

def error_analysis(measured_v, labjack_temp):
    """Perform error analysis for the thermistor measurement system."""
    # This function can be expanded to include more detailed error analysis
    # based on the specific requirements of the application.
    ain_r = voltage_to_resistance(measured_v, V_SUPPLY)
    ain_t = resistance_to_temp_c(ain_r)
    error = (ain_t - labjack_temp) / labjack_temp * 100
    return ain_r, ain_t, error

error_analysis_result1 = error_analysis(AIN1_V, AIN1_T_REAL)
error_analysis_result2 = error_analysis(AIN2_V, AIN2_T_REAL)

print(f"AIN1 TEMP ERROR: {error_analysis_result1[2]:.2f}%")
print(f"AIN2 TEMP ERROR: {error_analysis_result2[2]:.2f}%")
