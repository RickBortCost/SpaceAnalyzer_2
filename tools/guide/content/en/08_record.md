# 8. Recording (Record)

![record_dialog](«New recording» window.)

| N. | Field |
|---|---|
| 1 | **Format**: Parquet (recommended, lossless), FDA · DAS4 + original archive, RBF (up to 64 signals) and CSV. FDA and RBF are generated after Stop and keep the original Parquet with all samples; DAT/iba and DAT/PDA3 are also available. |
| 2 | **Binary compression** (Parquet): Compact ZSTD 9 (recommended) or faster profiles; no loss of precision. |
| 3 | **Session name, Machine, Operator, Description**: metadata saved with the file and used by the filters. |
| 4 | **New file every**: time-based rotation, without stopping the session. |
| 5 | **Maximum file size** (MiB; 0 = disabled): rotation at the first limit reached. |
| 6 | **Write buffer** (seconds). |
| 7 | **Destination folder**: editable; Browse… opens the Windows dialog, Default restores the standard folder. |
| 8 | **Start recording** (or Cancel). |

Pausing and zooming the chart do not interrupt saving. Record saves all acquired signals of the left column, regardless of how they are arranged in the charts.

**Formats and memory.** Each FDA file is closed before exceeding about 255 MB of RAM in the FDA program and recording continues in a new file `_FDA0002`, `_FDA0003`… without losing samples; this also applies to conversion and to `--tool export-fda`. The data type of each signal is chosen automatically (chapter 10); LIVE charts use 32 bits for booleans, integers up to 16 bits and Float32 and switch to 64 bits by themselves when needed, without rounding anything.

## During and after recording

![record_bar](«REC» bar: elapsed time, format, rows written, speed, lost samples, part, queue and file name.)

![record_saved](After Stop: «Recording saved» with duration, rows and path; «Background saves» appears above the charts.)

Stop ends the recorded interval and frees Record immediately, without waiting for the conversion of the whole file: a new recording can be started (even with a different format and signals) while the previous one is being saved. The «Background saves» panel shows, for each session, name, format, phase, files produced and any error; «Ready» indicates an available file and «Open recording» loads it in the Recordings section. The protection limit is 32 sessions still to be completed. The primary archive is block-based with checksums and crash recovery (`--tool` utilities, chapter 15).
