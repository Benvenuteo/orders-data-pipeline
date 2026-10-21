# Architektura, kontrakt i decyzje

## Źródło i kolejność zmian

SQLite przechowuje aktualne zamówienia i pełne after-images w `changes`.
`BEGIN IMMEDIATE` serializuje zapisy: zdarzenie i stan zamówienia zatwierdzają się
razem. Klucz zmiany jest rosnący w kolejności zatwierdzeń. API nie pozwala
edytować ani usuwać historii. UUID źródła jest trwały; odtworzenie nowej bazy
otrzymuje nowy UUID i nie może nadpisać istniejącego celu.

| Endpoint | Znaczenie |
|---|---|
| `GET /health` | Gotowość API, bez danych biznesowych |
| `GET /v1/boundary` | UUID źródła, ostatnia zmiana i zamrożenie |
| `GET /v1/changes?after=L&until=H&limit=N` | Zmiany w `(L,H]`, keyset pagination |
| `GET /v1/snapshot?at=H&after_order_id=I&limit=N` | Stan na `H`, łącznie z usunięciami |
| `POST /v1/orders` | Nowe zamówienie |
| `PATCH /v1/orders/{order_id}` | Nowa wersja lub tombstone |
| `POST /admin/seed` | Zasilenie wyłącznie pustego źródła |
| `POST /admin/freeze` | Zablokowanie/odblokowanie zapisów biznesowych |
| `POST /admin/fault` | Skończony scenariusz awarii transportu |

Poza `/health` operacje wymagają `X-API-Key`. To uwierzytelnienie do lokalnego
laboratorium; role administracyjne i konsumenckie są tutaj współdzielone.
W środowisku firmowym wymagają rozdzielenia i osobnych uprawnień.

Przykładowa odpowiedź zmian:

```json
{
  "source_id": "10592036-c072-4446-99ea-c3dc55e8f840",
  "until": 1,
  "items": [{
    "change_id": 1,
    "order": {
      "order_id": 100,
      "customer_id": 10,
      "total_gross": "123.45",
      "currency": "PLN",
      "status": "paid",
      "updated_at": "2026-01-01T10:00:00Z",
      "is_deleted": false
    }
  }],
  "next_after": 1,
  "has_more": false
}
```

## Mapowanie

| API | SQL Server | Reguła |
|---|---|---|
| `order.order_id` | `core.orders.order_id` | Dodatni bigint, klucz biznesowy |
| `order.customer_id` | `customer_id` | Dodatni bigint; syntetyczny identyfikator |
| `order.total_gross` | `decimal(14,2)` | Tekst z dokładnie dwiema cyframi po kropce, bez float |
| `order.currency` | `char(3)` | Usunięcie skrajnych spacji, duże litery, PLN/EUR/USD |
| `order.status` | `varchar(12)` | Spacje i wielkość liter; new/paid/shipped/cancelled |
| `order.updated_at` | `datetime2(6)` | Wymagana strefa; przechowywanie w UTC |
| `order.is_deleted` | `bit` | Zachowanie wiersza, wykluczenie z widoków aktywnych |
| `change_id` | `last_change_id` | Wersja źródłowa; starsza nie zastępuje nowszej |
| UUID uruchomienia | `last_run_id` | Powiązanie wyniku z partią |

Nie imputujemy brakujących pól. Kwota `0.00` jest poprawna, a wysoka poprawna
kwota zostaje zachowana. Nieznany status, pole dodatkowe, błędna data i kwota
w złym formacie blokują partię. Nie sumujemy różnych walut.

## Przebieg uruchomienia

1. Zdobądź sesyjną blokadę w docelowej bazie. Oznacz osierocone `running` jako
   `failed/Interrupted`; dopiero zdobycie blokady dowodzi, że poprzedni właściciel zniknął.
2. Zapisz uruchomienie i odczytaj checkpoint `L`.
3. Ustal UUID i granicę `H`. Wykryj reset lub zmianę tożsamości źródła.
4. Pobierz `(L,H]`. Każdą odpowiedź zapisz przed parsowaniem. Sprawdzaj postęp
   kursora, rosnące identyfikatory, granicę, tożsamość i limit wielkości partii.
5. Po kompletnym pobraniu waliduj wszystkie zdarzenia i zachowaj odrzucenia.
   Nieprawidłowe zdarzenie blokuje publikację, również gdy późniejsze zdarzenie
   dotyczy tego samego zamówienia.
6. Zapisz ostatni poprawny after-image każdego zamówienia do staging z `run_id`.
7. W jednej transakcji zaktualizuj/dodaj `core.orders`, przesuń checkpoint i oznacz sukces.
8. Zwolnij blokadę. Dane raw, staging i historia pozostają do diagnostyki.

Nie utrzymujemy transakcji SQL z zapisami biznesowymi podczas oczekiwania na HTTP.
Sesyjna blokada pozostaje aktywna między krótkimi transakcjami. Nie używamy
`MERGE`; zestawowy `UPDATE` i `INSERT` przy jednej blokadzie upraszczają kontrolę
równoległości. Unikalny klucz zapewnia dodatkowe zabezpieczenie przed duplikatami.

## Replay

Replay jest offline i dotyczy wyłącznie nieudanego uruchomienia z kompletnym
raw, którego dolna granica nadal odpowiada obecnemu checkpointowi. Tworzy nowy
`run_id` z `parent_run_id`. Ponownie waliduje dane i publikuje je transakcyjnie.
Replay nie naprawia błędnego payloadu: po błędzie transportowej walidacji wykonaj
świeży import; po poprawce kodu transformacji można przeliczyć zachowane raw.

## Złożoność i ograniczenia

Dla `E` zmian i `K` zmienionych zamówień walidacja zajmuje O(E), a mapa ostatnich
wersji O(K) pamięci. Implementacja odczytuje raw do pamięci do ponownej walidacji,
więc całkowite zużycie jest O(E); chroni je `MAX_EVENTS`. SQL wykonuje zapis
zestawowo, korzystając z klucza staging i klucza docelowego.

Snapshot źródła używa funkcji okna nad historią zmian. To prosty, audytowalny
wybór dla laboratorium; przy dużej historii potrzebna byłaby optymalizacja
snapshotów lub wersjonowanej tabeli stanu. Brak retencji historii jest jawnym
założeniem źródła.

Surowe dane mają własne uprawnienia i mogą zawierać odrzucone wartości. W projekcie
są syntetyczne. Konto aplikacji nie ma DDL, kasowania `core.orders` ani uprawnień `sa`.
Hasło administratora otrzymuje tylko polecenie inicjalizacji i sam SQL Server.
