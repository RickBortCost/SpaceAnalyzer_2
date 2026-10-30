# 9. The Recordings page

![recordings](Recordings page: folder list, filters and analysis chart.)

| N. | Control |
|---|---|
| 1 | **Change…**: sets the recordings folder; «Refresh» rereads the list. |
| 2 | **Open file from PC…**: imports files from any location (multiple selection, also of different formats). |
| 3 | **Search** by name, machine or session. |
| 4 | **Format**: CSV, Parquet, FDA DAS4, RBF, Scope (.svdx), DAT/PDA3, comparisons; minimum and maximum duration filters in seconds are also available. |
| 5 | **File list**: selecting a file shows its signals. |
| 6 | **Open in chart** (or «Whole file»): the signals appear in the chosen chart. |
| 7 | **Target chart**. |
| 8 | **Layout** of the charts. |

For DAT files without a manifest the UTC offset of the internal calendar can be given and consecutive DAT files joined only with verified continuity. Several files can be opened, several charts used and the same functions as the LIVE page: markers, zoom, FFT/Export, formulas, collapse to bar and reordering. «Close file» and «Close all» free the charts without deleting anything.

![recording_markers](Recording opened in the chart with X1 and X2 markers and the table of values.)

The file is read in blocks: Parquet skips row groups outside the interval and the new FDA files created by the app allow direct access to the requested interval. The chart uses bounded overviews that keep extremes and quality; FFT, formulas and export always use the original samples. The «Recording details» panel reports metadata, From/To interval and signals.

**DAT files written by ibaPDA.** They open in Recordings and can be converted (experimental reading): values are read without rounding, but names and units are not available and channels are called «Channel 01», «Channel 02»… reconstructed from the file structure; if the assignment is ambiguous the file is rejected instead of mixing channels. The time comes from the file's internal calendar (UTC offset given with «Open file from PC…», otherwise the PC time zone).

With synchronisation active, charts of the same file share the X interval, so cursor and grid fall on the same point. Error messages at the bottom close with a click and in any case after 20 seconds; the timeout message reports the request address and the recordings list has a 240 s limit.
