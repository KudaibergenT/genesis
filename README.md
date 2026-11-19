# genesis-auditenv

Воспроизводимая RL-среда для ИИ-аудитора: синтетические книги компании, инструменты
расследования, скрытые причинно внесённые ошибки и терминальная награда. Python 3.11,
только стандартная библиотека, без сети, GPU и платных API.

Статус: **этап 1** — контракт среды и два типа ошибок (`duplicate_invoice`,
`payment_allocation`). `fx_conversion` и `period_cutoff` описаны в схемах и принимаются
в ответах, но генератор их пока не вносит (этап 2).

## Быстрый старт

```bash
make venv        # Python 3.11 venv + pytest/hypothesis (только для тестов)
make test        # весь набор тестов
make generate    # 10 train + 10 dev эпизодов в episodes/
make baselines   # базовые политики, траектории и итоги в trajectories/
```

Без make: `PYTHONPATH=src python3.11 -m auditenv --help`.

## Контракт

```python
from auditenv.env import AuditEnv
env = AuditEnv()
obs, info = env.reset(seed=1002, config={"mode": "train"})   # или "eval"
obs, reward, terminated, truncated, info = env.step(
    {"action": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
```

| Действие | Аргументы |
|---|---|
| `list_records` | `table` ∈ ledger/invoices/payments/fx, `cursor` (null или `next_cursor`), `limit` 1..50 (больше — обрезается) |
| `get_record` | `table`, `id` — row_id / doc_id / `date\|source\|target` для fx |
| `get_document` | `doc_id` счёта или платежа |
| `calculator` | `expression` — только десятичные числа, `+ - * /`, скобки |
| `submit` | `{episode_id, findings: [{type, affected_ids, claimed_amount_minor, currency, evidence_ids, explanation}]}` |

- Максимум 100 шагов, включая `submit`. Любое действие, в том числе ошибочное, тратит шаг.
- `submit` → `terminated=true`. Исчерпание шагов или `timeout()` → `truncated=true`, ответ пустой.
- Награда по ходу эпизода 0. Терминальная: `clip(2·F1 − 1 − 0.002·inspection_steps, −1, 1)`,
  где `inspection_steps` — шаги до `submit` (при усечении — все шаги).
- `mode="train"`: терминальная награда возвращается политике. `mode="eval"`: политика всегда
  видит 0, ошибки схемы не раскрываются, итог доступен только оценщику (`env.closed_result()`).
- Неверная структура ответа → эпизод завершается с наградой −1.

## Устройство

```
src/auditenv/
  generator/   мир → события → журнал → мутации с provenance → эталон → рендер (ID и порядок последними)
  checks.py    независимая проверка ТОЛЬКО по открытым файлам (не импортирует generator)
  env/         контракт reset/step, инструменты, калькулятор без eval
  evaluator/   строгая схема, matcher, награда (держит truth; агенту недоступен)
  policies/    random_explorer, simple_reconciler, flag_everything, oracle (+ silence, reference_solver)
  runner.py    прогон эпизода, траектории JSONL отдельно от итогов оценщика
episodes/{train,dev}/seed_N/{books,truth}   открытые эпизоды; index.json — хэши для golden-теста
trajectories/  trajectories_<policy>.jsonl, results_<policy>.jsonl, summary.json
```

Подробности и принятые трактовки ТЗ — в [docs/design_note.md](docs/design_note.md),
вопросы организаторам — в [docs/open_questions.md](docs/open_questions.md).

## Базовые политики (train + dev, 20 эпизодов, режим eval)

| policy | return | F1 | шаги |
|---|---|---|---|
| oracle (только на стороне оценщика) | 1.000 | 1.000 | 1 |
| reference_solver (только через инструменты) | 0.969 | 1.000 | 16.5 |
| simple_reconciler | −0.519 | 0.251 | 15.1 |
| silence | −0.500 | 0.250 | 1 |
| random_explorer | −0.703 | 0.150 | 19.8 |
| flag_everything | −1.000 | 0.000 | 3 |

Средние P и R занижены чистыми эпизодами: по ТЗ при нулевом знаменателе P = R = 0, хотя F1 = 1.

Все компании, люди и суммы вымышлены. Это программная задача, а не аудиторское заключение.
