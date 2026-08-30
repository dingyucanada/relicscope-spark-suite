# RelicScope synthetic demo media

Every pixel in this directory is generated. The fixture depicts an abstract
calibration vessel and does **not** contain a real cultural object, collection
record, museum image, user upload, or scientific measurement.

The `DEMO / SYNTHETIC` and `NO REAL ARTIFACT` labels are burned into every
image and video frame. PNG text metadata also records the provenance. The MP4
uses H.264 with `yuv420p` pixel format and fast-start metadata for broad browser
playback compatibility.

Files:

- `reference.png`: synthetic baseline view.
- `comparison.png`: synthetic comparison view with an explicit registration marker.
- `frames/frame_01.png` … `frame_06.png`: six bounded temporal samples.
- `synthetic_orbit.mp4`: three-second browser-playable rendering of those frames.
- `manifest.json` and `SHA256SUMS`: provenance and byte-level integrity records.

Verify the committed fixture after cloning:

```bash
make demo-media-check
```

Run the complete headless media path in a temporary, automatically removed
runtime (requires the project `.venv`):

```bash
make demo-media-smoke
```

Regeneration is optional. It requires the installed Python dependencies plus
`ffmpeg`; the finished fixture is already committed so Spark deployments do
not need media tooling:

```bash
make demo-media-generate
```

This fixture exists only for product demonstration and software acceptance. It
must not be described as an authentication result or real scientific evidence.
