param(
    [Parameter(Mandatory = $true)][string]$DistributionDirectory,
    [string]$Compiler = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
$distribution = (Resolve-Path -LiteralPath $DistributionDirectory).Path
$inputScript = Join-Path $distribution 'build-input\installer\Build.iss'
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw 'Inno Setup compiler not found. Install only after user authorization, or pass -Compiler.'
}
if (-not (Test-Path -LiteralPath $inputScript -PathType Leaf)) {
    throw 'Build the complete portable snapshot first using build_distribution.py.'
}
$existing = Get-ChildItem -LiteralPath $distribution -Filter '*-setup-*.exe' -File
if ($existing) { throw 'Installer already exists. Use a new distribution build directory.' }
& $Compiler /Qp $inputScript
if ($LASTEXITCODE -ne 0) { throw "Installer compiler failed: $LASTEXITCODE" }
$artifacts = Get-ChildItem -LiteralPath $distribution -File | Where-Object { $_.Extension -in '.zip', '.exe' }
$hashLines = foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($artifact.Name)"
}
[IO.File]::WriteAllLines((Join-Path $distribution 'SHA256SUMS.txt'), $hashLines, [Text.Encoding]::ASCII)
$artifacts | Select-Object Name, Length
