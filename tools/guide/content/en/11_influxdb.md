# 11. Databases: the InfluxDB page

The InfluxDB page (menu **Database ▾ → InfluxDB**) reads and plots data from an InfluxDB 2.x server (developed for 2.7), with the same functions as the other charts: markers, FFT / Export, shared Y scales and several independent charts. Access is **read-only**: the application never writes to the database.

## Connecting

Open **Server connection**, enter the address (for example `http://localhost:8086`) and an API token with read permission on the buckets (InfluxDB: Load Data → API Tokens), then **Connect**. The organisation is derived from the token's buckets; specify it only if the token sees more than one. The token is saved in `influx.json` in the application data folder and is never sent back to the interface (only the last four digits are shown): on reopening, the connection is re-established automatically. «Forget token» deletes it. For HTTPS servers with an unverified certificate a dedicated option exists.

![influx_page](InfluxDB page with three series open in the chart (test server).)

| N. | Control |
|---|---|
| 1 | **Server connection**: address, token, organisation, TLS, Connect / Forget token. |
| 2 | **Bucket**; (3) **Show system buckets** (`_monitoring`, `_tasks`). |
| 4 | **Search series with data in the last**: 24 hours, 7 days, 30 days, 1 year; (5) **Refresh** the list. |
| 6 | **Filter** on measurements, fields and tags. The list groups by measurement, with the type of each field (Double, Int64, Boolean, String). |
| 7 | **Period**: 5 min, 15 min, 1 h, 6 h, 24 h, 7 d, 30 d; (8) **Interval** with start and end of your choice. |
| 9 | **Data reduction**: Automatic (mean), Mean, Minimum, Maximum, Last value, Median, All points (up to 500,000 per series). |
| 10 | **Automatic refresh**: No, 5, 10, 30 or 60 s. |
| 11 | **Open in chart** and (12) **Deselect**. |
| 13 | **Target chart**, (14) **Layout** and (15) **+ Empty chart**. |

FFT / Export, markers and analysis work as in the other pages. The X1 ↔ X2 analysis shows the values of the selected chart; the chart width adapts to the page.

## Period and data reduction

Relative periods end now; if the server has received no data for more than 5 minutes they end at the last data point. If the period contains fewer points than the chart displays, all of them arrive, exact. Otherwise the data are reduced by the InfluxDB server to windows of round duration (1 s, 2 s, 5 s … 1 d) with the chosen function, and the points are placed at the centre of the window: the line above the chart states the number of points, the original ones and the reduction («5,400 points (from 10,800, mean every 2 s)»). With «All points» data are read at the frequency at which they are stored (for example 10 ms), up to 100,000 points per series in the chart. After a zoom, the data of the visible interval are reread with more detail; Reset zoom returns to the chosen period.

As soon as you are connected, the series list builds itself and the first chart opens with the first numeric measurement. Up to 64 series can be opened per chart. Boolean series are drawn as 0/1 (with reduction the mean is the fraction of time at 1); text series are listed but cannot be selected.

## Export data

The **Export…** button (below the series list) opens a window to save locally the data of the active chart's series.

![influx_export](«Export InfluxDB data» window.)

| N. | Field |
|---|---|
| 1 | **Period to export**: «Loaded period», «Interval visible in the chart» (after a zoom) or «Custom interval» with From/To. |
| 2 | **Format**: CSV, Apache Parquet, FDA DAS4 or RBF. |
| 3 | **File name**. |
| 4 | **Destination folder**; (5) **Browse…** opens the Windows dialog to choose it, «Default» restores the standard folder. |
| 6 | **Export**: starts the export, with progress and cancellation. |

All original points of the period are written, without reduction: with very dense data the file can be large and reading can take time. FDA and RBF accept at most 64 signals (limits as in chapter 10).

## Security and limits

Names of measurements, fields and tags are always inserted into Flux queries between escaped quotes (also `\`, `"` and `${`): they cannot alter the query. If a series has more tags than those chosen, the query is limited to the tags shown. The chart does not remember the selection after the application is closed. The screenshots of this chapter were obtained with the test InfluxDB server supplied with the project's tests, which reproduces the 2.x API responses with simulated data; the connection to a real instance must be tried by the user.
