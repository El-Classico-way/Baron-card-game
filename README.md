# Baron-card-game

Online multiplayer karetní hra pro přátele. Server drží celý stav hry, prohlížeč jen vykresluje.
Pravidla architektury jsou v [ARCHITECTURE.md](ARCHITECTURE.md), mapa „kam sáhnout“ v [STRUCTURE.md](STRUCTURE.md).

## Hraní s kamarády (jedno spuštění)

Hru pouští jeden člověk (hostitel) na svém počítači. Ostatní hráči nic neinstalují, jen otevřou odkaz
v prohlížeči (telefon i počítač) a napíšou jméno. Hostem hry je ten, kdo založí místnost.

### Co potřebuješ

- **Python 3.10 nebo novější** ([python.org](https://www.python.org/downloads/)). Na Windows při instalaci
  zaškrtni **„Add python.exe to PATH“**.
- Připojení k internetu při prvním spuštění (stáhnou se závislosti a nástroj `cloudflared` pro veřejný odkaz).

### Spuštění

| Systém | Jak |
|---|---|
| **Windows** | dvojklik na `start.bat` (nebo v příkazové řádce ve složce hry: `start.bat`) |
| **Linux / macOS / WSL** | v terminálu ve složce hry: `./start.sh` |

Při prvním spuštění skript sám vytvoří složku `.venv` s Pythonem a nainstaluje závislosti (chvíli to trvá),
spustí server a vypíše **odkaz pro ostatní**. Odkaz funguje z libovolné sítě: skript otevře bezplatný
„quick tunnel“ přes `cloudflared` (stáhne se jednorázově do `.bin/`, účet není potřeba). Hostitel se připojí
na `http://localhost:8000`, kamarádi na vypsaný odkaz. Konec: **Ctrl+C** (nebo zavři okno).

### Možnosti

| Co | Windows | Linux / macOS |
|---|---|---|
| jen místní síť, nic se nestahuje (hráči jdou na `http://<IP>:8000`) | `start.bat --lan` | `./start.sh --lan` |
| jiný port (výchozí 8000) | `set PORT=9000` a pak `start.bat` | `PORT=9000 ./start.sh` |

### Když něco nejde

- **Windows: „Chybí Python 3.10+“** – nainstaluj Python s PATH (viz výše), zavři a znovu otevři okno a spusť znovu.
  Na Windows může Defender nebo firewall při prvním spuštění chtít povolení; povol ho pro soukromé sítě.
- **„Port 8000 je už obsazený“** – spusť s jiným portem (tabulka výše).
- **Veřejný odkaz se nevytvořil** – hostitel i hráči ve stejné síti můžou hrát přes `--lan`; jinak zkontroluj internet
  nebo nainstaluj `cloudflared` ručně ([návod](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).
- Odkaz je veřejný, kdo ho má, může hrát. Po ukončení skriptu přestane fungovat.

Za běhu hry:

- Když se hráči na mobilu ztratí spojení (přepnutí aplikace, zhasnutá obrazovka), hra se
  připojí znovu sama. Do rozehrané hry se jde vrátit i novým oknem pod **stejným jménem**.
  Hra na odpojeného hráče čeká; host ho může v jeho kartě (klepnutí na jméno) odebrat
  tlačítkem „Hrát bez něj/ní“ a hraje se za něj automaticky.
- Na konci hry si hráči nejdřív prohlédnou poslední kolo. Host pak klepne na
  „Ohlásit vítěze“ (animace „je Baron!“ proběhne jednou) a nakonec může všechny vrátit
  do lobby tlačítkem „Zpět do lobby“ (jde změnit mód a hrát znovu bez nového kódu).

## Vývoj

### Příprava

    python -m venv .venv
    . .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -r requirements-dev.txt

### Spuštění serveru ručně

    PYTHONPATH=server:. uvicorn networking.server:app --port 8000

Windows (PowerShell): `$env:PYTHONPATH="server;."` a pak stejný příkaz `uvicorn …`.
Server zároveň vydává webového klienta, takže stačí otevřít `http://localhost:8000/`.
Soubory klienta se vždy znovu ověřují (hlavička `Cache-Control: no-cache`), po aktualizaci tedy
prohlížeč nepoužije staré zapamatované skripty.

### Testy

    pytest tests --ignore=tests/e2e     # rychlé, bez prohlížeče
    pytest tests/e2e                    # prohlížečové testy, pár minut

`pytest.ini` přidává `server/` a kořen repozitáře do cesty pro importy. Prohlížečové testy
(`tests/e2e/`) ovládají Chromium přes Playwright proti živému serveru. Potřebují
`playwright install chromium` a bez prohlížeče se samy přeskočí.

## Náhled rozhraní bez serveru (telefon nebo počítač)

Klient má **ukázkový režim** (mock), který nepotřebuje herní server. Používá falešné hráče a zástupné
karty, jen aby šlo posoudit vzhled a ovládání. Pravidla v něm nejsou autoritativní a fialový pruh
„Ukázka“ to připomíná. Na úvodní stránce je tlačítko **Vyzkoušet ukázku**.

- Lokálně, při běžícím serveru: `http://localhost:8000/?mock=1`.
  `?mock=table` skočí rovnou ke stolu, `&bots=6` dá rozložení pro 7 hráčů,
  `&mode=vydrz_rucne` zvolí ruční variantu.
- Jeden samostatný soubor na libovolný hosting:

      python tools/build_preview.py preview/index.html

  Výstup je jediný HTML soubor (k sestavení je potřeba Node).

### Server z počítače na telefonu

Ve stejné Wi-Fi: spusť server na všech rozhraních a otevři `http://<IP-počítače>:8000/`
(nebo použij `./start.sh --lan` / `start.bat --lan`). Odjinud (HTTPS, libovolná síť):
`start.sh` / `start.bat` vytvoří veřejný odkaz sám; ručně to umí `cloudflared tunnel --url http://localhost:8000`
nebo `ngrok http 8000`. WebSockety fungují přes obojí.

### Skutečný server × ukázka

| | Skutečný server | Ukázka (mock) |
|---|---|---|
| Spojení | `client/js/core/socket.js` + Python server | `client/js/mock/mock-socket.js` |
| Karty, kola, ruce | rozhoduje server | falešné, bez pravidel |
| Zbytek `client/` (stav, vykreslování, CSS) | společný | společný |

## Herní módy

| Mód | Stav |
|---|---|
| **Výdrž** | Hotový, ve dvou variantách: **Automaticky** (animované počítání) a **Ručně** (hráči počítají sami). |
| **Řežba** | Zatím neimplementovaný, v lobby je vidět zablokovaný („brzy“). Starší prototyp enginu v `server/game` je jen náhrada a jde k němu jen přes vývojářský přepínač. |

Mód a variantu vybírá host v lobby. Kód módů je v `server/modes/<mód>/`; server vlastní stav i pravidla,
klient jen vykresluje snímky stavu.

### Výdrž

Každý hráč dostane jednu **postavu**, 8 **lokací** (od každého čísla 1–8 jednu) a 8 **existencí**, vše veřejné.
V každém z 8 kol hráč tajně vybere jednu lokaci a jednu existenci. Když vyberou všichni, vytáhnou se 3 karty
chlastu a číslo hráče je součet statů těchto druhů chlastu na jeho postavě, lokaci a existenci. Nejvyšší součet
vyhrává kolo, bere lokaci a její číslo si připíše do skóre. Remízu rozhoduje tah jednoho chlastu, počítají se jen postavy.
Na konci vyhrává nejvyšší skóre, prohrává nejnižší (shody se řeší stejně).

- **Automaticky:** počítání se animuje (rychlost v nastavení zařízení).
- **Ručně:** všech 8 dvojic se naplánuje předem (dá se přetahovat a jde „Rozdělit náhodně“), tři chlasty se postupně
  odhalí a hráč napíše svůj součet. Správně se započítá, nižší číslo je „Blbec“, vyšší „Švindlíř“ (−50).
- Hra čeká, až jednají všichni. Odešlého hráče hra dohraje za něj.
- Na konci host ohlásí vítěze („<Jméno> je Baron za postavu <Postava>!“) a může všechny vrátit do lobby.

Obsah karet je data: `server/modes/vydrz/data/vydrz.json` (11 pevných postav s vlajkou, názvy lokací a existencí
z dočasných seznamů `mista.txt` a `prezdivky.txt`, náhodné zástupné staty lokací a existencí). Generuje ho
`python tools/generate_vydrz_data.py`; skutečné karty stačí vložit do JSON, načítač je zkontroluje.
Počet kusů každého chlastu je na začátku každé hry náhodný (20 ± 5).

Nastavení na zařízení (v lobby i u stolu): zvuk, rychlost počítání, animace rozdávání.
