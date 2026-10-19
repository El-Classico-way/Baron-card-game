# Struktura repozitáře (pro vývojáře)

Jedna věta: **autoritativní Python server** (FastAPI + WebSocket) drží celý stav hry, **klient v čistém JS**
jen vykresluje to, co server pošle, a posílá záměry (volba karty, číslo, …). Klient nikdy nerozhoduje o výsledku.
Princip a pravidla architektury jsou v `ARCHITECTURE.md`, jak hru spustit v `README.md`.

## Kam sáhnout, když chceš…

| Chci změnit… | Kam |
|---|---|
| texty/hodnoty karet (postavy, lokace, existence) | `server/modes/vydrz/data/` (`mista.txt`, `prezdivky.txt` jsou dočasné seznamy názvů; postavy a jejich staty jsou v `tools/generate_vydrz_data.py`, výsledek `vydrz.json`) |
| počet kusů chlastu (průměr/odchylka) | `drink_count` v `vydrz.json` (generuje `tools/generate_vydrz_data.py`) |
| pravidla bodování, remízy | `server/modes/vydrz/scoring.py` |
| rozdání karet na začátku | `server/modes/vydrz/dealing.py` |
| průběh hry „Výdrž (automaticky)“ | `server/modes/vydrz/game.py` |
| průběh hry „Výdrž (ručně)“ | `server/modes/vydrz_manual/game.py` |
| seznam módů a variant v lobby | `server/modes/__init__.py` |
| zprávy klient ↔ server | `networking/protocol.py` (tvary) a `networking/room.py` (obsluha) |
| připojování, odpojení, návrat do hry | `networking/room.py` (`join`, `reconnect`, `handle_disconnect`), `networking/client.py` (časovač), `client/js/core/socket.js` |
| vzhled karet, vlajky | `client/js/vydrz/cards.js` + `client/css/vydrz.css` |
| stůl v automatickém módu | `client/js/vydrz/view.js`, tabulka počítání `result-overlay.js` |
| stůl v ručním módu (plán, číselník, výsledky) | `client/js/vydrz/manual-view.js`, `numpad.js` |
| balíčky karet a zbývající chlast | `client/js/vydrz/piles.js` |
| animace rozdávání / vítěze, zvuky | `deal-intro.js`, `winner-splash.js`, `sound.js` |
| nastavení na zařízení (rychlost, zvuk) | `client/js/vydrz/settings.js` |
| spuštění pro hosta (tunel, Windows) | `start.sh`, `start.ps1` + `start.bat` |

## Adresáře

```
server/
  modes/               herní módy (čisté Python, bez sítě)
    __init__.py        MODES: rezba (zatím neimplementováno), vydrz, vydrz_rucne
    vydrz/             data/ (JSON + seznamy názvů), loader, models, dealing, scoring, game
    vydrz_manual/      ruční varianta: vlastní engine, sdílí data a rozdání s vydrz/
  game/, cards/, effects/   starší engine pro budoucí mód Řežba (karty z JSON)
networking/            FastAPI app (server.py), místnosti (room.py), klient/spojení (client.py), zprávy (protocol.py)
client/
  index.html           všechny panely a překryvy (česky)
  css/                 main.css (základ, lobby), vydrz.css (Výdrž)
  js/
    app.js             start, zapojení formulářů, odpojení/návrat, ukázka (mock)
    core/              socket.js (spojení, heartbeat), store.js (stav), events.js (sběrnice)
    vydrz/             pohledy a animace Výdrže (viz tabulka výše)
    ui/                zvětšení karty, potvrzovací dialog
    game/              starý pohled pro Řežbu (prototyp)
    mock/              falešný server v prohlížeči (?mock=1, tlačítko „Vyzkoušet ukázku“)
tools/                 generate_vydrz_data.py (data), build_preview.py (jednosouborový náhled)
tests/                 pytest: engine, protokol, WebSocket; tests/e2e = Playwright (živý server)
```

## Toky, které se hodí znát

- **Stav:** server po každé akci pošle každému hráči vlastní snímek (`vydrz_state`). Karty v ruce jsou veřejné,
  skrytá je jen volba/plán do vyhodnocení a pořadí dvojic. Klient snímek jen vykreslí.
- **Náhoda:** jeden seedovaný `random.Random` na hru; míchá balíčky a určuje počet kusů chlastu.
  Nic jiného v pravidlech náhodné není. (Tlačítko „Rozdělit náhodně“ v ručním plánu míchá jen návrh plánu v prohlížeči.)
- **Čekání na hráče:** hra jde dál, až když jednali všichni; odešlý hráč se hraje automaticky (`mark_away`).
- **Konec hry:** po 8. kole host „Ohlásí vítěze“ (`vydrz_announce`, až potom server pošle pořadí) a může vrátit
  všechny do lobby (`return_to_lobby`).
- **Ukázka bez serveru:** `client/js/mock/` je zrcadlo serveru jen pro náhled; pravidla tam nejsou autoritativní.

## Testy

```
pytest tests --ignore=tests/e2e     # rychlé, bez prohlížeče
pytest tests/e2e                    # Playwright + živý uvicorn, pár minut
```
