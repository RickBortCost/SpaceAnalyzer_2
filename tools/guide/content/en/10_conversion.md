# 10. File conversion (Tools menu)

The Conversion section (Tools menu → Conversion) transforms a recorded file into another format, without opening it in the chart and without exporting an interval by hand. The original file is never modified.

![conversion](Conversion page with a file selected.)

| N. | Control |
|---|---|
| 1 | **Filter files…**: searches the list. |
| 2 | **Open file from PC…**: adds files from any location (also iba PDA3 DAT). |
| 3 | **File list** of the recordings folder: format, name, size and duration. |
| 4 | **Target format**: CSV, Apache Parquet, FDA DAS4 (.das), RBF v3 (.rbf). The limits of the chosen format appear under the menu. |
| 5 | **Signals**: All or a selection (maximum 64 for FDA and RBF). |
| 6 | **Filter signals…**. |
| 7 | **Interval** (optional): From/To, with a button to restore the whole file. Without an interval everything is converted. |
| 8 | **Convert**: starts the conversion. During the operation a bar shows the **overall percentage** (reading samples, preparing data and writing the file, with the expected total estimated — «~» — when few channels or a period were chosen) and the Cancel button, which stops reading and deletes partial files. With «Open file from PC…» the bar on the left also shows the upload (percentage and MB) and then the reading of the file. |

![conversion_done](Conversion completed: number of samples, file created and «Open in Recordings» button.)

| N. | Element |
|---|---|
| 1 | **Status**: outcome, samples written and format. |
| 2 | **Files produced**, with «Open in Recordings». |

| Item | Details |
|---|---|
| Sources | CSV, Parquet, FDA DAS4 (.das), RBF v3, TwinCAT Scope projects (.svdx), DAT (iba PDA3) and primary captures (.sac). |
| Targets | CSV, Apache Parquet, FDA DAS4 and RBF v3. |
| Samples | Every sample of the selection is written with value, quality and timestamp, without reduction. Instants in which an FDA/RBF channel has no value («missing») are not samples and are not reported. Names, units, period and session data go into the metadata of the new file. |
| Format limits | CSV and Parquet are lossless. FDA DAS4 stores scaled Int16 values (the rounding error is in the metadata) and times at millisecond resolution. FDA and RBF accept at most 64 signals. An RBF file holds at most 57,600 instants: longer recordings are split into several parts. Each FDA file is closed before exceeding about 255 MB of RAM in the FDA program and the conversion continues in `_FDA0002`, `_FDA0003`…, without losing samples. |
| Destination | «Conversioni» subfolder of the recordings folder. An existing name is not overwritten. |
| Execution | In the background, one conversion at a time; the interface stays usable. |

## Primary captures (.sac)

During every recording the application also writes a **primary capture** with all original samples, in the `Recordings\.primary` folder (extensions `.sac`, `.sac.part`, `.incomplete.sac`, `.recovered.sac`). It is normally just a safety copy, because the Parquet, FDA or RBF file is already in `Recordings`. If that file is missing or incomplete (abnormal stop, disk error, a format that failed), the primary capture can be **exported** to a readable format from the same Conversion page: captures appear in the list with the «Primary capture» format.

![conversion_sac](Primary capture selected in the Conversion page.)

| N. | Element |
|---|---|
| 1 | **Primary capture** in the list: type (complete, incomplete, interrupted `.part`, recovered), size and start. The list reads only the header, so it appears immediately even for files of tens of GB. |
| 2 | **Information**: format, size, number of signals and start. Duration and sample count are not shown because they would require reading the whole file. |
| 3 | **Format note**: reminds that the capture holds every original sample. |
| 4 | **Split into files of**: every file produced covers 15 minutes of recorded time (5, 30, 60 minutes or «A single file» can be chosen); the last one may be shorter. |
| 5 | **Convert**: signals and interval are chosen as for the other sources. |

The export writes every original sample of the chosen signals and interval, **split into files of 15 minutes** of recorded time (counted from the first exported sample; the cut falls between two instants, never inside a cycle): an archive of hours becomes a series of small files that are easy to open. With «A single file» one file is produced; the last file may be shorter. A capture that is **interrupted or damaged at the end** is not refused: all valid blocks are exported and the final message states the cause (for example «Capture not closed correctly» or «Capture closed with an error» with the number of samples that never reached the capture). With FDA DAS4 and RBF the non-periodic ADS notifications stay only in the capture and the maximum is 64 signals; CSV and Parquet export everything. The original file is not modified.

From the command line, without starting the interface: `--tool export-sac <file.sac> csv|parquet|das|rbf [folder] [minutes]`; minutes per file default to 15 (0 = a single file). Without a folder, files go to the `Conversioni` subfolder of the recordings; the result (samples, files, warnings) is printed as JSON.

## TwinCAT Scope projects (.svdx)

**Beckhoff TwinCAT Scope** projects saved with their data included («Include data in SVDX») open like any other recording and can be converted to CSV, Parquet, FDA DAS4 or RBF. Beckhoff does not publish the format specification: the reader was derived from a reference file (94 REAL32 channels at 1 ms) and checks the structure on every read; a file that does not match is refused with a message instead of producing wrong data.

![conversion_svdx](Scope project (.svdx) with two of three channels selected.)

| N. | Element |
|---|---|
| 1 | **Scope project** in the list (format «TwinCAT Scope (.svdx)»). A file from another PC is added with «Open file from PC…». |
| 2 | **Channels**: every channel of the project is a signal, with the name given in Scope and the unit if present. Only the selected ones are converted. |
| 3 | **Interval**: from/to within the recording. With fewer channels and a shorter period the output file is proportionally smaller. |
| 4 | **Convert**. |

Samples carry the absolute (UTC) time of the Scope recorder with 100 ns resolution; channels with different periods stay separate, without interpolation. FDA and RBF accept at most 64 signals: with more channels, choose the ones to export. Values are the raw values of the file (no scale or offset of the project is applied). Verified on REAL32 types; the other numeric types (integers, REAL64, BOOL) follow the same structure but have not been tried with real files.

## Automatically chosen data type

For each signal the application works out by itself the narrowest type that remains exact, based on the acquired type and the recorded values: no setting is needed. In RBF files boolean signals remain Boolean channels (1 byte) and integers of 16 bits or less with values within Int16 become native Int16 channels (2 bytes instead of the 4 of Float32). A UINT with values above 32767 stays Float32 (exact up to 16 million). For Double or 32/64-bit signals RBF only has Float32: that file stays approximate and the original Parquet archive stays exact. In FDA files an integer with a range within 65,536 values (for example a 16-bit UINT) is stored with exact 16 bits with gain 1 and offset 32768 (value = raw × gain + offset). The Recordings page shows the real storage of RBF files and the «lossless» indication derived from the metadata.

> To be verified in the proprietary FDA program: if it did not apply the channel offset, the values of 16-bit signals with offset would appear shifted.
