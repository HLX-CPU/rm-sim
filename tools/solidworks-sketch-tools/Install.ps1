[CmdletBinding()]
param(
    [string] $PackageDirectory,
    [string] $SolidWorksPath = 'D:\sw2026\sw\SOLIDWORKS',
    [string] $InstallRoot = 'D:\SolidWorksSketchTools'
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrEmpty($PackageDirectory)) {
    $PackageDirectory = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'dist'
}
$identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($PSVersionTable.PSEdition -ne 'Desktop' -or (-not [Environment]::Is64BitProcess) -or
    (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))) {
    throw 'Run this script from a 64-bit Windows PowerShell session as administrator.'
}

$install = $InstallRoot
$guid = '{3A3E2748-FE45-4ECD-B56B-E0152DFA085A}'
$catalog = "SOFTWARE\SOLIDWORKS\AddIns\$guid"
$regasm = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe'
$files = @('SolidWorksSketchTools.dll') + @(20, 32, 40, 64, 96, 128 | ForEach-Object { "Circle$_.png"; "Note$_.png" })
$sources = @{}
foreach ($file in $files) {
    $sources[$file] = Join-Path $PackageDirectory $file
}
$addin = [Reflection.Assembly]::ReflectionOnlyLoadFrom($sources['SolidWorksSketchTools.dll'])
$install = Join-Path $install $addin.GetName().Version.ToString()
foreach ($reference in $addin.GetReferencedAssemblies() | Where-Object Name -like 'SolidWorks.Interop.*') {
    $file = $reference.Name + '.dll'
    $source = Join-Path $SolidWorksPath "api\redist\$file"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or
        [Reflection.AssemblyName]::GetAssemblyName($source).FullName -ne $reference.FullName) {
        throw "Missing matching SOLIDWORKS interop file $source. Rebuild using the target installation."
    }
    $sources[$file] = $source
}
foreach ($source in $sources.Values) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing package file $source" }
}
New-Item -ItemType Directory -Path $install -Force | Out-Null
foreach ($file in $sources.Keys) {
    $source = $sources[$file]
    $target = Join-Path $install $file
    if (-not (Test-Path -LiteralPath $target) -or
        (Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

$dll = Join-Path $install 'SolidWorksSketchTools.dll'
$oldErrorPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $registration = & $regasm $dll /codebase /nologo 2>&1 | Out-String
    $registrationExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $oldErrorPreference
}
if ($registrationExit -ne 0) { throw "RegAsm failed with exit code $registrationExit. $registration" }
Write-Output $registration.Trim()
# Unicode labels also work when Windows PowerShell reads an ASCII script.
$title = -join [char[]](0x8349,0x56FE,0x5FEB,0x6377,0x5DE5,0x5177)
$key = [Microsoft.Win32.Registry]::LocalMachine.CreateSubKey($catalog)
try {
    $key.SetValue('', 0, [Microsoft.Win32.RegistryValueKind]::DWord)
    $key.SetValue('Title', $title, [Microsoft.Win32.RegistryValueKind]::String)
    $key.SetValue('Description', 'Select underdefined circles and add text notes in parts and assemblies.', [Microsoft.Win32.RegistryValueKind]::String)
} finally {
    $key.Close()
}
Write-Output "Registered $title at $dll"
