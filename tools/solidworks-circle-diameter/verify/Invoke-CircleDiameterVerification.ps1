[CmdletBinding()]
param(
    [ValidateSet('Compile', 'Smoke', 'Regression', 'AssemblySmoke', 'PrepareToolbarTest')]
    [string] $Mode = 'Compile',
    [string] $SolidWorksPath = 'D:\sw2026\sw\SOLIDWORKS',
    [string] $PartTemplate = 'C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2026\templates\gb_part.prtdot',
    [string] $AssemblyTemplate = 'C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2026\templates\gb_assembly.asmdot',
    [string] $Source = (Join-Path $PSScriptRoot '..\BatchCircleDiameterCore.bas'),
    [string] $OutputRoot = 'D:\SolidWorksCircleDiameterVerification',
    [string] $RollbackAnchor = 'newDimensions = newDimensions + 1',
    [string] $LateRollbackAnchor = 'conflict = CheckGeometry(circles, circleCount, diameterM, True)'
)

$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319'
$redist = Join-Path $SolidWorksPath 'api\redist'
$compiler = Join-Path $framework 'csc.exe'
$interop = Join-Path $redist 'SolidWorks.Interop.sldworks.dll'
$constants = Join-Path $redist 'SolidWorks.Interop.swconst.dll'
$runDir = Join-Path $OutputRoot ([Guid]::NewGuid().ToString('N'))
$executable = Join-Path $runDir 'CircleDiameterVerification.exe'

foreach ($path in @($compiler, $interop, $constants)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing prerequisite: $path"
    }
}

New-Item -ItemType Directory -Path $runDir | Out-Null
Write-Output "Verification output: $runDir"
& $compiler /nologo /platform:x64 /target:exe /warnaserror+ "/out:$executable" "/reference:$interop" "/reference:$constants" (Join-Path $PSScriptRoot 'CircleDiameterVerification.cs')
if ($LASTEXITCODE -ne 0) { throw "C# compilation failed with exit code $LASTEXITCODE" }
if ($Mode -eq 'Compile') { return }

$template = if ($Mode -eq 'AssemblySmoke') { $AssemblyTemplate } else { $PartTemplate }
if ($Mode -ne 'Smoke') { $template = (Resolve-Path -LiteralPath $template).ProviderPath }
if ($Mode -eq 'Regression' -or $Mode -eq 'AssemblySmoke') { $Source = (Resolve-Path -LiteralPath $Source).ProviderPath }

$arguments = @($Mode, $redist, $runDir, $template, $Source, $RollbackAnchor, $LateRollbackAnchor)
& $executable @arguments
if ($LASTEXITCODE -ne 0) { throw "Verification failed with exit code $LASTEXITCODE" }
