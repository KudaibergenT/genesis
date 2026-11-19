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
| train/1001 | 0 | — | — | 650 | 107 | Asia/Tokyo | USD | ROUND_HALF_UP | 0 |
| train/1002 | 7 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×1 | fx_partial_at_cutoff | 443 | 90 | UTC | EUR | ROUND_HALF_EVEN | 0 |
| train/1003 | 8 | duplicate_invoice×1, fx_conversion×2, payment_allocation×3, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 463 | 71 | Asia/Almaty | USD | ROUND_HALF_UP | 0 |
| train/1004 | 0 | — | — | 462 | 103 | America/Bogota | EUR | ROUND_HALF_EVEN | 0 |
| train/1005 | 7 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×1 | allocation_with_fx, fx_partial_at_cutoff | 703 | 121 | Asia/Kolkata | USD | ROUND_HALF_EVEN | 0 |
| train/1006 | 5 | duplicate_invoice×1, fx_conversion×2, payment_allocation×1, period_cutoff×1 | — | 535 | 64 | UTC | KZT | ROUND_HALF_EVEN | 0 |
| train/1007 | 2 | payment_allocation×1, period_cutoff×1 | — | 521 | 84 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| train/1008 | 3 | duplicate_invoice×1, fx_conversion×1, payment_allocation×1 | allocation_with_fx | 345 | 65 | Asia/Almaty | USD | ROUND_HALF_EVEN | 0 |
| train/1009 | 8 | duplicate_invoice×2, fx_conversion×3, payment_allocation×1, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 493 | 107 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| train/1010 | 7 | duplicate_invoice×2, fx_conversion×2, period_cutoff×3 | fx_partial_at_cutoff | 386 | 72 | Asia/Dubai | USD | ROUND_HALF_EVEN | 0 |
| dev/2001 | 0 | — | — | 608 | 112 | Asia/Almaty | EUR | ROUND_HALF_UP | 0 |
| dev/2002 | 9 | duplicate_invoice×2, fx_conversion×2, payment_allocation×2, period_cutoff×3 | fx_partial_at_cutoff | 496 | 62 | Asia/Dubai | USD | ROUND_HALF_UP | 0 |
| dev/2003 | 7 | duplicate_invoice×1, fx_conversion×3, payment_allocation×1, period_cutoff×2 | allocation_with_fx, fx_partial_at_cutoff | 561 | 65 | Asia/Dubai | USD | ROUND_HALF_UP | 0 |
| dev/2004 | 0 | — | — | 449 | 71 | Asia/Kolkata | USD | ROUND_HALF_EVEN | 0 |
| dev/2005 | 6 | duplicate_invoice×2, fx_conversion×2, period_cutoff×2 | — | 398 | 72 | UTC | EUR | ROUND_HALF_UP | 0 |
| dev/2006 | 3 | duplicate_invoice×2, fx_conversion×1 | — | 459 | 61 | UTC | KZT | ROUND_HALF_UP | 0 |
| dev/2007 | 3 | fx_conversion×1, payment_allocation×1, period_cutoff×1 | fx_partial_at_cutoff | 633 | 91 | UTC | USD | ROUND_HALF_EVEN | 0 |
| dev/2008 | 5 | duplicate_invoice×1, fx_conversion×2, payment_allocation×1, period_cutoff×1 | allocation_with_fx | 584 | 113 | Asia/Almaty | EUR | ROUND_HALF_EVEN | 0 |
| dev/2009 | 5 | duplicate_invoice×2, fx_conversion×1, payment_allocation×2 | — | 391 | 58 | America/Bogota | KZT | ROUND_HALF_EVEN | 0 |
| dev/2010 | 7 | fx_conversion×2, payment_allocation×3, period_cutoff×2 | allocation_with_fx | 620 | 97 | Asia/Kolkata | EUR | ROUND_HALF_UP | 0 |

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
| duplicate_invoice/new_number | 51 |
| duplicate_invoice/same_number | 55 |
| fx_conversion/invoice_wrong_date | 22 |
| fx_conversion/invoice_wrong_rate | 17 |
| fx_conversion/invoice_wrong_scale | 7 |
| fx_conversion/receipt_wrong_date | 138 |
| fx_conversion/receipt_wrong_rate | 24 |
| payment_allocation/missing | 67 |
| payment_allocation/partial | 77 |
| period_cutoff/early | 21 |
| period_cutoff/late | 14 |
| period_cutoff/receipt_early | 76 |
| period_cutoff/tz_shift | 16 |

Сценарии взаимодействия в сгенерированном наборе: allocation_with_fx — 55 эпизодов, fx_partial_at_cutoff — 55 эпизодов

