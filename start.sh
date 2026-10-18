#!/usr/bin/env bash
# ----------------------------------------------------------------------------
#  Baron: jedno spuštění pro hostitele (toho, kdo pouští server).
#
#    ./start.sh              spustí server a vytvoří veřejný odkaz (funguje i z jiné sítě)
#    ./start.sh --lan        jen místní síť (bez veřejného odkazu a bez stahování čehokoli)
#    PORT=9000 ./start.sh    jiný port (výchozí 8000)
#
#  Skript: 1) nachystá Python prostředí (.venv) a závislosti, 2) spustí server,
#  3) otevře veřejný tunel přes cloudflared ("quick tunnel", bez účtu) a vypíše
#  odkaz pro ostatní hráče. Ctrl+C všechno ukončí.
# ----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
PORT="${PORT:-8000}"
MODE="public"
[[ "${1:-}" == "--lan" ]] && MODE="lan"

say()  { printf '\n\033[1;33m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[1;31mChyba:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 1) Python + virtuální prostředí -------------------------------------------
PY="$(command -v python3 || command -v python || true)"
[[ -n "$PY" ]] || die "Chybí Python 3.10+. Nainstaluj ho (https://www.python.org) a spusť skript znovu."
"$PY" - <<'PYCHECK' || die "Potřebuju Python 3.10 nebo novější."
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PYCHECK

if [[ ! -x .venv/bin/python ]]; then
  say "Připravuji Python prostředí (jen poprvé, chvíli to trvá)…"
  "$PY" -m venv .venv || die "Nepodařilo se vytvořit .venv (na Debianu/Ubuntu zkus: sudo apt install python3-venv)."
fi
STAMP=".venv/.requirements-stamp"
if [[ ! -f "$STAMP" || requirements.txt -nt "$STAMP" ]]; then
  say "Instaluji závislosti…"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

# ---- úklid při ukončení ---------------------------------------------------------
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${PIDS[@]:-}"; do [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true; done
  printf '\n\033[1;33mServer zastaven. Díky za hru!\033[0m\n'
}
trap cleanup EXIT INT TERM

# ---- 2) server ------------------------------------------------------------------
if (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  die "Port $PORT je už obsazený. Spusť třeba: PORT=9000 ./start.sh"
fi
say "Spouštím server na portu $PORT…"
PYTHONPATH="$ROOT/server:$ROOT" .venv/bin/python -m uvicorn networking.server:app \
  --host 0.0.0.0 --port "$PORT" --log-level warning &
PIDS+=("$!")

for _ in $(seq 1 60); do
  (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  kill -0 "${PIDS[0]}" 2>/dev/null || die "Server se nespustil."
  sleep 0.25
done
(echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null || die "Server neodpovídá na portu $PORT."

lan_ip() {
  if command -v ip >/dev/null 2>&1; then ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}'
  elif command -v ipconfig >/dev/null 2>&1; then ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null
  else hostname -I 2>/dev/null | awk '{print $1}'; fi
}
LAN="$(lan_ip || true)"

show_qr() {   # volitelné: QR kód, pokud jde vyrobit (nikdy skript neshodí)
  .venv/bin/python - "$1" <<'PYQR' 2>/dev/null || true
import sys
try:
    import qrcode
except ImportError:
    sys.exit(0)
q = qrcode.QRCode(border=1); q.add_data(sys.argv[1]); q.make(fit=True); q.print_ascii(invert=True)
PYQR
}

# ---- 3) veřejný tunel -----------------------------------------------------------
PUBLIC_URL=""
if [[ "$MODE" == "public" ]]; then
  CF="$(command -v cloudflared || true)"
  if [[ -z "$CF" && -x .bin/cloudflared ]]; then CF="$ROOT/.bin/cloudflared"; fi
  if [[ -z "$CF" ]]; then
    say "Stahuji cloudflared (jednorázově, pro veřejný odkaz)…"
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"; arch="$(uname -m)"
    case "$arch" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) arch="";; esac
    base="https://github.com/cloudflare/cloudflared/releases/latest/download"
    mkdir -p .bin
    if [[ -n "$arch" && "$os" == "linux" ]] && curl -fsSL "$base/cloudflared-linux-$arch" -o .bin/cloudflared; then
      chmod +x .bin/cloudflared; CF="$ROOT/.bin/cloudflared"
    elif [[ -n "$arch" && "$os" == "darwin" ]] && curl -fsSL "$base/cloudflared-darwin-$arch.tgz" -o .bin/cf.tgz \
         && tar -xzf .bin/cf.tgz -C .bin && rm -f .bin/cf.tgz; then
      chmod +x .bin/cloudflared; CF="$ROOT/.bin/cloudflared"
    else
      info "cloudflared se nepodařilo stáhnout (offline, nebo nepodporovaný systém)."
      info "Nainstaluj ho ručně: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    fi
  fi
  if [[ -n "$CF" ]]; then
    say "Vytvářím veřejný odkaz…"
    TLOG="$(mktemp)"
    "$CF" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$TLOG" 2>&1 &
    PIDS+=("$!")
    for _ in $(seq 1 80); do
      PUBLIC_URL="$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TLOG" | head -1 || true)"
      [[ -n "$PUBLIC_URL" ]] && break
      kill -0 "${PIDS[1]}" 2>/dev/null || break
      sleep 0.5
    done
    [[ -n "$PUBLIC_URL" ]] || info "Veřejný odkaz se nepodařilo získat (log: $TLOG). Hrát jde v místní síti."
  fi
fi

# ---- výpis ----------------------------------------------------------------------
printf '\n\033[1;32m========================  BARON BĚŽÍ  ========================\033[0m\n'
if [[ -n "$PUBLIC_URL" ]]; then
  printf '\n  \033[1mPošli kamarádům tento odkaz (funguje odkudkoli):\033[0m\n\n      \033[1;36m%s\033[0m\n' "$PUBLIC_URL"
  show_qr "$PUBLIC_URL"
fi
printf '\n  Ty sám(a) se připoj na:   http://localhost:%s\n' "$PORT"
if [[ -n "$LAN" ]]; then
  printf '  Ve stejné Wi-Fi / síti:   http://%s:%s\n' "$LAN" "$PORT"
fi
[[ -z "$PUBLIC_URL" && "$MODE" == "public" ]] || true
printf '\n  Hosta ve hře dělá ten, kdo založí místnost. Konec: Ctrl+C.\n'
printf '\033[1;32m==============================================================\033[0m\n\n'

wait "${PIDS[0]}"
