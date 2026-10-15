"""Build both entry points with a shared onedir runtime; run on native Windows.

The window is Qt (PySide6-Essentials). PyInstaller's own PySide6 hooks collect what QtCore/QtGui/QtWidgets need
(the windows platform plugin, styles incl. windows11, image formats, the SVG icon engine); the filter below drops
what a widgets-only app never loads (software OpenGL, the D3D shader compiler, Qt Quick/QML, PDF, TLS, translations)
to keep the installer small. The installed-app smoke test (installed_smoke.py) is the gate that nothing needed
was dropped.
"""
from pathlib import Path
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
windows = root / "packaging" / "windows"
icon = str(windows / "SpecSurgeon.ico")
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
    StringStruct('ProductName', 'Spec Surgeon'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)''', encoding="utf-8")

datas = collect_data_files("spec_surgeon", include_py_files=True,
                           includes=["schemas/**/*.xsd", "skill/**/*.md", "ops/*.py", "ui/resources/*"])
datas += copy_metadata("anthropic", recursive=True)
pdf_datas, pdf_binaries, pdf_imports = collect_all("pymupdf")
datas += pdf_datas
hiddenimports = pdf_imports + collect_submodules("anthropic")
hiddenimports += ["pythoncom", "pywintypes", "win32com.client", "win32cred", "win32timezone", "spec_surgeon.sandbox_runner",
                  "spec_surgeon.ui.app", "PySide6.QtSvg"]
# CredRead constructs LastWritten through a dynamic win32timezone import; without it a saved key cannot be read.
excludes = ["pytest", "docx", "jsonschema", "tkinter", "_tkinter",
            "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtOpenGL",
            "PySide6.QtOpenGLWidgets", "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtDesigner"]

DROP_NAMES = ("opengl32sw.dll", "d3dcompiler_47.dll", "Qt6Pdf", "Qt6Quick", "Qt6Qml", "Qt6OpenGL", "Qt6Network",
              "Qt6VirtualKeyboard")
DROP_DIRS = ("PySide6/translations/", "PySide6/qml/", "PySide6/plugins/imageformats/qpdf",
             "PySide6/plugins/vectorimageformats/", "PySide6/plugins/platforms/qdirect2d",
             "PySide6/plugins/tls/", "PySide6/plugins/networkinformation/", "PySide6/plugins/platforminputcontexts/",
             "PySide6/plugins/qmltooling/", "PySide6/plugins/sqldrivers/", "PySide6/plugins/designer/")


def keep(entry):
    dest = entry[0].replace("\\", "/")
    name = dest.rsplit("/", 1)[-1]
    return not (name.startswith(DROP_NAMES) or dest.startswith(DROP_DIRS))


def analyze(entry):
    a = Analysis([str(windows / entry)], pathex=[str(windows), str(root / "src")],
                 binaries=pdf_binaries, datas=datas, hiddenimports=hiddenimports,
                 excludes=excludes, noarchive=False)
    a.binaries = [b for b in a.binaries if keep(b)]
    a.datas = [d for d in a.datas if keep(d)]
    return a


gui = analyze("gui_entry.py")
cli = analyze("cli_entry.py")
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="SpecSurgeon",
              console=False, version=str(version_file), icon=icon, upx=False,
              contents_directory="_internal")
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="spec-surgeon",
              console=True, version=str(version_file), icon=icon, upx=False,
              contents_directory="_internal")
COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
        name="SpecSurgeon", upx=False)
