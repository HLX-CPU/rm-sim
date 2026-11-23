[CmdletBinding()]
param(
    [string] $SolidWorksPath = 'D:\sw2026\sw\SOLIDWORKS',
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrEmpty($OutputDirectory)) { $OutputDirectory = Join-Path $root 'dist' }
$compiler = Join-Path $env:windir 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$redist = Join-Path $SolidWorksPath 'api\redist'
$references = @(foreach ($library in 'sldworks', 'swconst', 'swpublished') {
    $path = Join-Path $redist "SolidWorks.Interop.$library.dll"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing interop library $path" }
    "/reference:$path"
})
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$output = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
$source = @('CircleSelection.cs', 'AssemblyNote.cs', 'PartNote.cs', 'NoteDialog.cs', 'SketchCommands.cs', 'SketchToolsAddin.cs') | ForEach-Object { Join-Path $root $_ }
$library = Join-Path $output 'SolidWorksSketchTools.dll'
& $compiler /nologo /target:library /platform:x64 /codepage:65001 /optimize+ /warnaserror+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/out:$library" @references @source
if ($LASTEXITCODE -ne 0) { throw "C# compilation failed with exit code $LASTEXITCODE" }

Add-Type -AssemblyName System.Drawing
foreach ($size in 20, 32, 40, 64, 96, 128) {
    $image = [Drawing.Bitmap]::new($size, $size)
    $graphics = [Drawing.Graphics]::FromImage($image)
    $pen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(23, 105, 175), [single]($size * .10))
    try {
        $graphics.Clear([Drawing.Color]::Transparent)
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.DrawEllipse($pen, [single]($size * .17), [single]($size * .17), [single]($size * .66), [single]($size * .66))
        $graphics.FillEllipse([Drawing.Brushes]::SteelBlue, [single]($size * .44), [single]($size * .44), [single]($size * .12), [single]($size * .12))
        $image.Save((Join-Path $output "Circle$size.png"), [Drawing.Imaging.ImageFormat]::Png)
        $graphics.Clear([Drawing.Color]::Transparent)
        $graphics.DrawRectangle($pen, [single]($size * .17), [single]($size * .15), [single]($size * .66), [single]($size * .51))
        $graphics.DrawLine($pen, [single]($size * .36), [single]($size * .67), [single]($size * .20), [single]($size * .86))
        $graphics.DrawLine($pen, [single]($size * .33), [single]($size * .40), [single]($size * .66), [single]($size * .40))
        $image.Save((Join-Path $output "Note$size.png"), [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $pen.Dispose()
        $graphics.Dispose()
        $image.Dispose()
    }
}
Get-Item -LiteralPath $library
