"""Build both entry points with a shared onedir runtime; run on native Windows."""
from pathlib import Path
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
windows = root / "packaging" / "windows"
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
                           includes=["schemas/**/*.xsd", "skill/**/*.md", "ops/*.py"])
datas += copy_metadata("anthropic", recursive=True)
pdf_datas, pdf_binaries, pdf_imports = collect_all("pymupdf")
datas += pdf_datas
hiddenimports = pdf_imports + collect_submodules("anthropic")
hiddenimports += ["pythoncom", "pywintypes", "win32com.client", "spec_surgeon.sandbox_runner"]

def analyze(entry):
    return Analysis([str(windows / entry)], pathex=[str(windows), str(root / "src")],
                    binaries=pdf_binaries, datas=datas, hiddenimports=hiddenimports,
                    excludes=["pytest", "docx", "jsonschema"], noarchive=False)

gui = analyze("gui_entry.py")
cli = analyze("cli_entry.py")
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="SpecSurgeon",
              console=False, version=str(version_file), upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="spec-surgeon",
              console=True, version=str(version_file), upx=False)
COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
        name="SpecSurgeon", upx=False)
