# ----------------------------------------------------------------------------
#  Baron: jedno spusteni pro hostitele na Windows (obdoba start.sh).
#
#    start.bat              spusti server a vytvori verejny odkaz (funguje i z jine site)
#    start.bat --lan        jen mistni sit (bez verejneho odkazu a bez stahovani ciehokoli)
#    $env:PORT=9000; start.bat   jiny port (vychozi 8000)
#
#  Potrebuje Python 3.10+ (https://www.python.org, pri instalaci zaskrtni "Add python.exe to PATH").
#  Ctrl+C vse ukonci.
# ----------------------------------------------------------------------------
param([switch]$lan)
$ErrorActionPreference = "Stop"
if ($args -contains "--lan") { $lan = $true }
Set-Location $PSScriptRoot
$Root = $PSScriptRoot
$Port = if ($env:PORT) { [int]$env:PORT } else { 8000 }

function Say($t)  { Write-Host "`n$t" -ForegroundColor Yellow }
function Info($t) { Write-Host "  $t" }
function Die($t)  { Write-Host "`nChyba: $t" -ForegroundColor Red; exit 1 }

# ---- 1) Python + virtualni prostredi -------------------------------------------
$py = $null
foreach ($c in @("py", "python", "python3")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) { Die "Chybi Python 3.10+. Nainstaluj ho z https://www.python.org (zaskrtni 'Add python.exe to PATH') a spust skript znovu." }
$pyArgs = if ($py -eq "py") { @("-3") } else { @() }
& $py @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { Die "Potrebuju Python 3.10 nebo novejsi." }

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Say "Pripravuji Python prostredi (jen poprve, chvili to trva)..."
  & $py @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Die "Nepodarilo se vytvorit .venv." }
}
$stamp = Join-Path $Root ".venv\.requirements-stamp"
$req = Join-Path $Root "requirements.txt"
if (-not (Test-Path $stamp) -or (Get-Item $req).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
  Say "Instaluji zavislosti..."
  & $venvPy -m pip install --quiet --upgrade pip
  & $venvPy -m pip install --quiet -r requirements.txt
  if ($LASTEXITCODE -ne 0) { Die "Instalace zavislosti selhala." }
  New-Item -ItemType File -Force $stamp | Out-Null
}

# ---- 2) server ------------------------------------------------------------------
function PortOpen($p) {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $p); $c.Close(); return $true } catch { return $false }
}
if (PortOpen $Port) { Die "Port $Port je uz obsazeny. Spust treba: `$env:PORT=9000; start.bat" }

$procs = @()
try {
  Say "Spoustim server na portu $Port..."
  $env:PYTHONPATH = "$Root\server;$Root"
  $server = Start-Process -FilePath $venvPy -PassThru -NoNewWindow -ArgumentList @(
    "-m", "uvicorn", "networking.server:app", "--host", "0.0.0.0", "--port", "$Port", "--log-level", "warning")
  $procs += $server
  for ($i = 0; $i -lt 80; $i++) {
    if (PortOpen $Port) { break }
    if ($server.HasExited) { Die "Server se nespustil." }
    Start-Sleep -Milliseconds 250
  }
  if (-not (PortOpen $Port)) { Die "Server neodpovida na portu $Port." }

  $lanIp = $null
  try {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
      Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
      Select-Object -First 1).IPAddress
  } catch { }

  # ---- 3) verejny tunel ---------------------------------------------------------
  $publicUrl = $null
  if (-not $lan) {
    $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
    $local = Join-Path $Root ".bin\cloudflared.exe"
    if (-not $cf -and (Test-Path $local)) { $cf = $local }
    if (-not $cf) {
      Say "Stahuji cloudflared (jednorazove, pro verejny odkaz)..."
      New-Item -ItemType Directory -Force (Join-Path $Root ".bin") | Out-Null
      $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "386" }
      try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -OutFile $local `
          -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-$arch.exe"
        $cf = $local
      } catch {
        Info "cloudflared se nepodarilo stahnout (offline?)."
        Info "Nainstaluj ho rucne: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
      }
    }
    if ($cf) {
      Say "Vytvarim verejny odkaz..."
      $log = Join-Path $env:TEMP "baron-tunnel.log"
      Remove-Item $log -ErrorAction SilentlyContinue
      $tunnel = Start-Process -FilePath $cf -PassThru -WindowStyle Hidden -RedirectStandardError $log `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port")
      $procs += $tunnel
      for ($i = 0; $i -lt 80 -and -not $publicUrl; $i++) {
        if (Test-Path $log) {
          $m = Select-String -Path $log -Pattern "https://[a-zA-Z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
          if ($m) { $publicUrl = $m.Matches[0].Value }
        }
        if ($tunnel.HasExited) { break }
        Start-Sleep -Milliseconds 500
      }
      if (-not $publicUrl) { Info "Verejny odkaz se nepodarilo ziskat (log: $log). Hrat jde v mistni siti." }
    }
  }

  # ---- vypis --------------------------------------------------------------------
  Write-Host "`n========================  BARON BEZI  ========================" -ForegroundColor Green
  if ($publicUrl) {
    Write-Host "`n  Posli kamaradum tento odkaz (funguje odkudkoli):`n" -ForegroundColor White
    Write-Host "      $publicUrl" -ForegroundColor Cyan
  }
  Write-Host "`n  Ty sam(a) se pripoj na:   http://localhost:$Port"
  if ($lanIp) { Write-Host "  Ve stejne Wi-Fi / siti:   http://${lanIp}:$Port   (Windows se muze zeptat na povoleni firewallu)" }
  Write-Host "`n  Hosta ve hre dela ten, kdo zalozi mistnost. Konec: Ctrl+C."
  Write-Host "==============================================================`n" -ForegroundColor Green

  $server.WaitForExit()
}
finally {
  foreach ($p in $procs) { try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch { } }
  Write-Host "`nServer zastaven. Diky za hru!" -ForegroundColor Yellow
}
