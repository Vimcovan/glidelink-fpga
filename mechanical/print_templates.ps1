# Print out/_print_p*.png (A4 landscape, 300 dpi renders of wall_templates.pdf) at true size: the page is drawn in
# millimetres at 297 x 210, shifted by the printer's hard margins, so no "fit to page" scaling can happen.
#   powershell -File print_templates.ps1 -Printer "<name>" [-ToFile out\check.pdf]
param([string]$Printer, [string]$ToFile = "", [string]$Pattern = "out\_print_p*.png")
Add-Type -AssemblyName System.Drawing
$files = @(Get-ChildItem (Join-Path $PSScriptRoot $Pattern) | Sort-Object Name)
$script:i = 0
$doc = New-Object System.Drawing.Printing.PrintDocument
$doc.PrinterSettings.PrinterName = $Printer
if (-not $doc.PrinterSettings.IsValid) { "printer not found: $Printer"; exit 1 }
if ($ToFile) { $doc.PrinterSettings.PrintToFile = $true; $doc.PrinterSettings.PrintFileName = $ToFile }
$a4 = $doc.PrinterSettings.PaperSizes | Where-Object { $_.Kind -eq 'A4' } | Select-Object -First 1
if ($a4) { $doc.DefaultPageSettings.PaperSize = $a4 }
$doc.DefaultPageSettings.Landscape = $true
$doc.DocumentName = "GlideLink wall templates"
$doc.add_PrintPage({
  param($s, $e)
  $img = [System.Drawing.Image]::FromFile($files[$script:i].FullName)
  $g = $e.Graphics
  $g.PageUnit = [System.Drawing.GraphicsUnit]::Millimeter
  $hx = $e.PageSettings.HardMarginX * 0.254; $hy = $e.PageSettings.HardMarginY * 0.254
  $g.DrawImage($img, [single](-$hx), [single](-$hy), [single]297.0, [single]210.0)
  "page $($script:i + 1): hard margins $([math]::Round($hx,2)) / $([math]::Round($hy,2)) mm, paper $($e.PageSettings.PaperSize.PaperName), landscape $($e.PageSettings.Landscape)"
  $img.Dispose()
  $script:i++
  $e.HasMorePages = ($script:i -lt $files.Count)
})
$doc.Print()
"sent $($files.Count) pages to $Printer"
