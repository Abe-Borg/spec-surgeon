"""Build the desktop app as one onedir runtime; run on native Windows.

The window is a web page: a local FastAPI server (uvicorn) around one ``DocumentTask``, the page built from
``frontend/`` into ``src/spec_surgeon/web/static`` (collected as data, so the bundle must be built after
``npm run build``), and a native WebView2 window through pywebview. pywebview resolves its Windows backend (Edge
WebView2 through pythonnet/clr) at run time, invisible to static analysis, so ``webview``, ``clr_loader`` and
``pythonnet`` are collected whole and ``clr`` is named; uvicorn, FastAPI, Starlette, anyio and python-multipart load
their pieces dynamically too. The WebView2 runtime itself ships with Windows 10/11 and is not bundled. The
installed-app smoke test (installed_smoke.py) is the gate that nothing needed is missing: it imports the backend,
serves the page and drives a run over HTTP.
"""
from pathlib import Path
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
windows = root / "packaging" / "windows"
icon = str(windows / "SpecSurgeon.ico")
manifest = str(windows / "app.manifest")
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
version_tuple = tuple(int(n) for n in version.split(".")) + (0,)
version_file = root / "build" / "windows-version.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r},
                   mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Abraham Borg'),
    StringStruct('FileDescription', 'Spec Surgeon'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('LegalCopyright', 'Copyright (c) 2026 Abraham Borg. MIT License (own code); AGPL-3.0 as a whole, with PyMuPDF.'),
    StringStruct('ProductName', 'Spec Surgeon'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)''', encoding="utf-8")

page = root / "src" / "spec_surgeon" / "web" / "static" / "index.html"
if not page.is_file():
    raise SystemExit(f"The window's page is not built ({page}): run `npm ci` and `npm run build` in frontend/ first.")

datas = collect_data_files("spec_surgeon", include_py_files=True,
                           includes=["schemas/**/*.xsd", "skill/**/*.md", "legal/*.txt", "ops/*.py", "web/static/**/*",
                                     "examples/specs/*.json", "examples/tutorial/*"])
datas += copy_metadata("anthropic", recursive=True)
for dist in ("fastapi", "uvicorn", "pywebview"):           # the diagnostics report their versions from the metadata
    try:
        datas += copy_metadata(dist)
    except Exception:  # noqa: BLE001 — a missing distribution fails the smoke test, not the build
        pass
pdf_datas, pdf_binaries, pdf_imports = collect_all("pymupdf")
datas += pdf_datas
binaries = list(pdf_binaries)
hiddenimports = pdf_imports + collect_submodules("anthropic")
hiddenimports += ["pythoncom", "pywintypes", "win32com.client", "win32cred", "win32gui", "win32timezone",
                  "regex", "regex._regex", "regex._regex_core",       # bounded pattern searches (ops/patterns.py)
                  "spec_surgeon.sandbox_runner", "spec_surgeon.analysis_runner",
                  "spec_surgeon.web.server", "spec_surgeon.web.shell", "spec_surgeon.web.updates",
                  "spec_surgeon.web.watch"]
# CredRead constructs LastWritten through a dynamic win32timezone import; without it a saved key cannot be read.

# The server: uvicorn picks its loop, protocol and lifespan classes by name; FastAPI/Starlette/anyio/python-multipart
# load pieces lazily.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["fastapi", "starlette", "anyio", "anyio._backends._asyncio", "multipart", "python_multipart"]
for name in ("fastapi", "starlette", "anyio"):
    hiddenimports += collect_submodules(name)
for name in ("python_multipart", "multipart"):           # python-multipart's import name changed in 0.0.13
    try:
        hiddenimports += collect_submodules(name)
    except Exception:  # noqa: BLE001
        pass

# The native window: pywebview's Windows backend is Edge WebView2 through pythonnet (``import clr``), resolved at run
# time — collect everything (the interop DLLs under webview/lib, Python.Runtime.dll, clr_loader's runtimes).
for name in ("webview", "clr_loader", "pythonnet"):
    try:
        w_datas, w_binaries, w_imports = collect_all(name)
        datas += w_datas
        binaries += w_binaries
        hiddenimports += w_imports
    except Exception:  # noqa: BLE001 — absent on a non-Windows build box; the Windows build has them
        pass
hiddenimports += ["clr"]

excludes = ["pytest", "docx", "jsonschema", "tkinter", "_tkinter"]


def analyze(entry):
    return Analysis([str(windows / entry)], pathex=[str(windows), str(root / "src")],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    excludes=excludes, noarchive=False)


gui = analyze("gui_entry.py")
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="SpecSurgeon",
              console=False, version=str(version_file), icon=icon, manifest=manifest, upx=False,
              contents_directory="_internal")
COLLECT(gui_exe, gui.binaries, gui.datas, name="SpecSurgeon", upx=False)
