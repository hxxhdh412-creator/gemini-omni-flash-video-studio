$ErrorActionPreference = "Stop"

# Auto-detect Python 3.10+ (supports uv, system python, or py launcher)
$pyCandidates = @(
  "$env:APPDATA\uv\python\cpython-3.12.15-windows-x86_64-none\python.exe",
  (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
  (Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path $_) }

if ($pyCandidates.Count -eq 0) {
  throw "Python 3.10+ not found. Please install Python or uv."
}
$py = $pyCandidates[0]

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillScripts = Join-Path $repoRoot "skills\gemini-omni-flash-api\scripts\video"
$editPy = Join-Path $skillScripts "edit_video.py"
$producePy = Join-Path $skillScripts "produce_commercial.py"

$cmd = if ($args.Count -gt 0) { $args[0] } else { "help" }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

if ($cmd -eq "switch-account") {
  & $py $editPy --switch-account
} elseif ($cmd -eq "refresh") {
  & $py $editPy --refresh-from-cdp
} elseif ($cmd -eq "edit") {
  & $py $editPy @rest
} elseif ($cmd -eq "commercial") {
  & $py $producePy @rest
} else {
  Write-Host "Gemini Omni 1.1 Flash Video Studio CLI:"
  Write-Host "  .\produce.ps1 switch-account   # Open https://vids.new and bind Google account via CDP 9222"
  Write-Host "  .\produce.ps1 refresh          # Refresh vids_session.json cookies from Chrome CDP"
  Write-Host "  .\produce.ps1 commercial ...   # Run full Omni 1.1 Flash + Lyria Music + Gemini 3.8 TTS pipeline"
  Write-Host "  .\produce.ps1 edit ...         # Run Gemini Omni 1.1 Flash video editor / chainer / StructuredImage"
}
