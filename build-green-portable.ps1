<#
.SYNOPSIS
  Build a green portable package (Embeddable Python + app) as a zip.

.DESCRIPTION
  Assembles a self-contained folder - official Windows Embeddable Python,
  the project's dependencies, backend, frontend/dist, bundled question banks
  and a double-click launcher - then packages it into
  "EnglishPractice_Machine-portable".zip. The end user needs no Python,
  no Node, no admin rights.

  The package layout is chosen so backend/app/config.py resolves the project
  root via parents[2] without any code change.

.EXAMPLE
  .\build_portable.ps1

.EXAMPLE
  .\build_portable.ps1 -EmbedPyVersion 3.12.13
#>
[CmdletBinding()]
param(
  [AllowNull()]
  [string]$EmbedPyVersion,
  [string]$OutDir = ".build"
)
$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot

# --- 0. Preconditions -----------------------------------------------------
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
  throw "Python virtual env not found. Run setup.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "frontend\dist\index.html"))) {
  throw "Frontend not built. Run: cd frontend; corepack pnpm run build"
}

# --- Detect matching Embeddable Python from the venv unless overridden ----
if ([string]::IsNullOrEmpty($EmbedPyVersion)) {
  $EmbedPyVersion = (& $venvPython -c "import sys; print('%s.%s.%s' % sys.version_info[:3])").Trim()
  Write-Host "Detected .venv Python $EmbedPyVersion"
}
$major, $minor, $rest = $EmbedPyVersion.Split(".")
$ver2 = $major + $minor                                   # e.g. 313

# --- 1. Embeddable Python (cached download) ------------------------------
$cacheDir = Join-Path $projectRoot ".cache"
$embedZip = Join-Path $cacheDir "python-$EmbedPyVersion-embed-amd64.zip"
$embedUrl  = "https://www.python.org/ftp/python/$EmbedPyVersion/python-$EmbedPyVersion-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $embedZip)) {
  New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
  Write-Host "Downloading embeddable Python $EmbedPyVersion ..."
  Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
}

# --- 2. Fresh build dir ---------------------------------------------------
$buildRoot = Join-Path $projectRoot $OutDir
$buildDir  = Join-Path $buildRoot "portable"
Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
Write-Host "Expanding embeddable Python ..."
Expand-Archive -Path $embedZip -DestinationPath $buildDir -Force

# --- 3. pythonXXX._pth: enable site and register deps ---------------------
$stackName = "python$ver2._pth"
$pthContent = "python$ver2.zip`n.`nLib\site-packages`nimport site`n"
[System.IO.File]::WriteAllText(
  (Join-Path $buildDir $stackName),
  $pthContent,
  (New-Object System.Text.UTF8Encoding($false))
)

# --- 4. Copy dependencies from .venv -------------------------------------
Write-Host "Copying dependencies ..."
robocopy (Join-Path $projectRoot ".venv\Lib\site-packages") (Join-Path $buildDir "Lib\site-packages") /E /NFL /NDL /NJH /NJS /NP | Out-Null

# --- 5. Copy app code / frontend / bundled banks -------------------------
Write-Host "Copying app ..."
robocopy (Join-Path $projectRoot "backend") (Join-Path $buildDir "backend") /E /XD data __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $projectRoot "frontend\dist") (Join-Path $buildDir "frontend\dist") /E /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $projectRoot "examples\bundled-banks") (Join-Path $buildDir "examples\bundled-banks") /E /NFL /NDL /NJH /NJS /NP | Out-Null
Copy-Item (Join-Path $projectRoot "desktop_app.py") (Join-Path $buildDir "desktop_app.py") -Force
Copy-Item (Join-Path $projectRoot "brand-mark.ico") (Join-Path $buildDir "brand-mark.ico") -Force

# --- 6. Launcher + zip (handled in Python to keep ASCII in this script) ---
$env:EPM_BUILD = $buildDir
try {
  & $venvPython (Join-Path $projectRoot "tools\gen_launcher.py")
  if ($LASTEXITCODE -ne 0) { throw "gen_launcher failed" }
  & $venvPython (Join-Path $projectRoot "tools\zip_portable.py")
  if ($LASTEXITCODE -ne 0) { throw "zip_portable failed" }
}
finally {
  Remove-Item Env:\EPM_BUILD -ErrorAction SilentlyContinue
}

Write-Host "Done. Portable zip under: $buildRoot"