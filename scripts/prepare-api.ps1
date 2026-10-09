$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$backendPath = Join-Path $PSScriptRoot "..\backend"
$venvPath = Join-Path $backendPath ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $backendPath "requirements.txt"
$envPath = Join-Path $backendPath ".env"

if (-not (Test-Path $pythonPath)) {
    Write-Host "Creating the backend Python environment..."
    & py -3 -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create a Python environment. Install Python 3.10+ and the Windows Python launcher."
    }
}

& $pythonPath -c "import fastapi, dotenv, jwt, bcrypt, cryptography, multipart, cv2, deepface, faster_whisper, tensorflow, tf_keras"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing backend requirements..."
    & $pythonPath -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Backend dependencies could not be installed. Check your network and Python installation."
    }
}

if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $backendPath ".env.example") $envPath
}

$envText = Get-Content $envPath -Raw
if ($envText.Contains("replace-with-a-long-random-secret") -or $envText.Contains("replace-with-base64-encoded-32-byte-key")) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $jwtBytes = New-Object byte[] 48
    $fileKeyBytes = New-Object byte[] 32
    $rng.GetBytes($jwtBytes)
    $rng.GetBytes($fileKeyBytes)
    $jwtSecret = [Convert]::ToBase64String($jwtBytes)
    $fileKey = [Convert]::ToBase64String($fileKeyBytes)
    $rng.Dispose()
    $envText = $envText.Replace("replace-with-a-long-random-secret", $jwtSecret)
    $envText = $envText.Replace("replace-with-base64-encoded-32-byte-key", $fileKey)
    Set-Content -Path $envPath -Value $envText -Encoding ASCII
    Write-Host "Generated local development secrets in backend\.env. Keep this file private."
}
