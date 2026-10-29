# 1. What Space Fieldbus Analyzer is

Space Fieldbus Analyzer is Windows software to acquire, display and record data from PLCs and industrial systems, to read data from time-series databases and to analyse them after acquisition. The supported live sources are **OPC UA**, **ADS** (Beckhoff TwinCAT), **MQTT** and **S7** (Siemens): they can also be used together (simultaneous acquisition, up to 64 signals in total), each with its own session, errors and reconnection. Historical data are read from **InfluxDB 2.x** and **PostgreSQL** (including TimescaleDB), always read-only.

Since version 3.0 the product no longer contains Python. The Windows EXE consists of a launcher with splash screen (Go), an ASP.NET Core service (.NET 8, embedded) and the acquisition and recording engine (`SpaceAnalyzer.Engine.dll`). The interface is a web page served locally: access is allowed only from `127.0.0.1`/localhost and every modifying request requires a token.

| Section | Purpose |
|---|---|
| **LIVE** | Real-time trends, signal selection, X1/X2 markers, formulas on the interval, FFT, Record. |
| **Registrazioni** (Recordings) | Opening and analysis of recorded files (CSV, Parquet, FDA/DAS4, RBF, DAT), comparison of several files. |
| **Database ▾** | Menu with **InfluxDB** (InfluxDB 2.x server) and **PostgreSQL**: charts, table view, saved SQL queries and data export. |
| **Strumenti ▾** (Tools) | Menu with **Conversione** (conversion from one file format to another), **Dati calcolati** (formulas shared between Live and recordings) and **Debug SSH** (console to a remote target, independent of acquisition). |
| **Connessione** (Connection) | Live data source, connection parameters, read interval and buffer. |

The interface labels in the screenshots are in Italian; the language can be changed from the top bar (Italian, English, German, French, Spanish). In the tables, the Italian label is given in brackets where it helps to find the control.

## What changed from 3.0.8 to {{version}}

| Version | What changes |
|---|---|
| **3.1.0** | Double-click on a signal: creates a chart with that signal only. Independent Y scales per signal with coloured axes (up to 4 columns per side). Shared scales by dragging names in the legend; context menu. Up to 24 charts per page. |
| **3.2.x** | PC date and time in the header. X1/X2 no longer in the bar: markers are placed from the X1 ↔ X2 analysis. Zoom and pan per single Y scale, «+N Y», keyboard legend, connection status dot. |
| **3.3.0** | Wheel on the LIVE chart = displayed period (X axis). |
| **3.4.x** | New name **Space Fieldbus Analyzer** in the interface, launcher, logs and exports; the name appears in the header from 1500 px; Settings menu in a fixed position; shutdown without the «Failed to fetch» error. |
| **3.5.0** | New **Conversion** section between formats (CSV, Parquet, FDA DAS4, RBF v3, DAT); opening of DAT files written by ibaPDA; cursor and grid aligned between charts of the same file in Recordings; FDA files are split before exceeding 255 MB of RAM in the FDA program. |
| **3.6.0** | Data type chosen automatically (RBF with native Boolean/Int16 channels, FDA with exact 16 bits and offset, less memory in LIVE charts). |
| **3.7.0** | **InfluxDB** page (reading from InfluxDB 2.x) and **Tools** menu (Conversion, Calculated data, SSH Debug). |
| **3.8.x** | **General X1 ↔ X2 analysis**: a single section at the bottom of the page showing the values of the selected chart (no longer one per chart). **Collapse to bar** button next to full screen. **Chart reordering** by drag and drop. |
| **3.9.x** | ADS: grouped cyclic notifications (down to 50 ms) and Windows priority during acquisition. InfluxDB: calculated X1/X2 values, up to 100,000 points per series, adjustable width. |
| **3.10.0** | **InfluxDB data export** to CSV, Parquet, FDA or RBF, with period and folder of your choice. |
| **3.11.0** | **Database** menu with InfluxDB and **PostgreSQL**. |
| **3.12.x** | PostgreSQL: **table view**, read-only **SQL queries** and **saved queries**. A signal's name applies to all charts on the page. |
| **3.13.0** | This illustrated guide is included in the package of every version. |
| **3.14.0** | S7: **PLC timestamp** and cycle coherence check (classic and symbolic); **Select branch** in the S7 symbol tree. |

## Starting on Windows

Download `Space_Fieldbus_Analyzer_V<version>_WINDOWS.zip` from the latest release, extract it to a writable folder and run `Space_Fieldbus_Analyzer_V<version>.exe`. The splash screen with the start bar appears, then the interface opens in the browser (port 5000 or the first free one after it). It starts full screen (F11 to exit) and the page does not scroll: the top bar and the status bar stay fixed and the panels scroll inside. The **Spegni** (Shut down) button (power symbol, top right) closes the application. Closing only the browser tab leaves the service running.

## Typical workflow

1) Connect to the source → 2) scan and choose signals → 3) live trend and analysis with markers → 4) Record → 5) analysis of the recording (FFT, formulas, comparison, export). Alternatively: Database → InfluxDB or PostgreSQL → series, period and chart → export.
