"""Build the printer_msu release artifacts (Windows).

    python packaging/build_release.py [--skip-tests] [--no-smoke] [--no-zip]

Run it with the project venv's Python. Everything lands under dist/:

    wheels/                     printer_msu + fmc_motion wheels
    printer_msu-<ver>-win64/    one-dir bundle: printer-msu.exe (console),
                                printer-msu-ui.exe (windowed), _internal/,
                                configs/, firmware/duet3_6xd/*.g, examples/,
                                docs/, README, CHANGELOG, LICENSE,
                                RELEASE_MANIFEST.json
    printer_msu-<ver>-win64.zip

Order: tests -> wheels -> PyInstaller (printer_msu.spec) -> extras -> smoke
test of the frozen exes -> manifest -> zip. Any failure stops the build.
Nothing here tags, pushes or uploads; that procedure is in packaging/README.md.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "packaging" / "printer_msu.spec"
PY = sys.executable

# What this release was validated against (firmware/duet3_6xd/README.md and
# docs/PRD_Printer_App_v2.md section 4). Update when the board, the firmware
# or the baseline changes.
EXPECTED_MACHINE = {
    "board": "Duet 3 Mainboard 6XD v1.02",
    "firmware": "RepRapFirmware 3.6.1 (standalone)",
    "profile": "configs/duet_lab.json",
    "reference_baseline_s": {"preload": 1964.0, "stream": 1972.5},
}

BUNDLE_CONFIGS = ["duet_lab.json", "printer_sim_v2.json", "sim_machine.json"]
BUNDLE_FIRMWARE = ["config.g", "homeall.g", "homex.g", "homey.g", "homez.g",
                   "README.md"]
BUNDLE_DOCS = ["UI_Guide.md", "TODO.md"]
BUNDLE_TOP = ["README.md", "CHANGELOG.md", "LICENSE"]
SMOKE_GCODE = "square_20mm.gcode"
SMOKE_PROFILE = "printer_sim_v2.json"
UI_STARTUP_GRACE_S = 8.0


def log(msg: str) -> None:
    print(f"\n=== {msg}", flush=True)


def run(cmd, **kw) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    print("$", " ".join(cmd), flush=True)
    kw.setdefault("cwd", ROOT)
    return subprocess.run(cmd, check=True, **kw)


def versions() -> tuple[str, str]:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "libs" / "fmc-motion" / "src"))
    import fmc_motion
    import printer_msu

    return printer_msu.__version__, fmc_motion.__version__


def dist_version(name: str):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def git(*args: str):
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ steps
def step_tests() -> None:
    log("tests")
    run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"])


def step_wheels() -> list[Path]:
    log("wheels")
    wheels = DIST / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True)
    run([PY, "-m", "pip", "wheel", "--no-deps", "-w", wheels,
         ROOT / "libs" / "fmc-motion"])
    run([PY, "-m", "pip", "wheel", "--no-deps", "-w", wheels, ROOT])
    built = sorted(wheels.glob("*.whl"))
    for w in built:
        print("  +", w.name)
    return built


def step_pyinstaller(bundle_name: str) -> Path:
    log("PyInstaller")
    bundle = DIST / bundle_name
    shutil.rmtree(bundle, ignore_errors=True)
    run([PY, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", BUILD, SPEC])
    for exe in ("printer-msu.exe", "printer-msu-ui.exe"):
        if not (bundle / exe).is_file():
            raise SystemExit(f"build: {exe} missing from {bundle}")
    stray = [p for p in bundle.rglob("*") if "vendor" in p.parts and "fmc_motion" in p.parts]
    if stray:
        raise SystemExit(f"build: vendor DLL leaked into the bundle: {stray[:3]}")
    return bundle


def step_extras(bundle: Path) -> None:
    log("extras")

    def copy(src: Path, dst: Path) -> None:
        if not src.is_file():
            raise SystemExit(f"extras: {src} is missing")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("  +", dst.relative_to(bundle).as_posix())

    for name in BUNDLE_CONFIGS:
        copy(ROOT / "configs" / name, bundle / "configs" / name)
    for name in BUNDLE_FIRMWARE:
        copy(ROOT / "firmware" / "duet3_6xd" / name,
             bundle / "firmware" / "duet3_6xd" / name)
    for path in sorted((ROOT / "examples").glob("*.gcode")):
        copy(path, bundle / "examples" / path.name)
    for name in BUNDLE_DOCS:
        copy(ROOT / "docs" / name, bundle / "docs" / name)
    for name in BUNDLE_TOP:
        copy(ROOT / name, bundle / name)


def step_smoke(bundle: Path) -> None:
    """Drive the frozen exes from an unrelated working directory: help,
    preflight and a sim print must succeed, the job report must land in
    logs/ next to the exe, and the windowed exe must boot Qt and stay up."""
    log("smoke test (frozen executables)")
    cli = bundle / "printer-msu.exe"
    ui = bundle / "printer-msu-ui.exe"
    gcode = bundle / "examples" / SMOKE_GCODE
    profile = bundle / "configs" / SMOKE_PROFILE
    logs = bundle / "logs"
    shutil.rmtree(logs, ignore_errors=True)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    env.pop("PRINTER_MSU_LOG_DIR", None)

    with tempfile.TemporaryDirectory() as far_away:
        run([cli, "--help"], cwd=far_away, env=env, stdout=subprocess.DEVNULL)
        run([cli, "check", gcode, "--profile", profile], cwd=far_away, env=env)
        run([cli, "print", gcode, "--profile", profile, "--home-first"],
            cwd=far_away, env=env)
        reports = sorted(logs.glob("job-*.json"))
        if not reports:
            raise SystemExit(
                f"smoke: no job report under {logs}; app-anchored logs are broken")
        print("  report next to the exe:", reports[-1].relative_to(bundle).as_posix())

        proc = subprocess.Popen([str(ui), "--profile", str(profile)],
                                cwd=far_away, env=env)
        time.sleep(UI_STARTUP_GRACE_S)
        if proc.poll() is not None:
            err = logs / "printer-msu-ui.stderr.log"
            detail = err.read_text(errors="replace") if err.exists() else "(no stderr log)"
            raise SystemExit(
                f"smoke: UI exe exited early with code {proc.returncode}\n{detail}")
        proc.terminate()
        proc.wait(timeout=15)
        print(f"  UI exe started (offscreen) and stayed up {UI_STARTUP_GRACE_S:.0f} s")

    shutil.rmtree(logs, ignore_errors=True)  # ship a clean bundle


def step_manifest(bundle: Path, app_ver: str, lib_ver: str,
                  wheels: list[Path]) -> dict:
    log("manifest")
    hashed = {}
    for path in sorted(bundle.rglob("*")):
        rel = path.relative_to(bundle)
        if path.is_file() and "_internal" not in rel.parts \
                and path.name != "RELEASE_MANIFEST.json":
            hashed[rel.as_posix()] = sha256(path)
    internal = [p for p in (bundle / "_internal").rglob("*") if p.is_file()]
    manifest = {
        "app": "printer_msu",
        "version": app_ver,
        "fmc_motion_version": lib_ver,
        "bundle": bundle.name,
        "built_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "git": {
            "commit": git("rev-parse", "HEAD"),
            "describe": git("describe", "--tags", "--always", "--dirty"),
            "dirty": bool(git("status", "--porcelain")),
        },
        "toolchain": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pyside6": dist_version("PySide6"),
            "pyinstaller": dist_version("pyinstaller"),
        },
        "machine": EXPECTED_MACHINE,
        "wheels": {w.name: sha256(w) for w in wheels},
        "internal": {"files": len(internal),
                     "bytes": sum(p.stat().st_size for p in internal)},
        "sha256": hashed,
    }
    out = bundle / "RELEASE_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"  + {out.name} ({len(hashed)} hashed files, "
          f"_internal {manifest['internal']['bytes'] / 2**20:.0f} MiB)")
    return manifest


def step_zip(bundle: Path) -> Path:
    log("zip")
    base = DIST / bundle.name
    target = base.with_suffix(".zip")
    if target.exists():
        target.unlink()
    out = Path(shutil.make_archive(str(base), "zip", root_dir=DIST,
                                   base_dir=bundle.name))
    print(f"  {out.name}  {out.stat().st_size / 2**20:.1f} MiB  sha256 {sha256(out)}")
    return out


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-tests", action="store_true",
                    help="do not run pytest first (CI runs it separately)")
    ap.add_argument("--no-smoke", action="store_true",
                    help="skip driving the frozen exes")
    ap.add_argument("--no-zip", action="store_true", help="leave the folder unzipped")
    args = ap.parse_args(argv)

    if sys.platform != "win32":
        print("warning: the bundle targets Windows; building anyway", file=sys.stderr)
    app_ver, lib_ver = versions()
    bundle_name = f"printer_msu-{app_ver}-win64"
    print(f"printer_msu {app_ver}, fmc-motion {lib_ver}, "
          f"Python {platform.python_version()} ({PY})")
    DIST.mkdir(exist_ok=True)

    if not args.skip_tests:
        step_tests()
    wheels = step_wheels()
    bundle = step_pyinstaller(bundle_name)
    step_extras(bundle)
    if not args.no_smoke:
        step_smoke(bundle)
    manifest = step_manifest(bundle, app_ver, lib_ver, wheels)
    zip_path = None if args.no_zip else step_zip(bundle)

    log("done")
    print("  bundle :", bundle)
    for w in wheels:
        print("  wheel  :", w)
    if zip_path:
        print("  zip    :", zip_path)
    if manifest["git"]["dirty"]:
        print("  NOTE   : built from a DIRTY working tree; commit before tagging")
    return 0


if __name__ == "__main__":
    sys.exit(main())
