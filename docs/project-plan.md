# Plan wykonania i odbioru

| Krok | Co i po co | Ryzyka | Technologie | Kryterium odbioru |
|---|---|---|---|---|
| 1 | Ustal zakres i definicję zamówienia | Różne definicje sprzedaży | Markdown | Jeden wiersz = jedno zamówienie; jawne waluty i statusy |
| 2 | Opisz kontrakt źródła | Błędne typy i znaczniki czasu | Pydantic, OpenAPI | Niepoprawne dane są odrzucane |
| 3 | Odtwarzalne środowisko | Porty, sekrety, gotowość bazy | Docker Compose, Git | Środowisko startuje według README |
| 4 | Kontrolowane źródło testowe | Zmiany podczas pobierania, utrata historii | FastAPI, SQLite | Trwały UUID i rejestr zmian |
| 5 | Warstwy i metadane | Niejasna przyczyna błędu | SQL Server, T-SQL | Raw, staging, core, quality, control |
| 6 | Pełny import | Niepobrana strona, limit HTTP | HTTPX | Cały ograniczony przedział trafia do raw |
| 7 | Walidacja i normalizacja | Braki, float, nieznany status | Pydantic, Decimal | Odrzucenia mają przyczynę; brak częściowej publikacji |
| 8 | Idempotentny zapis | Duplikaty i stara wersja | Indeksy, transakcje SQL | Powtórka nie zmienia wyniku |
| 9 | Import przyrostowy | Zbyt wczesny checkpoint, usunięcia | Feed zmian, tabela stanu | Aktualizacje i tombstones trafiają do celu |
| 10 | Odtwarzanie po awarii | Częściowy zapis i równoległość | Retry, applock, replay | Awaria przed commit nie zmienia core |
| 11 | Uzgodnienie | Zgodna liczba, ale inne rekordy | SQL, Python | Zgodne identyfikatory, wersje, pola i kwoty |
| 12 | Automatyczne sprawdzenia | Regresja i pozorna integracja | pytest, Ruff, GitHub Actions | Testy na rzeczywistym SQL Server |
| 13 | Utrzymanie | Brak uruchomień, wygasły dostęp | JSON logs, Windows Task Scheduler | Status, health, kod zakończenia |
| 14 | Próba migracji | Zaległe zmiany i zły moment przełączenia | Freeze, reconciliation | Zamrożone źródło, brak zaległości, zgodne dane |
| 15 | Przekazanie projektu | Nieodtwarzalna demonstracja | README, runbook, raport JSON | Inna osoba wykonuje demo według instrukcji |

## Odbiór końcowy

1. Na pustym środowisku uruchom `pipeline demo`.
2. Sprawdź pełny import i brak wpływu symulowanej awarii na zatwierdzone dane.
3. Sprawdź offline replay i wynik 1010/1008.
4. Sprawdź `ready_for_cutover=true` w raporcie wykonanym podczas zamrożenia.
5. Uruchom `run` ponownie: zero nowych zapisów, te same dane i checkpoint.
6. W GitHub Actions sprawdź testy jednostkowe, integracyjne oraz rzeczywiste kontenery.

Następne rozszerzenia zależne od wymagań: osobne tabele klientów i pozycji,
kontrola relacji, retencja, większe wolumeny, historia wymiarów, chmurowe sekrety
i raport Power BI. Ich zakres wymaga osobnych reguł biznesowych.
