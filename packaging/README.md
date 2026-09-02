# Packaging and release procedure

`printer_msu` ships as a **system release**: the app, the machine profile, the
board configuration and the firmware version are one unit, because the app
cross-checks the board against the profile on every connect. A release is
three artifacts, all produced by one command:

| Artifact | What it is | Who it is for |
|---|---|---|
| `dist/wheels/*.whl` | `printer_msu` and `fmc_motion` wheels | maintainers, CI, anyone with Python |
| `dist/printer_msu-<ver>-win64.zip` | PyInstaller one-dir bundle with `printer-msu.exe` (console) and `printer-msu-ui.exe` (windowed), plus configs, board macros, docs | the operator: unzip, double-click |
| `RELEASE_MANIFEST.json` (inside the bundle) | versions, git commit, toolchain, expected board and firmware, SHA-256 of every shipped config, macro and exe, baseline durations | verification on the machine |

## Files here

- `printer_msu.spec` — the PyInstaller spec. One analysis, two executables,
  shared `_internal/`. Drops the FMC006GE31 vendor DLL if anything pulls it in
  (not ours to redistribute; the simulator does not need it).
- `launch.py` — the frozen entry point. `printer-msu-ui.exe` implies the
  `ui` subcommand; a windowed exe has no console, so its stderr goes to
  `logs/printer-msu-ui.stderr.log` next to the job reports.
- `build_release.py` — the driver: tests, wheels, PyInstaller, extras, smoke
  test, manifest, zip.

## Build

```bash
pip install -e libs/fmc-motion[dev] -c constraints.txt
pip install -e ".[ui,sim,dev,build]" -c constraints.txt
python packaging/build_release.py
```

The smoke test drives the frozen executables from an unrelated working
directory: `--help`, a preflight and a simulated print must pass, the job
report must appear in `logs/` next to the exe, and the windowed exe must boot
Qt offscreen and stay up. The build stops on any failure and never tags,
pushes or uploads.

## Release checklist

1. **Working tree clean on `main`**, `CHANGELOG.md` has the version's section
   including *Known limitations*, `__version__` bumped in
   `src/printer_msu/__init__.py` (and `libs/fmc-motion/src/fmc_motion/__init__.py`
   if the library changed). `EXPECTED_MACHINE` in `build_release.py` still
   matches the board, firmware and baseline.
2. `python packaging/build_release.py` — green end to end. The manifest must
   not say `"dirty": true`.
3. **Verify on the lab machine** with the zip, not the checkout: unzip to a
   writable folder (not Program Files), run
   `printer-msu-ui.exe --profile configs/duet_lab.json`, **Connect** and
   confirm the drift check reads CLEAN, home all, run the reference Cura file
   and compare the board-measured duration with `reference_baseline_s` in the
   manifest (within a few percent). Archive the job report under
   `docs/test-records/<date>/`.
4. Tag and push:

   ```bash
   git tag -a v1.0.0 -m "printer_msu 1.0.0"
   ```

   ```bash
   git push origin main --tags
   ```

5. The `v*` tag triggers the CI release job, which rebuilds the bundle on a
   clean Windows runner and opens a **draft** GitHub Release with the zip,
   the wheels and the manifest attached. Paste the CHANGELOG section (with
   *Known limitations*) as the release body, attach the verification job
   report, then publish.

## What the operator gets

```
printer_msu-<ver>-win64/
  printer-msu.exe         console: check / print / apply-config / calibrate plane / ui
  printer-msu-ui.exe      the desktop console
  _internal/              PyInstaller runtime (do not touch)
  configs/                duet_lab.json (the machine), printer_sim_v2.json + sim_machine.json (simulator)
  firmware/duet3_6xd/     config.g, homeall.g, home[xyz].g and the wiring README
  examples/               sample G-code
  docs/                   UI_Guide.md (operator guide), TODO.md (open safety items)
  README.md CHANGELOG.md LICENSE RELEASE_MANIFEST.json
  logs/                   created on first run: job-*.json reports and .log twins
```

Reports stay under the app folder. `PRINTER_MSU_LOG_DIR` overrides that; a
read-only app folder falls back to the per-user directory with a warning.

## Not in the bundle, on purpose

- The FMC006GE31 vendor DLL and the vendor documents under `docs/`
  (redistribution rights unconfirmed; the controller is retired).
- The host-side firmware tools (`stream_gcode.py`, `duet_send.py`,
  `cura_to_msu.py`): they need a Python interpreter, which the bundle does
  not carry. Use them from a checkout.
