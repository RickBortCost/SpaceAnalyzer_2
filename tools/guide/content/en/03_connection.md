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

Classic S7 with absolute addresses over TCP 102; enter the CPU IP, rack and slot. For S7-1200/1500 in classic mode PUT/GET must be enabled and non-optimised DBs used. Variables are not discovered automatically: define name, address (for example `DB1.DBD8`, `MW10`, `I0.0`) and type, then they appear in the catalogue. A symbolic mode (S7CommPlus) also exists, verifiable only on a real CPU.

## MQTT

![connection_mqtt](Connection popup for MQTT.)

Enter broker, port, version, QoS, topics to subscribe to, time base and JSON pointers. Acquisition is event-driven: every message received is processed. Space Fieldbus Analyzer subscribes to topics but does not publish or send commands. The catalogue shows only topics and fields actually received. JSON pointers can be given for timestamp and quality, and the time base (local reception or message timestamp).

## Simultaneous acquisition

After filling in the parameters of each protocol, press **Connect** once. In LIVE, in the Address Space panel, the Catalogue menu switches from one source to another without losing selection, buffer and charts. An unreachable source reports its own error without stopping the others.
