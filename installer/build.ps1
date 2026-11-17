# Builds the OmicChain Windows installer: R with the R packages of renv.lock, Python with the
# packages of requirements.windows.lock.txt, and the application, in one per-user installer.
# Run on Windows from the repository root, with R 4.5.1 (and Rtools) on the PATH:
#   powershell -File installer\build.ps1 -Version 1.0.0
param([string]$Version = "dev", [string]$PythonVersion = "3.12.10")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Build = Join-Path $Root "build"
$Bundle = Join-Path $Build "bundle"
$Lib = Join-Path $Build "library"
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
New-Item -ItemType Directory -Force $Bundle, $Lib | Out-Null

function Step($text) { Write-Host "`n=== $text" }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (code $LASTEXITCODE)" } }

Step "R"
$RHome = (& Rscript -e "cat(normalizePath(R.home(), winslash = '\\'))"); Check "Locating R"
& Rscript -e "if (getRversion() != '4.5.1') stop('R 4.5.1 is required, found ', getRversion())"; Check "R version"
Copy-Item -Recurse $RHome (Join-Path $Bundle "R")
$Rscript = Join-Path $Bundle "R\bin\Rscript.exe"

Step "R packages (renv.lock)"
$env:RENV_CONFIG_AUTOLOADER_ENABLED = "false"
$env:R_LIBS = $Lib; $env:R_LIBS_USER = $Lib; $env:R_LIBS_SITE = $Lib
$Empty = Join-Path $Build "empty.Rprofile"; Set-Content -Path $Empty -Value "" -NoNewline
$env:R_PROFILE_USER = $Empty; $env:R_ENVIRON_USER = $Empty
Push-Location $Build
& $Rscript (Join-Path $PSScriptRoot "install_r_packages.R") (Join-Path $Root "renv.lock") $Lib; Check "Installing the R packages"
Pop-Location

Step "The R environment of the installer must be, byte for byte, renv.lock"
$CheckDir = Join-Path $Build "check"
if (Test-Path $CheckDir) { Remove-Item -Recurse -Force $CheckDir }
New-Item -ItemType Directory $CheckDir | Out-Null
Copy-Item (Join-Path $Root "DESCRIPTION") $CheckDir
Push-Location $CheckDir
& $Rscript (Join-Path $Root "snapshot_renv.R") "renv.lock"; Check "Recording the R environment"
Pop-Location
$a = (Get-FileHash (Join-Path $CheckDir "renv.lock")).Hash
$b = (Get-FileHash (Join-Path $Root "renv.lock")).Hash
if ($a -ne $b) {
  & git --no-pager diff --no-index --stat (Join-Path $Root "renv.lock") (Join-Path $CheckDir "renv.lock")
  & git --no-pager diff --no-index (Join-Path $Root "renv.lock") (Join-Path $CheckDir "renv.lock") | Select-Object -First 80
  throw "The R environment of the installer differs from renv.lock"
}
Copy-Item -Recurse $Lib (Join-Path $Bundle "library")

Step "Python $PythonVersion"
$Zip = Join-Path $Build "python-$PythonVersion.zip"
if (-not (Test-Path $Zip)) { Invoke-WebRequest "https://www.nuget.org/api/v2/package/python/$PythonVersion" -OutFile $Zip }
$Unz = Join-Path $Build "python-nuget"
if (Test-Path $Unz) { Remove-Item -Recurse -Force $Unz }
Expand-Archive $Zip $Unz
Copy-Item -Recurse (Join-Path $Unz "tools") (Join-Path $Bundle "python")
$Py = Join-Path $Bundle "python\python.exe"
& $Py -m ensurepip --default-pip; Check "pip"
& $Py -m pip install --no-warn-script-location --disable-pip-version-check -r (Join-Path $Root "requirements.windows.lock.txt"); Check "Python packages"
& $Py -c "import cryptography, streamlit, snakemake, web3, pandas; print('Python packages OK')"; Check "Python imports"

Step "Application"
$App = Join-Path $Bundle "app"
$xd = @(".git", ".github", "tests", "docker", "portal", "installer", "build", "dist", "__pycache__", ".snakemake", "library", "staging", "results", "studies", "jobs", "verify_runs")
$xf = @("Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore", ".gitattributes", ".env", "orcid_link.json", "index.html", "orcid-callback.html", "*.pyc", "*.zip")
& robocopy $Root $App /E /NFL /NDL /NJH /NJS /NP /XD $xd /XF $xf | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the application failed" }
$global:LASTEXITCODE = 0
foreach ($f in "OmicChain.py", "Snakefile", "renv\activate.R", "official_code.json", ".streamlit\config.toml") {
  if (-not (Test-Path (Join-Path $App $f))) { throw "Missing in the application: $f" }
}

Step "Launcher, icon, version"
Copy-Item (Join-Path $PSScriptRoot "OmicChain.cmd"), (Join-Path $PSScriptRoot "omicchain-env.cmd") $Bundle
Copy-Item $Empty (Join-Path $Bundle "empty.Rprofile")
Set-Content -Path (Join-Path $Bundle "VERSION.txt") -Value $Version -NoNewline
$Logo = Join-Path $Root "omicchain_logo.png"
$Ico = Join-Path $Bundle "omicchain.ico"
& $Py -c "import sys; from PIL import Image; im = Image.open(sys.argv[1]).convert('RGBA'); n = max(im.size); sq = Image.new('RGBA', (n, n), (0, 0, 0, 0)); sq.paste(im, ((n - im.width) // 2, (n - im.height) // 2)); sq.save(sys.argv[2], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])" $Logo $Ico; Check "Icon"

Step "Installer"
$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
  & choco install innosetup -y --no-progress | Out-Null
  $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
& $Iscc "/DAppVersion=$Version" "/DBundle=$Bundle" "/DOutDir=$(Join-Path $Root 'dist')" "/DRoot=$Root" (Join-Path $PSScriptRoot "OmicChain.iss"); Check "Inno Setup"
$size = [math]::Round(((Get-ChildItem -Recurse $Bundle | Measure-Object Length -Sum).Sum) / 1GB, 2)
Write-Host "`nInstalled size: $size GB"
Get-ChildItem (Join-Path $Root "dist") | ForEach-Object { Write-Host ("Installer: {0} ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB)) }
