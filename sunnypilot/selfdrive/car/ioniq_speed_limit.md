# Ioniq factory-navigation speed limits

For `HYUNDAI_IONIQ_EV_LTD`, card polls the rooted head unit at
`192.168.43.2:38472` every 500 ms. A separately installed head-unit producer
supplies authenticated SL2 packets. The 32-byte shared key is read from
`/data/ioniq-speed-limit/key` and must never be committed. Missing credentials
disable the client. Existing positive CAN speed limits take priority.

Current limits and independently validated upcoming limits are published in
`carStateSP`. Replies must match an outstanding nonce and HMAC; current data
expires, and previews require freshness within one second. The producer is
responsible for direction, branch applicability and conditional-limit checks.
Existing Car/Map source priorities remain available.

Lower upcoming limits are selected using braking distance and response time.
Loss of a selected lower preview suppresses a higher recommendation until the
current limit changes, the driver disengages or the source policy changes.

## Acceleration advance

`/data/ioniq-speed-limit/acceleration_advance_m` contains a number in metres.
The resolver reloads it on its normal parameter update interval. The default
when absent is 25 metres; accepted values are 0 through 50. Zero disables
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
python -m unittest \
  sunnypilot.selfdrive.car.tests.test_ioniq_speed_limit \
  sunnypilot.selfdrive.car.tests.test_ioniq_speed_preview \
  sunnypilot.selfdrive.controls.lib.speed_limit.tests.test_ioniq_preview_resolver
```

The 25 m acceleration advance needs separate road verification; synthetic
resolver tests do not prove actuator timing or map accuracy.
