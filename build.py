"""Build the VS Code extension from the single source of truth.

    python build.py            package vscode-extension/project-rewind-<version>.vsix
    python build.py --install  ...and install it into VS Code (needs the `code` command)
    python build.py --zip      ...and write ../project-rewind.zip for sharing

The engine (rewind.py + rewind_core/) is copied into vscode-extension/ at build time; never edit those copies.
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, ROOT)
from rewind_core import __version__  # noqa: E402


def sync_engine():
    for name in ("rewind.py", "graph_template.html"):  # graph_template.html: legacy copy from older builds
        target = os.path.join(EXT, name)
        if os.path.exists(target) and name != "rewind.py":
            os.remove(target)
    shutil.copy(os.path.join(ROOT, "rewind.py"), os.path.join(EXT, "rewind.py"))
    dest = os.path.join(EXT, "rewind_core")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "rewind_core"), dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def set_version():
    path = os.path.join(EXT, "package.json")
    pk = json.load(open(path, encoding="utf-8"))
    pk["version"] = __version__
    json.dump(pk, open(path, "w", encoding="utf-8"), indent=2)


def package():
    for f in os.listdir(EXT):
        if f.endswith(".vsix"):
            os.remove(os.path.join(EXT, f))
    npx = "npx.cmd" if os.name == "nt" else "npx"
    subprocess.run([npx, "--yes", "@vscode/vsce", "package", "--allow-missing-repository", "--skip-license"],
                   cwd=EXT, check=True)
    return os.path.join(EXT, f"project-rewind-{__version__}.vsix")


def make_zip(vsix):
    out = os.path.join(os.path.dirname(ROOT), "project-rewind.zip")
    skip = {"__pycache__", "node_modules", ".git"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(ROOT):
            dns[:] = [d for d in dns if d not in skip]
            for f in fns:
                full = os.path.join(dp, f)
                if f.endswith(".pyc") or (f.endswith(".vsix") and full != vsix):
                    continue
                z.write(full, os.path.join("project-rewind", os.path.relpath(full, ROOT)))
    return out


if __name__ == "__main__":
    sync_engine()
    set_version()
    vsix = package()
    print("built", vsix)
    if "--install" in sys.argv:
        subprocess.run(["code", "--install-extension", vsix, "--force"], check=True, shell=(os.name == "nt"))
    if "--zip" in sys.argv:
        print("zip", make_zip(vsix))
