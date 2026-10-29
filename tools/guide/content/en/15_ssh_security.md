# 15. SSH Debug, command-line tools, security

## SSH Debug

![ssh](SSH Debug page (Tools menu).)

Console to a remote target, independent of acquisition. The whole SSH API requires the application token.

| N. | Control |
|---|---|
| 1 | **Host** (IP and port). |
| 2 | **User**. |
| 3 | **Authentication**: password or key. |
| 4 | **Connect**. |
| 5 | **Mode**: Command or shell. In command mode the PTY option is not available; Ctrl+C and interactive input are available in shell mode. |
| 6 | **Editor** of the command or script (local scripts can also be opened), with maximum duration. |
| 7 | **Run** (and Stop). |
| 8 | **Export** the output to TXT. |

## Offline tools (--tool)

The launcher/EXE accepts non-interactive commands for primary archive management and export:

```
--tool inspect <file.sac>
--tool recover <file.sac.part> [output]
--tool export-fda|export-rbf <original.parquet> <nodes.json> [max_bytes]
```

`inspect` verifies a primary archive; `recover` rebuilds a file after an abnormal stop; `export-fda` and `export-rbf` convert an original Parquet to FDA/DAS4 or RBF.

## Security and known limits

| Topic | Note |
|---|---|
| Access | Only from 127.0.0.1/localhost; token for every modifying request and for the SSH API; security headers (no-store, nosniff, CSP). |
| Read-only | Space Fieldbus Analyzer does not write to the PLC (ADS without writing or RUN/STOP, MQTT without PUBLISH) nor to databases: InfluxDB and PostgreSQL are read with read-only queries. |
| Passwords and tokens | The PostgreSQL password and InfluxDB token stay in the service and are not sent back to the interface nor written in exported files. |
| Symbolic S7 | Requires a real S7-1200/1500 CPU for verification. |
| Native ADS | Requires Windows with TwinCAT. |
| Read period | The value set is a target; the real period is shown in the Live diagnostics. |
| FFT | Requires regular sampling; with irregular sources analyse a recorded file. |
| InfluxDB and PostgreSQL | Verified with test servers and a temporary PostgreSQL; try the connection to your own instance. |
| ibaPDA DAT | Experimental reading: unnamed channels, reconstructed from the file structure. |
| FDA | To be verified in the proprietary FDA program: the channel offset of exact 16-bit signals. |

## Troubleshooting

| Symptom | Action |
|---|---|
| Does not connect | Check endpoint, Security Policy and Message Security consistent with the server and, for OPC UA, the certificates («Certificates and Application URI»). |
| The source selector is grey | Press Disconnect: the source can be changed only while disconnected. |
| Markers without values | Resume Play and pause again: the original samples must still be in the buffer. For long intervals use Record. |
| FFT rejected | Check distinct X1/X2 markers, chart paused, period regularity (tolerance) and at least 16 samples. |
| PostgreSQL query rejected | Only single SELECT, WITH, VALUES or TABLE statements are accepted; the message indicates the character of the error. |
| Closing | Use Shut down and wait for background saves. |

Reference documentation in the repository: `source/GUIDA_*.md`, `CHANGELOG.md`.
