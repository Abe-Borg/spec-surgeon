param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = "Stop"
$work = Join-Path $env:RUNNER_TEMP "Spec Surgeon installer validation"
$installDir = Join-Path $work "Installed app with spaces"
New-Item -ItemType Directory -Force -Path $work | Out-Null
python packaging/windows/prepare_smoke.py $work
if ($LASTEXITCODE -ne 0) { throw "Could not prepare installer fixtures" }

function Run-Checked([string]$File, [string[]]$Arguments, [int]$TimeoutSeconds = 240) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        taskkill /PID $process.Id /T /F
        throw "Process timed out: $File"
    }
    if ($process.ExitCode -ne 0) { throw "Process failed ($($process.ExitCode)): $File" }
}

Run-Checked $Installer @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                        "/CURRENTUSER", "/DIR=`"$installDir`"", "/LOG=`"$work\install.log`"")
$gui = Join-Path $installDir "SpecSurgeon.exe"
$cli = Join-Path $installDir "spec-surgeon.exe"
$uninstaller = Join-Path $installDir "unins000.exe"
foreach ($file in @($gui, $cli, $uninstaller)) {
    if (-not (Test-Path $file)) { throw "Installer did not create $file" }
}
$shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Spec Surgeon\Spec Surgeon.lnk"
if (-not (Test-Path $shortcut)) { throw "Start menu shortcut missing" }

# The installed app must work without any Python installation on PATH or PYTHONPATH.
$savedPath = $env:PATH
$savedPythonPath = $env:PYTHONPATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONPATH = $null
    $help = & $cli --help
    if ($LASTEXITCODE -ne 0 -or -not ($help -match "Spec Surgeon")) { throw "Installed CLI failed" }
    Run-Checked $gui @("--installer-smoke-test", "`"$work\request.json`"")
    $reportPath = Join-Path $work "smoke-result.json"
    if (-not (Test-Path $reportPath)) { throw "Installed app did not produce a smoke-test report" }
    Get-Content $reportPath
    $result = Get-Content $reportPath -Raw | ConvertFrom-Json
    if (-not $result.ok) { throw $result.error }

    # Running the installer again exercises an in-place reinstall with the same application ID.
    Run-Checked $Installer @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                            "/CURRENTUSER", "/DIR=`"$installDir`"")
    Run-Checked $uninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if (Test-Path $gui) { throw "Uninstall left the executable behind" }
    if (Test-Path $shortcut) { throw "Uninstall left the Start menu shortcut behind" }
    if (-not (Test-Path (Join-Path $work "Results"))) { throw "Uninstall removed user results" }
} finally {
    $env:PATH = $savedPath
    $env:PYTHONPATH = $savedPythonPath
}
