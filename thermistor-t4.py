#!/usr/bin/env python3
"""
Read cell voltage on AIN0 and 10k NTC thermistors on AIN1 and AIN2 and log to CSV.

Circuit (per channel):
    VS ──── 5kΩ ──┬── NTC ──── GND
                  │
                 AINx

NTC on the low side so a broken wire reads full-scale (easy fault detection).

    R_ntc = R_fixed * V_ain / (V_supply - V_ain)

Conversion uses the B-parameter equation:
    1/T = 1/T0 + (1/B) * ln(R/R0)


T4-SPECIFIC NOTES (differs from the T7)
---------------------------------------
* AIN0-AIN3 are dedicated high-voltage (HV) inputs, permanently ±10 V.
  There is no AIN#_RANGE to set and no differential mode, so the
  _RANGE and _NEGATIVE_CH writes from the T7 version are removed.
  (AIN4-AIN11 are 0-2.5 V only — too low for this divider, whose output
  is 3.33 V at 25 °C, so the HV terminals are the right choice here.)

* The T4 is 12-bit. On the ±10 V HV range that is a 5 mV step and about
  ±26.8 mV absolute accuracy, which works out to roughly ±0.6 °C near
  25 °C with this divider. Averaging (SAMPLES_PER_READING) knocks down
  the random part; the absolute part needs a known-temperature offset
  calibration if you care about it.

* Max source impedance for the HV lines is 1 kΩ, and the divider's source
  impedance is R_fixed ∥ R_ntc ≈ 3.3 kΩ at 25 °C. Two consequences:
      1. Auto-settling is only guaranteed to 1 kΩ, so settling time is set
         explicitly below (SETTLING_US) rather than left at 0/auto.
      2. The HV pin is ~1.3 MΩ to an internal ~1.4 V node, which pulls the
         reading slightly. That's ~0.25 % of reading here. Run with
         --compensate-input-z to correct for it analytically, or buffer the
         divider with an op-amp follower to make the issue go away properly.

* Like the T7, the T4 cannot read its own VS. Jumper VS to AIN3 and set
  V_SUPPLY_CHANNEL below for a ratiometric measurement; otherwise the 5.0 V
  constant is fine for USB power.
"""

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


def slug(label):
    """'Sensor 1' -> 'sensor_1', for CSV column names."""
    return re.sub(r"[^0-9a-zA-Z]+", "_", label).strip("_").lower()


def configure_ain(handle, channels):
    """Set resolution and settling. No _RANGE or _NEGATIVE_CH on T4 HV lines."""
    names, values = [], []
    for ch in channels:
        names += [f"{ch}_RESOLUTION_INDEX", f"{ch}_SETTLING_US"]
        values += [RESOLUTION_INDEX, SETTLING_US]
    ljm.eWriteNames(handle, len(names), names, values)


def read_averaged(handle, channels, n):
    """Read all channels n times and return the per-channel means."""
    totals = [0.0] * len(channels)
    for _ in range(n):
        for i, v in enumerate(ljm.eReadNames(handle, len(channels), channels)):
            totals[i] += v
    return [t / n for t in totals]


def build_csv_header(labels):
    header = ["timestamp", "elapsed_s", "cell_voltage_v", "cell_voltage_status"]
    for label in labels:
        s = slug(label)
        header += [f"{s}_volts", f"{s}_ohms", f"{s}_temp_c", f"{s}_status"]
    if V_SUPPLY_CHANNEL:
        header.append("v_supply")
    return header


def parse_args():
    p = argparse.ArgumentParser(description="Log cell voltage and NTC thermistors from a LabJack T4 to CSV.")
    default_name = f"dvdt_log_{datetime.now():%m%d_%H%M%S}.csv"
    p.add_argument("-o", "--csv", default=default_name, help=f"output CSV path (default: {default_name})")
    p.add_argument("-i", "--interval", type=float, default=INTERVAL_S, help="seconds between readings")
    p.add_argument("-d", "--duration", type=float, default=None, help="stop after N seconds (default: run until Ctrl-C)")
    p.add_argument("-a", "--append", action="store_true", help="append to the CSV instead of overwriting")
    p.add_argument("--identifier", default="ANY", help="serial number, IP, or name of the T4 (default: first found)")
    p.add_argument("--connection", default="ANY", choices=["ANY", "USB", "TCP", "ETHERNET"], help="connection type")
    p.add_argument("--compensate-input-z", action="store_true",
                   help="correct for the T4 HV input's ~1.3 MOhm loading (~0.25%% of reading)")
    p.add_argument("--quiet", action="store_true", help="write the CSV without printing each row")
    return p.parse_args()


def main():
    args = parse_args()

    labels = [label for label, _ in SENSORS]
    channels = [ch for _, ch in SENSORS]
    # Read order: cell voltage, thermistors, supply voltage (if measured)
    read_list = [V_CELL_CHANNEL] + channels + ([V_SUPPLY_CHANNEL] if V_SUPPLY_CHANNEL else [])
    r_input = R_INPUT if args.compensate_input_z else None

    handle = ljm.openS("T4", args.connection, args.identifier)
    try:
        info = ljm.getHandleInfo(handle)
        conn_types = {1: "USB", 2: "TCP", 3: "Ethernet", 4: "WiFi"}
        device_types = {4: "T4", 7: "T7", 8: "T8"}

        if info[0] != 4:
            print(f"WARNING: connected device is a {device_types.get(info[0], info[0])}, "
                  f"not a T4. AIN configuration may not be correct.", file=sys.stderr)

        print(f"Connected to LabJack {device_types.get(info[0], info[0])}  |  serial: {info[2]}  |  "
              f"connection: {conn_types.get(info[3], 'Unknown')}")

        configure_ain(handle, read_list)

        print(f"Cell voltage on {V_CELL_CHANNEL}")
        if V_SUPPLY_CHANNEL:
            print(f"Measuring V_SUPPLY on {V_SUPPLY_CHANNEL}")
        else:
            print(f"Using V_SUPPLY = {V_SUPPLY:.2f} V (nominal — not measured)")
        if r_input:
            print(f"Input-impedance compensation ON ({R_INPUT/1e6:.1f} MΩ / {V_INPUT_BIAS:.1f} V model)")

        mode = "a" if args.append else "w"
        # if you run w/ --append, opens the file in append mode, adds new rows @ the end. 
        # Otherwise, it opens in write mode, which creates the file or overwrites its existing contents.
        write_header = not (args.append and _has_content(args.csv))

        with open(args.csv, mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(build_csv_header(labels))
                f.flush()

            print(f"Logging cell voltage and {len(SENSORS)} thermistors every {args.interval}s to {args.csv}  "
                  f"(Ctrl-C to stop)\n")
            if not args.quiet:
                print(f"{'Channel':<{LABEL_W}} {'Voltage':>9} {'R (Ω)':>10} {'Temp (°C)':>10}")
                print("-" * 45)

            rows = 0
            t_start = time.monotonic()
            try:
                while True:
                    now = time.monotonic()
                    elapsed = now - t_start
                    if args.duration is not None and elapsed > args.duration:
                        break

                    try:
                        values = read_averaged(handle, read_list, SAMPLES_PER_READING)
                    except ljm.LJMError as e:
                        print(f"Read error: {e}", file=sys.stderr)
                        time.sleep(args.interval)
                        continue

                    v_cell = values[0]
                    voltages = values[1:1 + len(channels)]
                    v_supply = values[-1] if V_SUPPLY_CHANNEL else V_SUPPLY

                    cell_ok = V_CELL_MIN <= v_cell <= V_CELL_MAX
                    cell_status = "OK" if cell_ok else "OUT_OF_RANGE"

                    row = [datetime.now().isoformat(timespec="milliseconds"), round(elapsed, 3),
                           round(v_cell, 5), cell_status]
                    if not args.quiet:
                        flag = "" if cell_ok else " <-- OUT OF RANGE"
                        print(f"{'Cell Voltage':<{LABEL_W}} {v_cell:>8.4f}V{flag}")

                    
                    for label, v in zip(labels, voltages):
                        r = voltage_to_resistance(v, v_supply, r_input)
                        if r is None:
                            row += [round(v, 5), "", "", "FAULT"]
                            if not args.quiet:
                                print(f"{label:<{LABEL_W}} {v:>8.4f}V {'FAULT':>10} {'---':>10}")
                        else:
                            t_c = resistance_to_temp_c(r)
                            row += [round(v, 5), round(r, 1), round(t_c, 3), "OK"]
                            if not args.quiet:
                                print(f"{label:<{LABEL_W}} {v:>8.4f}V {r:>9.1f}Ω {t_c:>9.2f}°C")
                    if V_SUPPLY_CHANNEL:
                        row.append(round(v_supply, 5))

                    writer.writerow(row)
                    f.flush()          # survive a Ctrl-C or power loss mid-run
                    rows += 1
                    if not args.quiet:
                        print()

                    # Schedule against the start time so the interval doesn't drift
                    next_t = t_start + rows * args.interval
                    time.sleep(max(0.0, next_t - time.monotonic()))

            except KeyboardInterrupt:
                print("\nStopped.")

            print(f"Wrote {rows} rows to {args.csv}")

    finally:
        ljm.close(handle)
        print("LabJack closed.")


def _has_content(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return bool(f.read(1))
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    main()


# PREV YR NOTES
# Charging at 02:00:30
# calorimetery_ambient: 24.55
# cell_body: 24.80
# cell_tab: 24.12
# 4.94									