param(
    [Parameter(Mandatory = $true)][string]$DistributionDirectory,
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [Parameter(Mandatory = $true)][ValidateSet('Overwrite', 'Remove', 'Default')][string]$Phase
)
$ErrorActionPreference = 'Stop'
$dist = (Resolve-Path -LiteralPath $DistributionDirectory).Path
$setupFiles = @(Get-ChildItem -LiteralPath $dist -Filter 'LinguaFloat-v*-windows-x64-setup-build*.exe' -File)
if ($setupFiles.Count -ne 1) { throw 'Expected exactly one installer in this isolated distribution directory.' }
$setup = $setupFiles[0].FullName
$target = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
$testRoot = 'C:\Users\Astatine\Desktop\codex\.scratch\lf163-installer-check'
$defaultTarget = Join-Path $env:LOCALAPPDATA 'Programs\LinguaFloat'
$registration = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{7AB27613-3319-49B5-A2EB-796458D9C0F6}_is1'
$validation = Join-Path $dist 'validation'
$formalConfig = Join-Path $env:APPDATA 'LinguaFloat\config.json'
$formalHash = (Get-FileHash -LiteralPath $formalConfig -Algorithm SHA256).Hash
$evidence = [ordered]@{ phase = $Phase; target = $target; real_requests = 0 }

function Invoke-TestInstaller([string]$Name, [string[]]$Additional) {
    $logPath = Join-Path $validation ($Name + '.log')
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        ('/DIR="' + $target + '"'), '/GROUP="LinguaFloat Distribution Test"',
        ('/LOG="' + $logPath + '"')) + $Additional
    $process = Start-Process -FilePath $setup -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(300000)) { throw "Timed out: $Name; leave the process for inspection." }
    return @{ exit_code = $process.ExitCode; log = $logPath }
}

if ($Phase -eq 'Default') {
    if ($target -ne $defaultTarget -or (Test-Path -LiteralPath $target) -or
        (Test-Path -LiteralPath $registration)) { throw 'Default test requires an absent default installation and registration.' }
    $evidence.install = Invoke-TestInstaller 'default-install' @('/TASKS=!desktopicon')
    if ($evidence.install.exit_code -ne 0) { throw 'Default install failed.' }
    $evidence.executable_present = Test-Path -LiteralPath (Join-Path $target 'LinguaFloat.exe')
    $evidence.desktop_absent = -not (Test-Path -LiteralPath 'C:\Users\Astatine\Desktop\LinguaFloat.lnk')
} else {
    if (-not $target.StartsWith($testRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing lifecycle mutation outside the dedicated installation test folder.'
    }
    $registered = (Get-ItemProperty -LiteralPath $registration).InstallLocation.TrimEnd('\')
    if ($registered -ne $target) { throw 'Registration belongs to a different installation.' }
}

if ($Phase -eq 'Overwrite') {
    $marker = Join-Path $target 'qa-unrelated-user-file.txt'
    [IO.File]::WriteAllText($marker, 'Preserve this generated unrelated test file.')
    $worker = Start-Process -FilePath (Join-Path $target 'document-runtime\python.exe') -ArgumentList @('-I', '-B', '-c', '"import time; time.sleep(120)"') -WindowStyle Hidden -PassThru
    try {
        Start-Sleep -Milliseconds 600
        if ($worker.HasExited) { throw 'Test document worker failed to start.' }
        $evidence.running_worker = Invoke-TestInstaller 'running-worker-protection' @()
        $evidence.running_worker.blocked = $evidence.running_worker.exit_code -ne 0
        if (-not $evidence.running_worker.blocked) { throw 'Installer did not block the live test worker.' }
    } finally {
        if (-not $worker.HasExited) { Stop-Process -Id $worker.Id }
    }
    $evidence.overwrite = Invoke-TestInstaller 'same-version-overwrite' @('/TASKS=desktopicon')
    if ($evidence.overwrite.exit_code -ne 0) { throw 'Overwrite failed.' }
    $evidence.unrelated_file_retained = (Get-Content -LiteralPath $marker -Raw) -eq 'Preserve this generated unrelated test file.'
} else {
    $profileMarker = Join-Path $validation 'uninstall-preserve-profile\LinguaFloat\config.json'
    New-Item -ItemType Directory -Path (Split-Path -Parent $profileMarker) -Force | Out-Null
    [IO.File]::WriteAllText($profileMarker, '{"acceptance_test":true}')
    $marker = Join-Path $target 'qa-unrelated-user-file.txt'
    if (-not (Test-Path -LiteralPath $marker)) {
        [IO.File]::WriteAllText($marker, 'Preserve this generated unrelated test file.')
    }
    $uninstaller = Join-Path $target 'unins000.exe'
    $logPath = Join-Path $validation ($Phase.ToLowerInvariant() + '-uninstall.log')
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + $logPath + '"')) -WindowStyle Hidden -PassThru
    if (-not $uninstall.WaitForExit(300000)) { throw 'Uninstall timed out; inspect the test process.' }
    $evidence.uninstall_exit_code = $uninstall.ExitCode
    $evidence.executable_removed = -not (Test-Path -LiteralPath (Join-Path $target 'LinguaFloat.exe'))
    $evidence.registration_removed = -not (Test-Path -LiteralPath $registration)
    $evidence.unrelated_file_retained = Test-Path -LiteralPath $marker
    $evidence.profile_file_retained = (Get-Content -LiteralPath $profileMarker -Raw) -eq '{"acceptance_test":true}'
    $evidence.desktop_removed = -not (Test-Path -LiteralPath 'C:\Users\Astatine\Desktop\LinguaFloat.lnk')
    $evidence.start_menu_removed = -not (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\LinguaFloat'))
    if ($uninstall.ExitCode -ne 0 -or -not $evidence.executable_removed -or
        -not $evidence.registration_removed -or -not $evidence.unrelated_file_retained -or
        -not $evidence.profile_file_retained) { throw 'Uninstall/data preservation checks failed.' }
}
$evidence.formal_config_unchanged = (Get-FileHash -LiteralPath $formalConfig -Algorithm SHA256).Hash -eq $formalHash
if (-not $evidence.formal_config_unchanged) { throw 'Formal configuration changed.' }
$evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $validation ('lifecycle-' + $Phase.ToLowerInvariant() + '.json')) -Encoding utf8
$evidence | ConvertTo-Json -Depth 8 -Compress
