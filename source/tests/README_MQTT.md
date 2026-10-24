# MQTT regression tests

Run from `source`, with the application requirements installed:

```sh
python -m unittest discover -s tests -p test_mqtt.py -v
python -m unittest discover -s tests -p test_mqtt_api.py -v
```

`mqtt_peer.py` is a deterministic local TCP/TLS wire peer, not a production
broker. It exchanges real MQTT packets with unmodified Paho 2.1.0, including
3.1.1/5, authentication, subscription refusal, QoS 0/1/2 and retained messages.
Tests use temporary CA/server/client certificates. No public broker is contacted.

The API suite exercises actual acquisition, the formula subprocess and writing
workers: CSV/Parquet/RBF, exact integer text, nanosecond SourceTimestamp,
out-of-order originals/history/comparison, draining Stop, reconnect gaps,
partial archives after upstream failure, protocol guards and secret persistence.

The component UI test requires Node plus `npm install` in `tests` (or
`JSDOM_PATH` pointing at jsdom):

```sh
UI_SCRIPT=test_mqtt_ui.cjs python tests/test_ui_harness.py
```

It uses real local Flask/OPC UA services and the MQTT peer, with an actual
MQTT publisher thread, but simulated canvas/DOM layout. It is not a browser
pixel-rendering or Windows-native execution test. The OPC UA test server is
part of the shared harness; MQTT acquisition never reads that server.

Testing with the actual plant broker and packaged EXE on Windows remains
necessary. QoS tests do not imply exactly-once persistence across failures.
