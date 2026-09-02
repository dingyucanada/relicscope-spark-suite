# RelicScope local engineering preview

The browser interface served at the repository root is the **Local Engineering
Preview**. It is an operator-facing development console for exercising the existing
image, video, local-knowledge, active-sensing, evidence, audit, and report APIs. It
is not a public website, a production collection portal, or a scientific instrument
readout.

## Safe local start

From a fresh clone, install the locked dependencies and start the deterministic
preview with:

```bash
make console-install
```

Then open exactly:

```text
http://127.0.0.1:8088
```

This route binds the application to the loopback address, keeps public model
fallbacks disabled, and starts in the visibly labelled offline deterministic-demo
profile. Do not change the listener to `0.0.0.0` or expose port 8088 to the public
internet.

On later runs, use `make console`. Dependency installation is permitted only by
the first command; the ordinary start command does not access a package registry.
The equivalent lower-level commands are `./scripts/reproduce-demo.sh --install`
and `./scripts/reproduce-demo.sh`.

## API-unavailable state

The preview checks the same-origin `/api/health` endpoint when the page loads. If
the browser cannot reach it, the page now keeps the scientific workspace visible
but shows a persistent `API OFFLINE` panel with the exact loopback command and URL.
API-dependent controls are disabled until a health check succeeds. The retry button
does not start a process or change the machine; it only repeats the health request.

Common causes are:

1. The HTML file was opened directly with `file://`. Close it and use the loopback
   URL above.
2. The service has not been started. Run the safe local-start command from the
   repository root.
3. The API responded but failed its health check. Read the existing start terminal,
   fix the reported local error, and select **重新检查 API**.

For a terminal-only check:

```bash
curl --fail http://127.0.0.1:8088/api/health
```

## Display and evidence boundaries

The label `Local Engineering Preview` is intentionally explicit: it identifies the
surface as a local engineering tool and avoids suggesting a finished public
product. Runtime, model, GPU, node, latency, evidence, and report state continue to
come from backend responses. The browser does not fabricate scientific
measurements or hardware verification.

The existing boundary remains in force:

- uploaded media are operator inputs;
- instrument measurements and included references are demonstration or replay data
  unless a separate governed workflow proves otherwise;
- image similarity and model observations cannot establish identity, authenticity,
  exact period, kiln, value, legal status, or conservation decision;
- evidence must retain source, calibration, uncertainty, model/runtime, and expert
  review context.

## Verification

The focused checks for this surface are:

```bash
node --check app/static/app.js
.venv/bin/python -m pytest -q tests/test_engineering_console.py
```

The repository's existing reproduction check also runs JavaScript syntax validation
before starting the loopback service.
