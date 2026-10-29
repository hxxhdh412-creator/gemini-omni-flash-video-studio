$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcSkill = Join-Path $repoRoot "skills\gemini-omni-flash-api"
$dstSkill = Join-Path $HOME ".gemini\config\skills\gemini-omni-flash-api"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dstSkill) | Out-Null
if (Test-Path $dstSkill) {
  Remove-Item -Recurse -Force $dstSkill
}
Copy-Item -Recurse -Force $srcSkill $dstSkill
Write-Host "Installed skill 'gemini-omni-flash-api' -> $dstSkill" -ForegroundColor Green
