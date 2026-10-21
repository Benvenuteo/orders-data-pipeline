# Testowanie i pomiary

## Warstwy sprawdzeń

| Warstwa | Zakres |
|---|---|
| Kontrakty | Braki, typy, daty, kwoty, statusy i schema drift |
| Źródło | Trwałość, równoległe zapisy, tombstones, zamrożenie, historyczne snapshoty |
| HTTP | Retry, timeout, 401, 429, budżet prób i błędne stronicowanie |
| Uzgodnienie | Zgodne liczby przy błędnych ID, zmienione wartości, osobne waluty |
| SQL Server | Upsert, rollback, checkpoint, replay, blokady, uprawnienia i odrzucenia |
| Kontenery | Build Dockerfile, Compose, inicjalizacja bazy i kompletne demo |

Testy integracyjne nie zastępują SQL Server bazą SQLite. TestClient wykonuje
prawdziwe endpointy FastAPI i operacje źródłowego SQLite w izolowanych plikach.
Testy timeoutów używają transportu HTTPX generującego błędy sieciowe.

## Lokalne testy SQL na Windows

Zainicjalizuj środowisko zgodnie z README. Następnie w PowerShell wczytaj lokalną
konfigurację bez jej wypisywania. Ten prosty loader obsługuje plik wygenerowany
przez nasze skrypty, nie dowolny format dotenv.

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$env:SQL_SERVER = 'localhost,1433'
$env:SQL_DATABASE = 'orders_local_test'
$env:RUN_SQL_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -v
```

Fixture tworzy i czyści tylko bazę kończącą się `_test`. Ochrona ma zapobiegać
przypadkowemu wykonaniu czyszczenia w zwykłej bazie projektu. Testy wymagają
administratora do przygotowania środowiska, ale sam pipeline korzysta z konta aplikacji.

## GitHub Actions

`unit` sprawdza formatowanie, lint i testy bez SQL na Linux oraz Windows.
Na Windows dodatkowo sprawdza składnię PowerShell i generowanie `.env` bez
nadpisania istniejącej konfiguracji. `sql-server` uruchamia
rzeczywisty SQL Server 2022 CU23, testy integracyjne oraz pełną demonstrację
zbudowanego obrazu i API. Raporty JUnit oraz JSON demonstracji są załączone
do uruchomienia workflow. Hasła CI są generowane losowo dopiero na jednorazowym
runnerze i maskowane w logach. Repozytorium nie zawiera danych dostępowych SQL.

## Pomiar wydajności

Na nowym środowisku zasil źródło 10 000 zamówień. Zmierz pierwszy import oraz
ponowny import bez zmian. Następnie zastosuj paczkę zmian i zmierz przyrost.

```powershell
Measure-Command { docker compose run --rm pipeline run }
docker compose run --rm pipeline status
```

Zapisz liczbę zmian, stron, czas, wersje zależności, CPU/RAM i konfigurację dysku.
Sprawdź zgodność po każdym wariancie. Nie traktuj czasu uruchomienia kontenera
jako samego czasu zapisu SQL. Nie ustalamy arbitralnego limitu wydajności bez
pomiaru na docelowym sprzęcie.

Przed zwiększeniem `MAX_EVENTS` oceń pamięć, rozmiar raw, staging, czas transakcji
i obciążenie źródła. To projekt wsadowy dla ograniczonego wolumenu, z jawnym limitem
chroniącym proces przed niekontrolowanym wzrostem pamięci.
