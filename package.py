#!/usr/bin/env python3
"""Finish a PyInstaller build into a distributable archive.

PyInstaller cannot carry Playwright's browsers itself. It treats every Mach-O
file it collects as its own binary and rewrites the signature, which fails
outright on Chromium's signed nested .app. So the browsers go in afterwards,
and on macOS three details each break the download in their own way:

  * They must land in Contents/Resources, never Contents/Frameworks. macOS
    reads Frameworks subdirectories as nested code, and codesign then refuses
    to seal Playwright's marker files (.links, DEPENDENCIES_VALIDATED).
  * The bundle must be re-signed after they land. Otherwise the seal is stale
    and a quarantined download reports "damaged" -- which, unlike the ordinary
    unsigned warning, offers the user no way through.
  * ditto, not cp and not zip. Chromium's framework contains symlinks that
    plain zip flattens into copies, and the browser will not start.

Run after `pyinstaller club-scraper.spec`:

    python package.py --browsers build/pw-browsers --name club-scraper-macos-arm64
"""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Club Scraper"
BROWSER_DIR = "ms-playwright"

IS_MAC = sys.platform == "darwin"


def fail(message: str) -> None:
    print(f"package.py: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_root(dist: Path) -> Path:
    """The tree PyInstaller produced for this platform."""
    root = dist / (f"{APP_NAME}.app" if IS_MAC else APP_NAME)
    if not root.is_dir():
        fail(f"no build at {root} - run pyinstaller club-scraper.spec first")
    return root


def browser_target(root: Path) -> Path:
    """Where the browsers go, and where _MEIPASS will find them."""
    if IS_MAC:
        return root / "Contents" / "Resources" / BROWSER_DIR
    return root / "_internal" / BROWSER_DIR


def copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if IS_MAC:
        # ditto keeps symlinks, permissions and extended attributes; the
        # copies shutil makes lose the last of those.
        subprocess.run(["ditto", str(source), str(target)], check=True)
    else:
        shutil.copytree(source, target, symlinks=True)


def link_into_meipass(root: Path) -> None:
    """Bridge Contents/Frameworks -> Contents/Resources for _MEIPASS lookups.

    Only macOS needs this: elsewhere the browsers already sit in _MEIPASS.
    """
    link = root / "Contents" / "Frameworks" / BROWSER_DIR
    if link.is_symlink() or link.exists():
        return
    link.symlink_to(Path("..") / "Resources" / BROWSER_DIR)


def resign(root: Path) -> None:
    """Re-seal the bundle, then prove the seal is valid.

    Without --deep: deep re-signs nested code and trips over the same
    Playwright marker files that Frameworks placement did.
    """
    subprocess.run(["codesign", "--force", "--sign", "-", str(root)], check=True)
    verify = subprocess.run(
        ["codesign", "--verify", "--strict", str(root)],
        capture_output=True, text=True,
    )
    if verify.returncode != 0:
        fail("bundle seal invalid after signing - a download would report "
             f"'damaged':\n{verify.stderr.strip()}")
    print("package.py: signature verified")


def archive(root: Path, dist: Path, name: str) -> Path:
    out = dist / f"{name}.zip"
    out.unlink(missing_ok=True)
    if IS_MAC:
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(root), str(out)],
            check=True,
        )
    else:
        shutil.make_archive(str(out.with_suffix("")), "zip",
                            root_dir=str(dist), base_dir=root.name)
    return out


def check_browser_present(root: Path) -> None:
    """A build that silently shipped no browser looks fine until first scrape.

    Both builds matter. The app scrapes headless by default, which drives
    chromium_headless_shell; "Show the browser" drives full chromium. Shipping
    one without the other breaks exactly one of the two modes, and only once a
    teammate tries it.
    """
    target = browser_target(root)
    found = sorted(p.name for p in target.glob("chromium*")) if target.is_dir() else []
    for needed in ("chromium-", "chromium_headless_shell-"):
        if not any(name.startswith(needed) for name in found):
            fail(f"no {needed}* under {target} - the app would ship unable to "
                 f"scrape. Found: {found or 'nothing'}")
    print(f"package.py: browsers present ({', '.join(found)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browsers", required=True, type=Path,
                        help="Playwright browser install to ship inside the app")
    parser.add_argument("--name", required=True,
                        help="archive name without the .zip suffix")
    parser.add_argument("--dist", default=Path("dist"), type=Path)
    args = parser.parse_args()

    if not args.browsers.is_dir():
        fail(f"no browsers at {args.browsers} - run playwright install first")

    root = build_root(args.dist)
    print(f"package.py: {platform.system()} {platform.machine()} -> {root}")

    copy_tree(args.browsers, browser_target(root))
    if IS_MAC:
        link_into_meipass(root)
        resign(root)
    check_browser_present(root)

    out = archive(root, args.dist, args.name)
    size = out.stat().st_size / (1024 * 1024)
    print(f"package.py: wrote {out} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
