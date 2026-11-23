[CmdletBinding()]
param(
    [string] $SolidWorksPath,
    [string] $InstallRoot = 'D:\SolidWorksSketchTools'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $root 'Setup.log'
try {
    if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess) {
        throw 'Run Install.cmd using 64-bit Windows PowerShell.'
    }
    if ([string]::IsNullOrWhiteSpace($SolidWorksPath)) {
        $registered = Get-ChildItem 'HKLM:\SOFTWARE\SolidWorks' -ErrorAction SilentlyContinue |
            Where-Object { $_.PSChildName -match '^SOLIDWORKS [0-9]{4}$' } |
            Sort-Object PSChildName -Descending
        foreach ($version in $registered) {
            $setup = Get-ItemProperty -LiteralPath (Join-Path $version.PSPath 'Setup') -ErrorAction SilentlyContinue
            $candidate = $setup.'SolidWorks Folder'
            if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'api\redist\SolidWorks.Interop.sldworks.dll'))) {
                $SolidWorksPath = $candidate
                break
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($SolidWorksPath)) {
        throw 'SOLIDWORKS was not found. Run Setup.ps1 -SolidWorksPath with the installation folder.'
    }
    if (-not (Test-Path -LiteralPath ([IO.Path]::GetPathRoot($InstallRoot)))) {
        throw "Install drive is missing. Run Setup.ps1 -InstallRoot with an existing non-C drive folder. Default: $InstallRoot"
    }
    $buildLog = & (Join-Path $root 'Build.ps1') -SolidWorksPath $SolidWorksPath 2>&1 | Out-String
    [IO.File]::WriteAllText($log, "SOLIDWORKS=$SolidWorksPath`r`n$buildLog")

    # Encode exact paths so spaces and punctuation survive elevation.
    $installScript = (Join-Path $root 'Install.ps1').Replace("'", "''")
    $package = (Join-Path $root 'dist').Replace("'", "''")
    $sw = $SolidWorksPath.Replace("'", "''")
    $destination = $InstallRoot.Replace("'", "''")
    $quotedLog = $log.Replace("'", "''")
    $command = @"
try {
  & '$installScript' -PackageDirectory '$package' -SolidWorksPath '$sw' -InstallRoot '$destination' *> '$quotedLog'
  exit 0
} catch {
  (`$_ | Out-String) | Out-File -LiteralPath '$quotedLog' -Encoding utf8 -Append
  exit 1
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $shell = Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $installer = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Verb RunAs -WindowStyle Hidden -WorkingDirectory $root -PassThru
    $installer.WaitForExit()
    if ($installer.ExitCode -ne 0) { throw "Registration failed. See $log" }
    $startup = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('SOFTWARE\SOLIDWORKS\AddInsStartup\{3A3E2748-FE45-4ECD-B56B-E0152DFA085A}')
    try { $startup.SetValue('', 1, [Microsoft.Win32.RegistryValueKind]::DWord) } finally { $startup.Dispose() }
    Write-Host 'Installed. Save your work and restart SOLIDWORKS to load the new version.'
    Write-Host 'Part notes: Tools > Part Notes > Add Text Note (Chinese UI: see the included guide).'
    Write-Host "Installation root: $InstallRoot"
    exit 0
} catch {
    $message = $_ | Out-String
    [IO.File]::AppendAllText($log, "`r`n$message")
    Write-Host $message -ForegroundColor Red
    exit 1
}
