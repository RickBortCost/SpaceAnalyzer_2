# 6. X1/X2 markers and interval analysis

![markers](Paused chart with X1 and X2 markers and the «X1 ↔ X2 analysis» section.)

There is **one** «X1 ↔ X2 analysis» section, at the bottom of the page: it shows the values of the selected chart (click on the chart or use the «Active chart» menu); there is no longer one per chart.

| N. | Element |
|---|---|
| 1 | **Header** of the section: opens and closes with a click and states the chart it refers to. |
| 2 | **Place X1**: press and click on the chart. |
| 3 | **Place X2**: same as X1. |
| 4 | **Reset markers**: removes them. |
| 5 | **Summary**: time of each marker and Δt. |
| 6 | **Table of values**: for each signal the value at X1, at X2 and the difference. |

Markers can be dragged. In Play they follow the window: use Pause to fix the interval. Values at the markers are read from the original samples; if a stretch has left the buffer, the application says so instead of reconstructing it (message «Resume Play and pause again»). The same panel is present in the Recordings, InfluxDB and PostgreSQL pages.
