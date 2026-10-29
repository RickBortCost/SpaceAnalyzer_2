# 5. The LIVE page: signals and charts

![live](LIVE page with two vertical charts: each signal has its own coloured Y scale (Y1…Y4).)

| N. | Element |
|---|---|
| 1 | **Active chart**: ticked signals are assigned to the chart selected in this menu. |
| 2 | **Live signals**: checkbox to assign the signal to the chart, coloured bar, value, quality, scale (Y1, Y2…) and ⋮ menu. Double-click on the name: creates a chart with that signal only. |
| 3 | **Time view**: «Whole acquisition» (the whole session) or «Last window» (recent period). |
| 4 | **Layout**: 1 chart, 2 vertical, 2 horizontal, 4 charts, free by column or grid. |
| 5 | **+ Chart**: adds a chart (up to 24 per page). |
| 6 | **FFT / Export**: spectral analysis and export of the X1–X2 interval (chapter 13). |
| 7 | **Chart area**: drag to zoom, wheel = displayed period, wheel on the axes = zoom of that scale, double-click to reset; below, the time-window navigator. |
| 8 | **X1 ↔ X2 analysis**: a single section at the bottom of the page with markers and the table of values (chapter 6). |

## Y scales per signal, legend and wheel

Each signal has its own Y scale: axes are coloured like the signal, numbered Y1, Y2… and arranged in up to 4 columns per side. Beyond the limit the scale is still calculated but has no column and the «+N Y» indication appears.

**Shared scales.** Dragging a channel name onto another in the legend makes the two share one scale (repeatable with any number of channels); autoscale uses the overall minimum and maximum of the valid samples of the group. Joining channels with different physical units requires explicit confirmation («numeric scale without physical equivalence»): values are never modified. From the keyboard: Space or arrows pick up a channel, Enter drops it onto another, Del separates it, Esc cancels.

![legend_menu](Legend context menu (right click): Separate scale, Join all scales, Separate all scales.)

![legend_shared](After dragging «Forza» onto «Pressione» the two channels share scale Y1.)

**Zoom per single scale.** The wheel over an axis column zooms only that scale around the cursor (with Shift it moves it by 10 %); vertical dragging on the axis moves it; double-click on the axis restores the scale.

**Wheel on the chart area = displayed period.** Upwards it shortens the period, downwards it lengthens it (step ×1.25), anchored to the last sample. The status line shows «Displayed period» and the seconds shown, as a reminder that the chart is not showing the whole session.

![wheel](After a few wheel clicks the chart shows the last seconds: «Displayed period».)

## Name, colour and properties of a signal

From the signal's ⋮ menu (or from the legend) the «Signal · Chart» window opens:

![signal_properties](Signal properties window.)

| N. | Field |
|---|---|
| 1 | **Signal**: switches to the properties of another signal in the chart. |
| 2 | **Name / alias**: the name shown in legend, tables and recordings. **It applies to all charts** on the page: if the signal is moved to another chart it keeps the assigned name. |
| 3 | **Displayed unit**. |
| 4 | **Axis**: left or right Y scale the signal is bound to. |
| 5 | **Colour** of the curve and the axis. |
| 6 | **Line thickness** in pixels. |
| 7 | **Visible in the chart**: hides the curve without removing it from acquisition. |

Colour, thickness, axis and visibility are specific to the single chart; the name is global. Record, FFT and export always keep the original values. «Remove from acquisition» takes the signal out of the selection.

## Chart menu and options

![chart_menu](Chart ⋮ menu.)

The ⋮ menu contains: **Configure axes** (autoscale or manual limits), **Configure signals**, **Synchronise time**, **Export PNG image**, **Rename**, **Duplicate**, **Clear** (resets the view of that chart without touching the others or the recording) and **Remove chart**. «Synchronisation» (at the top) aligns zoom and crosshair across charts.

## Collapse to bar and chart reordering

![collapse_reorder](Chart 1 expanded, Chart 2 collapsed to a bar.)

| N. | Element |
|---|---|
| 1 | **Drag handle** (⠿): drag the chart to change its position on the page. |
| 2 | **Collapse to bar** (symbol ▁): the chart is reduced to its header only (title, signals and status) and frees space; in the collapsed bar the same button (▣) restores it. Acquisition continues. |
| 3 | **Expand** (full screen): takes the chart to full page; the same button returns to the normal view. |

## Pause, Play and Live zoom

Pause freezes only the view: data keep being acquired and recorded. Play brings the view back to the updated data. **Live zoom**: enable it, drag a rectangle on the chart to choose X duration and Y scale; the window keeps following new samples with the chosen duration and fixed vertical limits. Reset zoom (or double-click) returns to the full view. Live zoom is saved in the workspace.

Live uses the same precision as recordings: the chart contains every acquired sample. When visible samples exceed the pixels, each pixel column shows the minimum–maximum range of all its samples (single-sample peaks remain visible) and individual points appear when zooming in. Markers, formulas, FFT and export always use the original samples.
