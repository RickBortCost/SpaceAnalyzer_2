# 2. The top bar

![topbar](Top bar: the numbers refer to the table.)

| N. | Control |
|---|---|
| 1 | **LIVE**: real-time trend page. |
| 2 | **Registrazioni** (Recordings): analysis of recorded files and comparison. |
| 3 | **Database ▾**: menu with **InfluxDB** and **PostgreSQL** (reading, charts, table view, queries and export). |
| 4 | **Strumenti ▾** (Tools): menu with **Conversion**, **Calculated data** and **SSH Debug**. Both menus close with a click outside, Esc or by choosing an item. |
| 5 | **Connessione** (Connection): opens the popup with data source and parameters. The dot next to it shows the state: green connected, yellow in progress, red error, grey not connected. |
| 6 | **Pausa / Play** (Pause / Play): freezes the view of the active chart; acquisition and Record continue. |
| 7 | **Zoom live**: the zoom keeps the X duration and Y limits while the window follows new data. |
| 8 | **Record**: opens the «New recording» window; while recording it shows the elapsed time. |
| 9 | **Stop**: ends the recording; the file conversion continues in the background. |
| 10 | **Zoom (↺)**: resets zoom and autoscale. |
| 11 | **Language**: Italian, English, German, French, Spanish. |
| 12 | **Theme**: Standard, Dark, System. |
| 13 | PC date and time, updated every second (useful to compare the PC time with the PLC time). |
| 14 | **Impostazioni** (Settings): saving of settings and layout, opening of the logs. The menu opens in a fixed position, above everything else. |
| 15 | **Spegni** (Shut down): closes the application and waits for saves in progress to finish. |

## The Database and Tools menus

![menu_database](Database menu: the two database reading pages.)

![menu_tools](Tools menu: Conversion, Calculated data and SSH Debug.)

The name «Space Fieldbus Analyzer» appears from 1500 px of width (German and French from 1640 px, Spanish from 1560 px); below that only the logo remains. From 1500 to 1920 px the bar stays on one row in all languages. The version is in the status bar at the bottom, the REQ/REAL periods in the Live diagnostics. The X1/X2 markers are not in the bar: they are placed from the «X1 ↔ X2 analysis» panel (chapter 6).
