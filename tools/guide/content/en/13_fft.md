# 13. FFT and export

The FFT opens from **FFT / Export**. On a live chart it must be paused and two distinct X1/X2 markers placed; on recordings the two markers are required. The analysis uses the original samples, not the reduced drawing.

![fft_params](Analysis window: FFT and export parameters.)

| N. | Parameter | Meaning |
|---|---|---|
| 1 | Signal / Interval | Signal to analyse; X1–X2 mode or «Live · sliding window». |
| 2 | Window | Hann (general), Flat-top (amplitude), Blackman-Harris, Rectangular. |
| 3 | Resolution | Samples per block, desired Δf (Hz) or minimum block duration (0 = automatic). |
| 4 | Overlap | 0, 50, 75 or 90 %. |
| 5 | Average | Energy, None (last block), Exponential energy (τ), Max Hold. |
| 6 | Quantity | Peak amplitude, RMS amplitude, Power, PSD, ASD; unit of the original signal. |
| 7 | Quality and gaps | Exclude invalid blocks or Reject invalid interval; period tolerance 0.01–25 %. |
| 8 | Fundamental F1 | Auto (dominant AC peak) or Manual; harmonic search ± %; RMS band from/to. |
| 9 | Remove DC mean | Subtracts the DC component. |
| 10 | Run FFT | Calculates and shows the spectrum. |

![fft_result](FFT result on the «Vibrazione» signal (synthetic 1 kHz recording with components at 50, 150 and 250 Hz).)

The spectrum shows frequency in Hz and the level in the chosen quantity, with dashed lines for F1–H5 (fundamental and harmonics up to the fifth). In the example the dominant peak is at about 50 Hz, with harmonics at about 150 and 250 Hz. Controls: Curve or Spectral lines view, F1–H5 view, From/To Hz range, Y log / X log, Last block, Max Hold, Autoscale; Save spectrum PNG, Export spectrum CSV and Export harmonics CSV. The harmonics table compares expected and measured frequencies with the deviation and the outcome.

**Sampling regularity.** The FFT checks the regularity of timestamps. With irregular sources (for example an OPC UA read at 100 ms with a not perfectly regular period) the analysis of a live interval may answer «no valid continuous Good block»: it is better to record and analyse the file, or to use sources with regular sampling (for example cyclic ADS). The maximum tolerance that can be set is 25 %.

## Interval export

Below the parameters, **Export interval** saves the chart's signals between X1 and X2 as CSV or Apache Parquet and makes the «Download file» link available. If the requested initial part has left the original buffer, the service reports that the interval is unavailable instead of exporting only its tail.
