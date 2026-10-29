# 14. Comparing several recordings

![compare](Comparison of two recordings (1 ms and 10 ms) in the same chart, with independent Y scales.)

In the list of the Recordings page tick from 2 to 32 files and press **Compare in chart**. Each signal keeps its name, source file and sampling; identical names in different files remain distinct signals. The chart opens up to 64 signals (up to 8 charts; 2048 signals in total).

| N. | Setting | Meaning |
|---|---|---|
| 1 | **Time reference** | «Actual date and time (UTC)» (default): the position on the X axis comes from the timestamps. «Align starts to the first file»: compares events that happened at different times; it does not indicate simultaneity. |
| 2 | **Comparison interval** | «Whole extent» (keeps different durations and gaps) or «Common interval only» (explicit error if there is no overlap). |
| 3 | **Time correction (ms)** | For each file: positive shifts forward, negative brings earlier (for example clock ahead by 1 s → −1000 ms). The original file is not modified. |
| 4 | **Compare in chart** | Opens the ticked files in the target chart. |

There is no clock drift correction nor interpolation or resampling: a 1 ms base can be compared with a 10 ms one keeping each original series. Markers do not report a value outside the signal's coverage. FFT, formulas and export use the original samples. The comparison generates a support file in the application's imports folder.
