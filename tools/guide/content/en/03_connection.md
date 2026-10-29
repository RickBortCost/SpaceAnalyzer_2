# 3. Connecting to the data sources

The **Connessione** (Connection) button opens the «Data source and connection» popup. The source (and the other transport parameters) can be changed only while disconnected: the selector is disabled while connected.

![connection_opcua](Connection popup for OPC UA.)

| N. | Field |
|---|---|
| 1 | **Live data source**: OPC UA, ADS · Beckhoff TwinCAT, S7 · Siemens ISO-on-TCP, MQTT · TCP/TLS broker. |
| 2 | **Server endpoint** (for example `opc.tcp://127.0.0.1:4840`). |
| 3 | **Discover endpoints and policies**: queries the server and proposes the available policies. |
| 4 | **Security Policy** and (5) **Message Security**: None, Sign, SignAndEncrypt. In production use a signed or encrypted policy; certificates are managed in «Certificates and Application URI». |
| 6 | **Username / Password**. The password is «session only»: it is not saved. |
| 7 | **Simultaneous acquisition**: enables several protocols together, each with its own period. |
| 8 | **Read interval**: from 1 ms (ADS) to 2 s. It is a target: the actual period depends on server, network and PC and is shown in the Live diagnostics. |
| 9 | **Recent buffer / FFT**: 10 s, 30 s, 1 min, 2 min. |
| 10 | **Reconnect automatically** if the connection drops. |
| 11 | **Connect and read** and (12) **Disconnect**. |
| 13 | **Save settings and layout**. |

Live buffer limit: «Time + sample limit» or «Sample count only» (maximum 360,000 per signal; 2 million samples in memory overall). Record also saves the samples that have left the buffer.

## ADS · Beckhoff TwinCAT

![connection_ads](Connection popup for ADS.)

Two modes: direct ADS/TCP (independent client) or local TwinCAT with cyclic notifications. Enter the PLC IP and AMS Net ID, ports, the client AMS Net ID and the acquisition method (SumRead or others). In direct mode a route on the PLC towards the PC is needed and the PLC must publish its symbols. With local TwinCAT, cyclic notifications with the PLC timestamp are used (1, 2 and 5 ms are available for ADS only). Writing variables and RUN/STOP commands are not provided.

## S7 · Siemens

![connection_s7](Connection popup for S7.)

The «Acquisition mode» menu offers three modes, with separate selections and charts (only one mode is active at a time; to change it, disconnect and stop Record):

| Mode | Source of the variables | Acquisition |
|---|---|---|
| **Classic S7** | Catalogue of absolute DB/M/I/Q addresses defined by the user (up to 5000) | ReadVar polling over TCP 102 |
| **Symbolic S7** | Catalogue read from the CPU: DBs, structures and arrays (S7-1200/1500, S7CommPlus) | Aggregated reads over TCP 102 |
| **S7 UDP Direct Generic** | Payload fields defined by the user | The PLC sends telegrams cyclically to the PC |

**Classic.** Enter the CPU IP, rack and slot. For S7-1200/1500 PUT/GET must be enabled and non-optimised DBs used. Variables are not discovered automatically: define name, address (for example `DB1.DBD8`, `MW10`, `I0.0`) and type, then they appear in the catalogue (which can be imported and exported as JSON).

**Symbolic.** The catalogue is read from the CPU and shows DBs, structures and arrays, optimised ones too; no offsets or Siemens software on the PC are needed. The S7CommPlus driver is in beta and has not been tried on a real CPU: availability depends on model, firmware and permissions. In the tree on the left, next to every branch (DB, structure, array), a **Select branch** button appears: one click adds to the charts all variables of the branch that are not already there. If they exceed the maximum of 64 signals the selection is refused with a message stating how many there are: choose a sub-branch instead.

**UDP Direct Generic.** The PLC sends a telegram cyclically (Direct Generic format documented by iba); the pace is set by the PLC program. One valid telegram produces one sample for each selected field. The application reports sequence losses, timeouts (watchdog) and queue overflow by marking samples «Uncertain»; duplicates and out-of-order telegrams are counted in the diagnostics. With a UTC timestamp in the payload, the time of the samples is the PLC's.

### PLC timestamp and cycle coherence (classic and symbolic)

![connection_s7_time](«PLC timestamp and cycle coherence» section of the Connection popup.)

ReadVar and S7CommPlus do not provide the PLC time. To get it, the PLC program writes a 64-bit integer with the UTC time every cycle (for example from `RD_SYS_T`) and it is entered here:

| N. | Field |
|---|---|
| 1 | **PLC timestamp variable / symbol**: address `DB1.DBD300:LINT` in classic mode, symbol (for example `DB_Produzione.Orologio.Utc`) in symbolic mode. The type must be LINT, ULINT or LWORD. |
| 2 | **Unit of the value**: microseconds since 1970 UTC (LINT) or nanoseconds (LINT, LDT). |

The value is read together with every group of variables and saved as the source timestamp (`source_timestamp`) of all samples of the group. If the group needs several ReadVar requests, the timestamp is read before and after the other variables: if it changes, the values do not belong to the same PLC cycle and are marked «Uncertain» with the reason. A timestamp that cannot be read, has the wrong type or is outside 2000–2199 (for example nanoseconds entered as microseconds) marks the samples «Uncertain» but does not discard them. The chart axis remains the arrival time on the PC; the PLC time is in the recorded files.

> In testing, cycle coherence was verified with a simulated S7 server (classic mode). The symbolic mode has not been tried with a real CPU.

## MQTT

![connection_mqtt](Connection popup for MQTT.)

Enter broker, port, version, QoS, topics to subscribe to, time base and JSON pointers. Acquisition is event-driven: every message received is processed. Space Fieldbus Analyzer subscribes to topics but does not publish or send commands. The catalogue shows only topics and fields actually received. JSON pointers can be given for timestamp and quality, and the time base (local reception or message timestamp).

## Simultaneous acquisition

After filling in the parameters of each protocol, press **Connect** once. In LIVE, in the Address Space panel, the Catalogue menu switches from one source to another without losing selection, buffer and charts. An unreachable source reports its own error without stopping the others.
