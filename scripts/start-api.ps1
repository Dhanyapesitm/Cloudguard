$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
. (Join-Path $PSScriptRoot "prepare-api.ps1")

Write-Host "Starting the CloudGuard API at http://127.0.0.1:8000 ..."
& $pythonPath -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --app-dir $backendPath
if ($LASTEXITCODE -ne 0) {
    throw "The CloudGuard API stopped with an error. Review the API output above."
}
