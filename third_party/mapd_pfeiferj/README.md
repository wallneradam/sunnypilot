# MAPD implementation by pfeiferj
https://github.com/pfeiferj/openpilot-mapd/releases/

## Road-number continuity

The bundled binary is upstream v1.12.0 with `road-ref-continuity.patch` applied.
At junctions, valid exact or shared road references are preferred before street
names. Existing one-way and connection checks remain in effect.

`road-ref-build.json` records the source commit, toolchain, flags and hashes.
The patch includes regression tests and the Dömsöd map fixture. To rebuild,
extract the recorded upstream commit, apply the patch with `patch -p1`, then run:

```sh
go test ./...
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -ldflags="-extldflags=-static -s -w" -o mapd
```

Set `MAPD_REPLAY_TILE` to an offline map tile to run the full downloaded-map replay.
The installer base version remains v1.12.0.
