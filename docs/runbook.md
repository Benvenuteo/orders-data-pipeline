# Utrzymanie i rozwiązywanie problemów

## Zwykła praca

```powershell
docker compose up -d --wait sqlserver api
docker compose run --rm pipeline run
docker compose run --rm pipeline status
docker compose run --rm pipeline health
```

`status` pokazuje ostatni wynik, checkpoint i liczbę zamówień. `health` zwraca kod
`2`, jeśli ostatnie uruchomienie nie zakończyło się sukcesem albo ostatni sukces
jest starszy niż 30 minut. Pozwala to wykryć również niedziałający harmonogram.
Logi pipeline'u to JSON na stderr; dane wynikowe to JSON na stdout.

| Kod procesu | Znaczenie |
|---|---|
| 0 | Sukces; dla reconcile również spełnienie warunków kontroli |
| 1 | Błąd operacyjny, kontraktu lub danych |
| 2 | Negatywny wynik kontroli health/reconcile |
| 3 | Jawnie zamówiona symulacja awarii przed commit |

## Retry

Ponawiamy wyłącznie GET po błędach transportu i HTTP 429/500/502/503/504.
Domyślnie maksymalnie cztery próby, rosnące opóźnienie z jitterem. `Retry-After`
do 60 sekund jest respektowane; większa wartość kończy zadanie, aby następny
termin mógł spróbować później. Nie ponawiamy automatycznie zapisów źródłowych,
bo timeout nie dowodzi, że zapis się nie udał. HTTP 401 wymaga naprawy dostępu.

## Scenariusze awarii

Po zasileniu źródła, ale przed pobraniem jego najnowszych zmian:

```powershell
docker compose run --rm pipeline source fault http_429 --remaining 2
docker compose run --rm pipeline run
```

Oczekiwane: ponowienia i sukces. Gdy nie ma zmian do pobrania, pipeline nie pyta
`/v1/changes`, więc scenariusz pozostanie do następnej niepustej partii.

```powershell
docker compose run --rm pipeline source fault invalid_record --remaining 1
docker compose run --rm pipeline run
docker compose run --rm pipeline status
docker compose run --rm pipeline run
```

Oczekiwane: najpierw błąd walidacji, zapis raw i odrzucenia, niezmieniony core
i checkpoint. Następny świeży odczyt przejdzie, bo fault nie zmienia źródłowej historii.
Replay błędnego raw nadal odrzuci rekord, co jest prawidłowe.

```powershell
docker compose run --rm pipeline run --fail-before-commit
docker compose run --rm pipeline status
docker compose run --rm pipeline replay UUID_NIEUDANEGO_URUCHOMIENIA
```

Replay nie potrzebuje API. Wymaga kompletnego raw i niezmienionego checkpointu.
Jeśli w międzyczasie wykonano udany import tego przedziału, replay zostanie
odrzucony. W poleceniu wpisz UUID z `status`, bez otaczających nawiasów.

## Typowe problemy

| Objaw | Sprawdzenie i działanie |
|---|---|
| SQL niegotowy | `docker compose ps`, `docker compose logs sqlserver`; sprawdź pamięć i port 1433 |
| Błąd logowania po zmianie `.env` | Istniejący wolumen zachował stare hasła; uzgodnij je administracyjnie |
| `ValidationRejected` | Sprawdź `quality.rejected_records`, raw i kontrakt; popraw źródło lub kod |
| `CheckpointConflict` | Sprawdź UUID źródła i checkpoint; dla nowego źródła przygotuj nowy cel |
| `ConcurrentRun` | Inny proces nadal pracuje; sprawdź kontenery i historię, nie usuwaj checkpointu |
| `Interrupted` | Poprzedni proces zakończył się bez zapisania wyniku; zwykły import jest bezpieczny |
| `SourceUnavailable` | API/połączenie nie wróciło w budżecie retry; sprawdź gotowość i limit |
| `MAX_EVENTS` | Wstrzymano zbyt dużą partię; zwiększ limit dopiero po ocenie pamięci i czasu transakcji |
| `health` ma kod 2 | Ostatni błąd albo brak sukcesu ponad 30 minut; sprawdź harmonogram |
| Brak raportu na Linux | Nadaj UID kontenera uprawnienie do `reports` lub uruchom go jako UID hosta |

Komunikaty sterownika SQL mogą zawierać informacje o połączeniu, dlatego CLI
pokazuje wyłącznie typ nieoczekiwanego błędu. Rozpoznane błędy pipeline'u mają
bezpieczny opis. Nie zapisujemy nagłówków autoryzacji w logach.

## Harmonogram Windows

Po inicjalizacji i pierwszym udanym imporcie, w PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_task.ps1
Get-ScheduledTask -TaskName OrdersDataPipeline
Get-ScheduledTaskInfo -TaskName OrdersDataPipeline
```

Skrypt rejestruje zadanie co 15 minut dla zalogowanego użytkownika. Docker Desktop
musi działać. Kontenery API i SQL powinny być wcześniej uruchomione. Kolejna
instancja zadania jest pomijana, a SQL dodatkowo chroni przed innymi procesami.
Limit wykonania zadania to 14 minut; dopasuj go po zmierzeniu czasu importu.

Logi dzienne: `reports/pipeline-YYYY-MM-DD.log`. Kontroluj wynik zadania i
uruchamiaj `health` z zewnętrznego monitora, jeśli proces ma pracować stale.
Projekt nie wysyła automatycznie wiadomości do zewnętrznych osób ani usług.

Usunięcie harmonogramu:

```powershell
Unregister-ScheduledTask -TaskName OrdersDataPipeline -Confirm:$false
```

## Zatrzymanie, retencja i kopie

`docker compose down` zatrzymuje usługi i zachowuje wolumeny. Nie dodawaj `-v`,
jeżeli chcesz zachować źródło, historię i bazę docelową. `bootstrap` nie resetuje
danych i nie zmienia istniejących haseł.

Raw, staging, odrzucenia i historia nie są automatycznie kasowane. W laboratorium
to ułatwia naukę i replay. W firmie ustal retencję, wielkość kopii i wymagane
okno odtworzenia. Usuwaj staging/raw dopiero po zatwierdzonej polityce i zachowuj
dane potrzebne do replay. Zwykły wolumen Dockera nie zastępuje kopii zapasowej.
