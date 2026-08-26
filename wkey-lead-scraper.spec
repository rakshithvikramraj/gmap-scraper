# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the Wkey Lead Scraper desktop app.

One spec, two shipped targets: macOS arm64 and Windows x64. The architecture
follows whichever Python runs the build, so CI picks it by choosing a runner
rather than by passing a flag — a local build on an Intel Mac still produces
an x86_64 app, CI just no longer ships one.

This spec deliberately does NOT bundle the Playwright browsers. PyInstaller
rewrites the signature of every Mach-O file it collects, which fails outright
on Chromium's signed nested .app. package.py adds them afterwards; run it
straight after this spec or the build ships unable to scrape.

APP_VERSION, if set, is written into the macOS Info.plist.

Built onedir, never onefile: onefile unpacks ~500MB to a temp directory on
every launch and reads to Windows antivirus as a self-extracting archive.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

VERSION = os.environ.get("APP_VERSION", "0.0.0-dev")

# Playwright is a Python package wrapping a Node driver that launches a
# browser. collect_all reaches the Python package and the Node driver; the
# browser itself is package.py's job.
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=list(pw_binaries),
    datas=list(pw_datas),
    hiddenimports=list(pw_hiddenimports),
    hookspath=[],
    runtime_hooks=[],
    # Test-only and build-only packages that would otherwise ride along if
    # they happen to be importable in the build environment.
    excludes=["pytest", "_pytest", "PyInstaller", "tkinter.test", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wkey Lead Scraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries are a reliable antivirus false positive.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Wkey Lead Scraper",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Wkey Lead Scraper.app",
        icon=None,
        bundle_identifier="dev.wkey.leadscraper",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # The window paints a hardcoded light palette. Letting macOS put
            # dark chrome around it looks broken, so opt out of dark mode.
            "NSRequiresAquaSystemAppearance": True,
        },
    )
