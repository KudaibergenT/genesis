# Демо: interaction_fx_partial_at_cutoff, seed 216

Компания `Stakrion Sabroukrer (synthetic)`, период 2026-07-01 – 2026-07-31, базовая валюта EUR, пояс Asia/Kolkata; 617 строк журнала, 98 документов. Политика — скриптовый `reference_solver` в отдельном процессе (uid сброшен: False), режим closed.

## Шаги

  0. reset → task, manifest, policy, 100 steps (reward 0.0)
  1. list_records(invoices) → 50 rows, next_cursor=yes (reward 0.0)
  2. list_records(invoices) → 10 rows, next_cursor=none (reward 0.0)
  3. list_records(payments) → 38 rows, next_cursor=none (reward 0.0)
  4. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
  5. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
  6. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
  7. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
  8. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
  9. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 10. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 11. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 12. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 13. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 14. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 15. list_records(ledger) → 50 rows, next_cursor=yes (reward 0.0)
 16. list_records(ledger) → 17 rows, next_cursor=none (reward 0.0)
 17. list_records(fx) → 50 rows, next_cursor=yes (reward 0.0)
 18. list_records(fx) → 16 rows, next_cursor=none (reward 0.0)
 19. submit(2 findings) → {'status': 'received'} (reward 0.0)

## Выводы агента

- `fx_conversion` ['D2PN5QQVQA'] — 5483 EUR, доказательства ['2026-07-31|GBP|EUR', 'D2PN5QQVQA', 'LDHARJKNOJX']
- `period_cutoff` ['D2PN5QQVQA'] — 450322 EUR, доказательства ['D2PN5QQVQA', 'LDHARJKNOJX']

## Эталон (только у оценщика)

- `fx_conversion` ['D2PN5QQVQA'] — 5483 EUR
- `period_cutoff` ['D2PN5QQVQA'] — 450322 EUR

## Оценка (только у оценщика)

TP=2, FP=0, FN=0, F1=1.0, inspection_steps=18, reward=0.964. Политике во всех шагах была видна награда 0.0.
