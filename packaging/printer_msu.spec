# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-dir Windows bundle with two executables.

    printer-msu.exe      console   check / print / apply-config / calibrate / ui
    printer-msu-ui.exe   windowed  the desktop console (implies `ui`)

Both come from packaging/launch.py and share one _internal/ folder. Build it
through packaging/build_release.py, which adds configs, board macros, docs and
the release manifest around the PyInstaller output and smoke-tests the exes.
"""

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # noqa: F821  (injected by PyInstaller)
ROOT = SPEC_DIR.parent
sys.path.insert(0, str(ROOT / "src"))
from printer_msu import __version__  # noqa: E402

BUNDLE_NAME = f"printer_msu-{__version__}-win64"

# Qt modules the app never imports (it uses QtCore, QtGui, QtWidgets). The
# per-module PySide6 hooks only bundle what is imported, so this list is a
# guard against accidental pulls, not the mechanism that keeps the bundle small.
QT_EXCLUDES = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtHttpServer", "PySide6.QtLocation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtNfc", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtWebView",
]

a = Analysis(
    [str(SPEC_DIR / "launch.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "libs" / "fmc-motion" / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        # reached only through imports deferred inside functions
        "printer_msu.app",
        "printer_msu.ui.print_console",
        "printer_msu.machine",
        "printer_msu.controllers.motion.duet.adapter",
        "printer_msu.controllers.motion.duet.client",
        "printer_msu.controllers.tool.duet_tool_controller",
        "printer_msu.controllers.motion.fmc_motion_adapter",
        "printer_msu.config.duet_config_gen",
        "fmc_motion",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=QT_EXCLUDES + ["tkinter", "pytest", "_pytest"],
    noarchive=False,
)


def _drop_vendor(toc):
    """The FMC006GE31 vendor DLL is not ours to redistribute and the
    controller is retired; the sim backend loads nothing. Drop it if any
    analysis step pulled it in."""
    kept = []
    for entry in toc:
        dest = str(entry[0]).replace("\\", "/")
        if "fmc_motion/vendor/" in dest:
            continue
        kept.append(entry)
    return kept


# Qt pieces the hooks add through plugin dependencies that a QtWidgets-only
# app never loads: the QML/Quick stack (pulled by the virtual-keyboard input
# context plugin) and Qt6Pdf (pulled by the qpdf image-format plugin). About
# 25 MiB unpacked. Kept on purpose: Qt6Svg (icon engine), Qt6Network (tls and
# network-information plugins), Qt6OpenGL and opengl32sw.dll (software
# rendering fallback for RDP sessions and VMs).
QT_TRIM_PREFIXES = (
    "qt6qml", "qt6quick", "qt6virtualkeyboard", "qtvirtualkeyboardplugin",
    "qt6pdf", "qpdf",
)


def _trim_qt(toc):
    kept = []
    for entry in toc:
        base = Path(str(entry[0])).name.lower()
        if base.startswith(QT_TRIM_PREFIXES):
            continue
        kept.append(entry)
    return kept


a.binaries = _trim_qt(_drop_vendor(a.binaries))
a.datas = _trim_qt(_drop_vendor(a.datas))

pyz = PYZ(a.pure)

exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="printer-msu",
    console=True,
    debug=False,
    strip=False,
    upx=False,
)

exe_ui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="printer-msu-ui",
    console=False,
    debug=False,
    strip=False,
    upx=False,
)

coll = COLLECT(
    exe_cli,
    exe_ui,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=BUNDLE_NAME,
)
