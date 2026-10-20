# entropic-heat-testing
Measures the entropic heating coefficient (dV/dT, Bernardi's coefficient) of a single cell at different SOC (states of charge). OCV is measured at different chamber temperatures for each SOC. The slope of OCV vs. temperature then gives dV/dT.

## Channels
AIN0	- Cell voltage (OCV)	- Wired directly to the cell
AIN1	- Cell body thermistor	- 10k NTC in a voltage divider
AIN2	- Cell tab thermistor	- 10k NTC in a voltage divider

The configured sensors are AIN0, AIN1, and AIN2. The program takes eight readings per channel and averages them for each logged row. By default, it logs about once per second and continues until you press Ctrl+C.

## Running the program
Example:
python ntc_logger.py
By default it logs about once per second until you press Ctrl+C. Each logged row is the average of 8 readings per channel.

Example: log for 10 minutes at a 2-second interval, saving to a named file:
python ntc_logger.py --duration 600 --interval 2 -o dvdt_soc80_25C.csv

## flags raised
- Voltages within 0.5% of the supply rails are marked FAULT, and resistance and temperature are left blank for that reading. This may indicate an open or shorted sensor circuit, or a voltage outside the expected range.
- OUT_OF_RANGE on the cell voltage is only a flag. It does not stop the test. Charge and discharge cutoffs are enforced by the RIGOL and the power supply, not by this script.
- A floating or disconnected AIN0 reads around 1.4 V and will be flagged.

-o, --csv          Set the output CSV path
-i, --interval     Set the seconds between logged rows
-d, --duration     Stop after this many seconds
-a, --append       Append to an existing CSV
--identifier       Select a LabJack by serial number, IP, or name
--connection       Choose ANY, USB, TCP, or ETHERNET
--compensate-input-z
                   Apply the input-impedance correction
--quiet            Write the CSV without printing each row

## CSV columns
Column	            Description
timestamp	        Wall-clock time of the row (ISO format, ms)
elapsed_s	        Seconds since the logger started
cell_voltage_v	    Cell voltage in volts
cell_voltage_status	OK, or OUT_OF_RANGE if outside 3.0-4.35 V
<sensor>_volts	    Measured divider voltage (per thermistor)
<sensor>_ohms	    Calculated thermistor resistance (blank on FAULT)
<sensor>_temp_c	    Estimated temperature in °C (blank on FAULT)
<sensor>_status	    OK or FAULT

# Test Procedures:

1	Attach the NTC thermistor to the cell surface and route the harness and thermistor wires so they can exit the oven/thermal chamber without stressing the cell. Connect the cell to the RIGOL and the LabJack.

2	Set SOC (see charging procedure) at 25 C to 95%. 	

3	Turn off the RIGOL (make cell open circuit) and rest at 25 C until values are stable. (Wait around 1hr to establish stability)	

4   Record stable voltage and temperature as the OCV at 25 C for this SOC. 	

5	Increase oven/thermal chamber temperature by an increment of 10 C while keeping the cell at open circuit. 	

6	Wait until values are stable, then record final stable voltage and temperature. (Wait around 1hr to establish stability)	

7	Place the cell in the Thermal Chamber at 35 C	

8	Repeat steps 1-7, decreasing the SOC by 5%. (E.g., the SOCs you will be measuring are 95%, 90%, 85%,... 5%) 	

9	Disassemble the test setup with care. Return all materials	

10	Take the data, create an excel spreadsheet and for each SOC, fit OCV vs temperature. 

11  Take the dV/dT values for each SOC and plot dV/dT vs. SOC. 

# data needed to collect
- temperature of cell
- SOC (state of charge)
- oven/thermal chamber temp
- open circuit voltage 

timestamp, SOC %, chamber temperature °C, cell temperature °C, OCV V, stable?, sensor location