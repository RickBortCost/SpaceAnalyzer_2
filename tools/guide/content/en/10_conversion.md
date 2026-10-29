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
| 8 | **Convert**: starts the conversion. During the operation the samples read appear (with percentage when the total is known), the writing phase and the Cancel button, which stops reading and deletes partial files. |

![conversion_done](Conversion completed: number of samples, file created and «Open in Recordings» button.)

| N. | Element |
|---|---|
| 1 | **Status**: outcome, samples written and format. |
| 2 | **Files produced**, with «Open in Recordings». |

| Item | Details |
|---|---|
| Sources | CSV, Parquet, FDA DAS4 (.das), RBF v3 and DAT (iba PDA3). |
| Targets | CSV, Apache Parquet, FDA DAS4 and RBF v3. |
| Samples | Every sample of the selection is written with value, quality and timestamp, without reduction. Instants in which an FDA/RBF channel has no value («missing») are not samples and are not reported. Names, units, period and session data go into the metadata of the new file. |
| Format limits | CSV and Parquet are lossless. FDA DAS4 stores scaled Int16 values (the rounding error is in the metadata) and times at millisecond resolution. FDA and RBF accept at most 64 signals. An RBF file holds at most 57,600 instants: longer recordings are split into several parts. Each FDA file is closed before exceeding about 255 MB of RAM in the FDA program and the conversion continues in `_FDA0002`, `_FDA0003`…, without losing samples. |
| Destination | «Conversioni» subfolder of the recordings folder. An existing name is not overwritten. |
| Execution | In the background, one conversion at a time; the interface stays usable. |

## Automatically chosen data type

For each signal the application works out by itself the narrowest type that remains exact, based on the acquired type and the recorded values: no setting is needed. In RBF files boolean signals remain Boolean channels (1 byte) and integers of 16 bits or less with values within Int16 become native Int16 channels (2 bytes instead of the 4 of Float32). A UINT with values above 32767 stays Float32 (exact up to 16 million). For Double or 32/64-bit signals RBF only has Float32: that file stays approximate and the original Parquet archive stays exact. In FDA files an integer with a range within 65,536 values (for example a 16-bit UINT) is stored with exact 16 bits with gain 1 and offset 32768 (value = raw × gain + offset). The Recordings page shows the real storage of RBF files and the «lossless» indication derived from the metadata.

> To be verified in the proprietary FDA program: if it did not apply the channel offset, the values of 16-bit signals with offset would appear shifted.
