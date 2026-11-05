$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskVenvPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskVenvPython)) {
    $taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $taskBundledPython) {
        $taskBasePython = $taskBundledPython
    } else {
        $taskBasePython = (Get-Command python -ErrorAction Stop).Source
    }
    & $taskBasePython -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python-omgeving aanmaken mislukt.' }
}
& $taskVenvPython -m pip install -r (Join-Path $taskRoot 'requirements-local.txt')
if ($LASTEXITCODE -ne 0) { throw 'Afhankelijkheden installeren mislukt.' }
& $taskVenvPython (Join-Path $PSScriptRoot 'local_check.py')
exit $LASTEXITCODE
