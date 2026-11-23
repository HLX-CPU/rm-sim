[CmdletBinding()]
param(
    [string] $SolidWorksPath = 'D:\sw2026\sw\SOLIDWORKS',
    [switch] $PrepareUiFixture,
    [switch] $AssemblyNote,
    [switch] $PartNote,
    [switch] $Runtime,
    [switch] $AnnotatedUi,
    [switch] $CheckCallbacks,
    [int] $SessionPid
)

$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$verified = Join-Path 'D:\SolidWorksSketchToolsVerification' ([guid]::NewGuid().ToString('N'))
$redist = Join-Path $SolidWorksPath 'api\redist'
New-Item -ItemType Directory -Path $verified -Force | Out-Null
$references = @(foreach ($name in 'sldworks', 'swconst', 'swpublished') {
    $file = "SolidWorks.Interop.$name.dll"
    Copy-Item -LiteralPath (Join-Path $redist $file) -Destination $verified
    "/reference:$(Join-Path $verified $file)"
})
$dll = Join-Path $PSScriptRoot 'dist\SolidWorksSketchTools.dll'
Copy-Item -LiteralPath $dll -Destination $verified
$test = if ($Runtime) { 'VerifyRuntime' } elseif ($AssemblyNote -or $PartNote) { 'VerifyAssemblyNote' } else { 'VerifySelection' }
$exe = Join-Path $verified "$test.exe"
& $compiler /nologo /platform:x64 /codepage:65001 /warnaserror+ "/reference:$dll" "/out:$exe" @references (Join-Path $PSScriptRoot "$test.cs") (Join-Path $PSScriptRoot 'RuntimeSession.cs')
if ($LASTEXITCODE -ne 0) { throw "Verification compile failed: $LASTEXITCODE" }
$arguments = @()
if ($AssemblyNote -or $PartNote) { $arguments += $verified }
if ($PartNote) { $arguments += '--part' }
if ($PrepareUiFixture) { $arguments += '--ui' }
if ($AnnotatedUi) { $arguments += '--annotated' }
if ($CheckCallbacks) { $arguments += '--callbacks' }
if ($SessionPid) { $arguments += "--pid=$SessionPid" }
& $exe @arguments
if ($LASTEXITCODE -ne 0) { throw "Verification failed: $LASTEXITCODE" }
