# Ioniq factory-navigation speed limits

For `HYUNDAI_IONIQ_EV_LTD`, card subscribes to the rooted head unit at
`192.168.43.2:38472`. A separately installed head-unit producer pushes
HMAC-authenticated SL3 navigation events. The client renews its subscription and
clock lease every 500 ms; navigation data is received independently of that
renewal interval. The 32-byte shared key is read from
`/data/ioniq-speed-limit/key` and must never be committed. Missing credentials
disable the client. Existing positive CAN speed limits take priority.

Current limits and independently validated upcoming limits are published in
`carStateSP`. Acknowledgements and events must match the client session,
subscription token and remote session. Sequence numbers reject replayed events.
The client bounds the devices' monotonic-clock offset and rejects delayed
packets and expired source observations. Current data has a 1.5-second maximum
age; preview positions must be fresh within one second. Renewing the subscription
cannot refresh old navigation data. The producer is responsible for direction,
branch applicability and conditional-limit checks. Existing Car/Map source
priorities remain available. SL1 and SL2 responses are not accepted.

Lower upcoming limits are selected using braking distance and response time.
Loss of a selected lower preview suppresses a higher recommendation until the
current limit changes, the driver disengages or the source policy changes.

## Acceleration advance

`/data/ioniq-speed-limit/acceleration_advance_m` contains a number in metres.
The resolver reloads it on its normal parameter update interval. The default
when absent is 25 metres; accepted values are 0 through 200. Zero disables
advance, and malformed or unreadable configuration disables it.

While moving, a fresh applicable higher preview becomes the selected target
within that distance of the map boundary, accounting for sample age. Its
resolver distance is zero, allowing immediate target adoption. This may cause
physical acceleration before the boundary; the distance is not a measured
actuator delay and does not establish the physical sign position.

The configuration is a file parameter, not a settings-screen control. It does
not enable speed assistance or change cruise acceleration limits. Preview
selection still requires a valid current limit.

## Verification

Run the client, helper and resolver tests from the configured project runtime:

```sh
python -m pytest \
  sunnypilot/selfdrive/car/tests/test_ioniq_speed_limit.py \
  sunnypilot/selfdrive/car/tests/test_ioniq_speed_preview.py \
  sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_ioniq_preview_resolver.py
```

The configured acceleration advance needs separate road verification; synthetic
resolver tests do not prove actuator timing or map accuracy.
