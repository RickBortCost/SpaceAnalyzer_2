# 7. Calculated data: formulas

Formulas create derived signals. There are two ways: the quick panel under the charts and the dedicated «Calculated data» page (Tools menu).

## Quick panel under the charts

![calc_quick](Formula [A]-[B] with A = Temperature and B = Setpoint, result Max between the X1/X2 markers.)

| N. | Element |
|---|---|
| 1 | **Variables**: choose Variable A; with «Add variable» assign B, C… (also from different protocols). |
| 2 | **Formula** with references in square brackets: `[A]-[B]`, `([A]+[B])/2`, `mean([A],[B])`, `abs([A])`, `ifelse([B]>0,[A]/[B],0)`. Operators and functions: + − × ÷ ( ) abs sqrt mean min max pow ifelse; comparisons < <= > >= == !=. |
| 3 | **Alignment**: «Last valid value» uses only earlier samples within the tolerance (ms); «Identical timestamps» requires samples at the same instant. |
| 4 | **Result**: Last value, Mean, Min, Max, RMS, Peak-to-Peak, Standard deviation, X2−X1, Δ/Δt, Integral. |
| 5 | **Value** calculated, with interval, valid and excluded samples. The interval is the one between the markers if both are present, otherwise the visible window. |
| 6 | **Save and add**: registers the formula among the Calculated data and adds it to the chart; the signal is acquired from that moment. |

Invalid qualities, expired values and mathematical errors produce an empty point with an explanation: they are not replaced with zero. During Record the recording must be stopped before saving formula changes.

## The «Calculated data» page

![calc_page](Full formula editor (Tools menu → Calculated data).)

| N. | Element |
|---|---|
| 1 | **Name** of the calculated signal. |
| 2 | **Use**: in Live and/or in recordings. |
| 3 | **Sources**: list of available signals; a click inserts the reference in the formula. |
| 4 | **Formula**. |
| 5 | **Validate**: checks syntax and references. |
| 6 | **Test and preview**: shows values, quality and a curve without saving. |
| 7 | **Save formula**: makes it active. |

A formula can apply to Live, to recordings or to both; on recordings the X1/X2 interval can be used and a new CSV or Parquet saved with the original signals and the result. In Live, active formulas use one source at a time; notifications are reordered within a 20 ms window.
