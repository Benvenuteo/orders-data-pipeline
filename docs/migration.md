# Próba migracji danych i przełączenia odczytu

## Zakres i warunki

Przenosimy stan zamówień z aplikacji testowej SQLite/FastAPI do SQL Server.
Odbiorcą danych jest raport odczytujący widoki `reporting`. Przy migracji całego
ERP dochodzą konfiguracja aplikacji, procesy dokumentów, uprawnienia, załączniki,
integracje i obsługa nowych zapisów po przełączeniu.

Właściciel biznesowy zatwierdza zakres, reguły mapowania i kryteria zgodności.
Osoba techniczna przygotowuje import i kontrolę. Operator odpowiada za okno
przełączenia i plan powrotu. W laboratorium role pełni jedna osoba.

## Procedura próbna

1. **Przygotuj cel.** Wykonaj `bootstrap`, sprawdź dostęp i trwałość wolumenów.
   Zapisz UUID źródła. Cel dla innego źródła powinien być osobną bazą.
2. **Wykonaj pełny import.** `pipeline run` na pustym celu odtwarza zdarzenia do
   ustalonego punktu `H`. Bieżące zapisy źródłowe mogą nadal działać.
3. **Sprawdź stan na `H`.** `pipeline reconcile` porównuje wszystkie pola.
   Poprawny wynik przy istniejących zaległościach potwierdza stan historyczny,
   ale jeszcze nie gotowość do przełączenia.
4. **Przenoś przyrosty.** Kolejne `run` doprowadzają cel do aktualnego stanu.
5. **Rozpocznij okno przełączenia.** Zatrzymaj konsumentów wymagających stabilnego
   momentu zmiany źródła i uzgodnij blokadę zapisów biznesowych.
6. **Zamroź źródło.** `pipeline source freeze` blokuje nowe zamówienia i zmiany.
7. **Przenieś końcówkę.** Wykonaj `pipeline run`.
8. **Wykonaj kontrolę cutover.** Poniższe polecenie musi zakończyć się kodem 0:

   ```powershell
   docker compose run --rm pipeline reconcile --cutover --output /reports/cutover.json
   ```

9. **Podejmij decyzję.** Wymagane `matched=true`, `source_frozen=true`,
   `lag_events=0`, `ready_for_cutover=true`. Zarchiwizuj raport i identyfikatory uruchomień.
10. **Przełącz odczyt.** Ustaw raport na SQL Server i widok
    `reporting.active_orders` lub `reporting.sales_by_currency`. Sprawdź połączenie,
    filtr statusów, walutę i przykładowe zamówienia. Potwierdź akceptację użytkownika.
11. **Zakończ próbę.** `pipeline source unfreeze` pozwala dalej ćwiczyć integrację.
    W rzeczywistej migracji właściciel procesu ustala, gdzie mają trafiać nowe zapisy.

## Rollback

Przed zmianą źródła raportu zapisz poprzednią konfigurację połączenia i punkt
odcięcia. Jeśli kontrola nie przejdzie, nie przełączaj odbiorcy; napraw problem
i powtórz import. Jeśli błąd wykryto po przełączeniu raportu:

1. Wstrzymaj odświeżanie raportu.
2. Przywróć poprzednią konfigurację odczytu.
3. Zweryfikuj wynik i dostępność danych w poprzednim źródle.
4. Zachowaj nową bazę, raw i historię do analizy.
5. Odmroź źródło po decyzji operatora i właściciela biznesowego.

Ta procedura dotyczy odbiorcy tylko do odczytu. Jeśli nowy system przyjmuje już
faktury, płatności lub inne zapisy, rollback wymaga osobnego uzgodnienia i
przeniesienia tych operacji. Sam powrót do starej bazy może je zgubić.

## Kryteria akceptacji

| Kontrola | Wymaganie |
|---|---|
| Tożsamość źródła | UUID zgodny z celem |
| Kompletność | Brak brakujących i dodatkowych identyfikatorów |
| Wartości | Zgodne wszystkie pola i identyfikatory wersji |
| Kwoty | Dokładna zgodność Decimal, osobno w każdej walucie |
| Usunięcia | Zgodne tombstones i liczba aktywnych zamówień |
| Bieżące zmiany | Zamrożone źródło, checkpoint równy jego końcowi |
| Odtwarzanie | Próba awarii i replay zakończona powodzeniem |
| Użytkownik | Poprawny odczyt po zmianie konfiguracji raportu |

## Połączenie raportu z SQL Server

Na komputerze hosta: serwer `localhost,1433`, baza z `.env`, użytkownik
`SQL_USER` i jego hasło `SQL_PASSWORD`. Wybierz widok `reporting`.
W laboratorium SQL używa samopodpisanego certyfikatu. W firmie należy zapewnić
poprawny certyfikat i osobne konto raportowe tylko do odczytu.
Plik Power BI nie jest wymagany do działania ani odbioru pipeline'u.
