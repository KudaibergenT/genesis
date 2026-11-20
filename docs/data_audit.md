# Аудит данных

Файл сгенерирован `scripts/audit_episodes.py`.

Для каждого эпизода проверяется:
1. `checks.verify_episode` по открытым файлам: размеры, достаточность каждого набора доказательств,
   детектор находит ровно эталон, каждое нарушение инвариантов объяснено эталонной ошибкой;
2. внесение ошибок не ломает двойную запись (проводки сбалансированы, ссылки целы, суммы целые);
3. та же компания без ошибок (`max_issues: 0`) проходит все бухгалтерские равенства и не даёт находок;
4. provenance ↔ truth: число, тип и affected_ids каждой мутации, взаимность связей сценариев;
5. truth: суммы > 0, валюты из manifest, допуски из опубликованных конвенций, минимальность каждого
   набора доказательств (удаление любого элемента ломает вывод);
6. в открытых файлах нет внутренних ключей и ID эталона.

**Итого: 20 открытых эпизодов + 152 сгенерированных, 493 ошибок в сгенерированных, проблем найдено: 0.**

## Открытые train/dev эпизоды

| эпизод | ошибок | типы | сценарии | строк | документов | пояс | база | округление | проблемы |
|---|---|---|---|---|---|---|---|---|---|
| train/1001 | 0 | — | — | 675 | 115 | Asia/Tokyo | USD | ROUND_HALF_UP | 0 |
| train/1002 | 7 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×1 | fx_partial_at_cutoff | 441 | 90 | UTC | EUR | ROUND_HALF_EVEN | 0 |
| train/1003 | 8 | duplicate_invoice×1, fx_conversion×2, payment_allocation×3, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 467 | 72 | Asia/Almaty | USD | ROUND_HALF_UP | 0 |
| train/1004 | 0 | — | — | 482 | 111 | America/Bogota | EUR | ROUND_HALF_EVEN | 0 |
| train/1005 | 7 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×1 | allocation_with_fx, fx_partial_at_cutoff | 695 | 117 | Asia/Kolkata | USD | ROUND_HALF_EVEN | 0 |
| train/1006 | 5 | duplicate_invoice×1, fx_conversion×2, payment_allocation×1, period_cutoff×1 | — | 554 | 69 | UTC | KZT | ROUND_HALF_EVEN | 0 |
| train/1007 | 2 | payment_allocation×1, period_cutoff×1 | — | 521 | 82 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| train/1008 | 3 | duplicate_invoice×1, fx_conversion×1, payment_allocation×1 | allocation_with_fx | 348 | 66 | Asia/Almaty | USD | ROUND_HALF_EVEN | 0 |
| train/1009 | 8 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 510 | 116 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| train/1010 | 7 | duplicate_invoice×2, fx_conversion×2, period_cutoff×3 | fx_partial_at_cutoff | 383 | 68 | Asia/Dubai | USD | ROUND_HALF_EVEN | 0 |
| dev/2001 | 0 | — | — | 611 | 112 | Asia/Almaty | EUR | ROUND_HALF_UP | 0 |
| dev/2002 | 9 | duplicate_invoice×2, fx_conversion×2, payment_allocation×2, period_cutoff×3 | fx_partial_at_cutoff | 517 | 70 | Asia/Dubai | USD | ROUND_HALF_UP | 0 |
| dev/2003 | 7 | duplicate_invoice×1, fx_conversion×3, payment_allocation×1, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 571 | 70 | Asia/Dubai | USD | ROUND_HALF_UP | 0 |
| dev/2004 | 0 | — | — | 449 | 70 | Asia/Kolkata | USD | ROUND_HALF_EVEN | 0 |
| dev/2005 | 6 | duplicate_invoice×2, fx_conversion×2, period_cutoff×2 | — | 395 | 68 | UTC | EUR | ROUND_HALF_UP | 0 |
| dev/2006 | 3 | duplicate_invoice×2, fx_conversion×1 | — | 459 | 63 | UTC | KZT | ROUND_HALF_UP | 0 |
| dev/2007 | 3 | fx_conversion×1, payment_allocation×1, period_cutoff×1 | fx_partial_at_cutoff | 647 | 95 | UTC | USD | ROUND_HALF_EVEN | 0 |
| dev/2008 | 5 | duplicate_invoice×1, fx_conversion×2, payment_allocation×1, period_cutoff×1 | allocation_with_fx | 591 | 115 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| dev/2009 | 5 | duplicate_invoice×2, fx_conversion×1, payment_allocation×2 | — | 410 | 70 | America/Bogota | KZT | ROUND_HALF_EVEN | 0 |
| dev/2010 | 7 | fx_conversion×2, payment_allocation×3, period_cutoff×2 | allocation_with_fx | 610 | 95 | Asia/Kolkata | EUR | ROUND_HALF_UP | 0 |

## Сгенерированный набор (5 сидов на случай, кроме чистых по поясам)

| случай | эпизодов | ошибок всего | ошибок на эпизод | проблемы |
|---|---|---|---|---|
| clean/America/Bogota/UTC | 1 | 0 | 0 | 0 |
| clean/America/Bogota/company | 1 | 0 | 0 | 0 |
| clean/Asia/Almaty/UTC | 1 | 0 | 0 | 0 |
| clean/Asia/Almaty/company | 1 | 0 | 0 | 0 |
| clean/Asia/Dubai/UTC | 1 | 0 | 0 | 0 |
| clean/Asia/Dubai/company | 1 | 0 | 0 | 0 |
| clean/Asia/Kolkata/UTC | 1 | 0 | 0 | 0 |
| clean/Asia/Kolkata/company | 1 | 0 | 0 | 0 |
| clean/Asia/Tokyo/UTC | 1 | 0 | 0 | 0 |
| clean/Asia/Tokyo/company | 1 | 0 | 0 | 0 |
| clean/UTC/UTC | 1 | 0 | 0 | 0 |
| clean/UTC/company | 1 | 0 | 0 | 0 |
| duplicate_invoice/new_number | 5 | 5 | 1 | 0 |
| duplicate_invoice/same_number | 5 | 5 | 1 | 0 |
| fx_conversion/invoice_wrong_date | 5 | 5 | 1 | 0 |
| fx_conversion/invoice_wrong_rate | 5 | 5 | 1 | 0 |
| fx_conversion/invoice_wrong_scale | 5 | 3 | 0, 1 | 0 |
| fx_conversion/receipt_wrong_date | 5 | 5 | 1 | 0 |
| fx_conversion/receipt_wrong_rate | 5 | 5 | 1 | 0 |
| interactions | 10 | 40 | 4 | 0 |
| k=0 | 5 | 0 | 0 | 0 |
| k=1 | 5 | 5 | 1 | 0 |
| k=2 | 5 | 10 | 2 | 0 |
| k=3 | 5 | 15 | 3 | 0 |
| k=4 | 5 | 20 | 4 | 0 |
| k=5 | 5 | 25 | 5 | 0 |
| k=6 | 5 | 30 | 6 | 0 |
| k=7 | 5 | 35 | 7 | 0 |
| k=8 | 5 | 40 | 8 | 0 |
| k=9 | 5 | 45 | 9 | 0 |
| k=10 | 5 | 50 | 10 | 0 |
| k=11 | 5 | 55 | 11 | 0 |
| k=12 | 5 | 60 | 12 | 0 |
| payment_allocation/missing | 5 | 5 | 1 | 0 |
| payment_allocation/partial | 5 | 5 | 1 | 0 |
| period_cutoff/early | 5 | 5 | 1 | 0 |
| period_cutoff/late | 5 | 5 | 1 | 0 |
| period_cutoff/receipt_early | 5 | 5 | 1 | 0 |
| period_cutoff/tz_shift | 5 | 5 | 1 | 0 |

## Покрытие

| вариант | раз |
|---|---|
| duplicate_invoice/new_number | 53 |
| duplicate_invoice/same_number | 53 |
| fx_conversion/invoice_wrong_date | 18 |
| fx_conversion/invoice_wrong_rate | 11 |
| fx_conversion/invoice_wrong_scale | 9 |
| fx_conversion/receipt_wrong_date | 139 |
| fx_conversion/receipt_wrong_rate | 31 |
| payment_allocation/missing | 67 |
| payment_allocation/partial | 77 |
| period_cutoff/early | 18 |
| period_cutoff/late | 14 |
| period_cutoff/receipt_early | 80 |
| period_cutoff/tz_shift | 15 |

Сценарии взаимодействия в сгенерированном наборе: allocation_with_fx — 55 эпизодов, fx_partial_at_cutoff — 55 эпизодов

