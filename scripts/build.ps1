$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OriginalPath = $env:PATH

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment missing. Create .venv and install dependencies first."
}

Push-Location $ProjectRoot
try {
    # Codex's bundled Poppler ships an incompatible generic ICU DLL. Do not let
    # PyInstaller mistake it for the Windows ICU dependency used by Qt.
    $env:PATH = (($OriginalPath -split [IO.Path]::PathSeparator) | Where-Object {
        $_ -notmatch '\\native\\poppler\\Library\\bin$'
    }) -join [IO.Path]::PathSeparator
    & $Python scripts\prepare_assets.py
    if ($LASTEXITCODE -ne 0) { throw "Asset generation failed." }
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name LinguaFloat `
        --icon photo\app.ico `
        --add-data "photo;photo" `
        --collect-all keyring `
        --collect-all uiautomation `
        --collect-data qtawesome `
        --collect-data rapidocr `
        --collect-all onnxruntime `
        --hidden-import rapidocr.inference_engine.onnxruntime.main `
        --paths src `
        src\linguafloat\__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed. Check the errors above."
    }
}
finally {
    $env:PATH = $OriginalPath
    Pop-Location
}

Write-Host "GUI-only internal build: $ProjectRoot\dist\LinguaFloat\LinguaFloat.exe. Use build_documents.ps1 -Publish for the complete supported exe bundle."
