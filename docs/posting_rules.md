# Правила проводок: разобранные примеры

Файл сгенерирован `scripts/posting_examples.py`. Каждый пример взят из чистого эпизода
(`generate_episode(seed, configs/scenarios/clean.json)`), а числа пересчитаны независимым
верификатором `jury_sim/verify.py`, который не импортирует `auditenv`. Правила — те же, что в
`policy.json` каждого эпизода. `tests/test_posting_examples.py` проверяет, что файл совпадает со
свежим прогоном.

## 1. Счёт в иностранной валюте

Правило (`policy.json`): local date of the document in the company timezone; use the latest published rate on or before that date; convert the document amount once and round to target minor units

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `DWTCEWZHNK` (FV-2026-00893): 4525000 минорных единиц CNY, `issued_at` 2026-09-02T14:03:44+05:00, местная дата 2026-09-02.
Курс: строка fx `2026-09-02|CNY|USD` = 0.137375 (последний опубликованный на местную дату или раньше).
Пересчёт: 4525000 × 0.137375 × 10^(2−2) → округление ROUND_HALF_UP = **621622** USD.

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| LIYAIXF3GV7 | 2026-09-02T14:03:44+05:00 | 1200 Accounts Receivable | 621622 | 0 | FV-2026-00893 | null |
| L7UOWVLDUMI | 2026-09-02T14:03:44+05:00 | 4000 Revenue | 0 | 621622 | FV-2026-00893 | null |


## 2. Поступление по валютному счёту и курсовая разница

Правило (`policy.json`): AR is credited at the invoice carrying rate; the difference to cash at the receipt-date rate is booked to 7900 Realized FX Gain/Loss

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `DPA2ZKTVQ4` (RCPT-202605627): 3000000 CNY, `timestamp` 2026-09-07T14:08:08+05:00, `invoice_refs` [{"amount_minor": 3000000, "invoice_id": "FV-2026-00898"}].
Деньги по курсу даты поступления `2026-09-07|CNY|USD` = 0.137788: **413364** USD.
Списание AR по FV-2026-00898 по курсу счёта `2026-09-04|CNY|USD` = 0.137532: 412596 USD.
Разница между деньгами и списанием AR — строка 7900.

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| LIASQ2IBMVF | 2026-09-07T14:08:08+05:00 | 1000 Cash at Bank | 413364 | 0 | null | RCPT-202605627 |
| L3NNKA5YS6B | 2026-09-07T14:08:08+05:00 | 1200 Accounts Receivable | 0 | 412596 | FV-2026-00898 | RCPT-202605627 |
| LCERIE6BOYS | 2026-09-07T14:08:08+05:00 | 7900 Realized FX Gain/Loss | 0 | 768 | null | RCPT-202605627 |


## 3. Кредит-нота

Правило (`policy.json`): invoices with negative total_minor are credit notes (AR credit, 4100 Sales Returns debit); payments with negative amount_minor are refunds paid to the customer

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `DEJNVCMLSC` (CN-2026-0077): total_minor -120000 USD.

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| L44WBRXRSZT | 2026-09-19T10:36:52+05:00 | 1200 Accounts Receivable | 0 | 120000 | CN-2026-0077 | null |
| LDWB3Q3YAHY | 2026-09-19T10:36:52+05:00 | 4100 Sales Returns | 120000 | 0 | CN-2026-0077 | null |


## 4. Возврат клиенту

Правило (`policy.json`): invoices with negative total_minor are credit notes (AR credit, 4100 Sales Returns debit); payments with negative amount_minor are refunds paid to the customer

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `DLB253JFHB` (RCPT-202605648): amount_minor -6000000 CNY, invoice_refs [{"amount_minor": -6000000, "invoice_id": "CN-2026-0078"}].

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| LT5H6YDEG2R | 2026-09-23T16:37:08+05:00 | 1000 Cash at Bank | 0 | 823938 | null | RCPT-202605648 |
| LRMXWFS5TC2 | 2026-09-23T16:37:08+05:00 | 1200 Accounts Receivable | 821184 | 0 | CN-2026-0078 | RCPT-202605648 |
| LZR2PUAU4EW | 2026-09-23T16:37:08+05:00 | 7900 Realized FX Gain/Loss | 2754 | 0 | null | RCPT-202605648 |


## 5. Разбитый платёж

Правило (`policy.json`): receipts are applied to invoices exactly as listed in invoice_refs

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Счёт `DVOYUJIMCY` (FV-2026-00894) на 430000 CNY закрыт 2 платежами: `DDPO2GWYTU` (227900), `DQNUGXYMNY` (202100).
Остаток AR по счёту: 0 — это **не ошибка**.

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| LSBX3CT5JPT | 2026-09-03T10:13:43+05:00 | 1200 Accounts Receivable | 59160 | 0 | FV-2026-00894 | null |
| LO5MDUGRPUT | 2026-09-11T12:22:06+05:00 | 1200 Accounts Receivable | 0 | 31355 | FV-2026-00894 | RCPT-202605632 |
| L4A4AFVWQCS | 2026-09-11T14:07:13+05:00 | 1200 Accounts Receivable | 0 | 27805 | FV-2026-00894 | RCPT-202605633 |


## 6. Платёж без ссылки на счёт

Правило (`policy.json`): receipts with null invoice_refs are held in 2400 Unapplied Customer Receipts

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `DVFRXQIOC2` (RCPT-202605626): amount_minor 3630000 CNY, invoice_refs null.

| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |
|---|---|---|---|---|---|---|
| LHQHM7DFO34 | 2026-09-06T17:48:05+05:00 | 1000 Cash at Bank | 499241 | 0 | null | RCPT-202605626 |
| L7CUGHZB2IY | 2026-09-06T17:48:05+05:00 | 2400 Unapplied Customer Receipts | 0 | 499241 | null | RCPT-202605626 |


## 7. Отнесение к периоду по местной дате

Правило (`policy.json`): an item belongs to the period that contains the local date of its document timestamp; records dated after period end up to review_window_end belong to the NEXT period and are included only for cut-off review

Эпизод: seed 701, `ep-scbvkcvhyk3yglzd`, базовая валюта USD, смещение +05:00.

Документ `D7NXRR2WKX`: время 2026-10-01T00:32:52+05:00; в UTC это 2026-09-30T19:32:52+00:00 (дата 2026-09-30), местная дата 2026-10-01.
Период 2026-09-01 … 2026-09-30, окно проверки до 2026-10-05. По местной дате документ относится к периоду **next**; по UTC получился бы другой ответ — так ловушка `tz_shift` отличает ошибку от корректной записи.


## Как ошибки нарушают эти правила

| тип | какое равенство нарушено | сумма в truth |
|---|---|---|
| duplicate_invoice | два документа с одинаковым содержанием, оба проведены | total_minor копии в валюте документа |
| payment_allocation | платёж ссылается на счёт, но сумма (или часть) осталась в 2400 | остаток в 2400, базовая валюта |
| fx_conversion | проводка ≠ сумма × курс на местную дату документа | модуль разности «проведено − правильно», базовая валюта |
| period_cutoff | проводка в другом периоде, чем местная дата документа | сумма проводки, базовая валюта |

Независимая проверка любого каталога эпизода: `python jury_sim/verify.py <episode_dir>...`.
