# genesis-auditenv

Воспроизводимая RL-среда для ИИ-аудитора: синтетические книги компании, инструменты
расследования, скрытые причинно внесённые ошибки и терминальная награда. Python 3.11,
только стандартная библиотека, без сети, GPU и платных API.

Статус: **этап 3**. Четыре типа ошибок (`duplicate_invoice`, `payment_allocation`, `fx_conversion`,
`period_cutoff`), два сценария взаимодействующих ошибок, чистые компании и отвлекающие случаи; агент
работает в отдельном процессе (JSONL), официальная проверка — офлайн в Docker одной командой.

## Официальная проверка (одна команда)

```bash
scripts/docker_run_all.sh       # docker build + run_all.py в контейнере: без сети, 2 CPU, 4 ГБ,
                                # read-only FS, cap-drop ALL; отчёт: results/docker_run_all/run_all.{json,csv}
python3.11 scripts/run_all.py   # то же без Docker (нужны pytest/hypothesis из requirements-dev.lock)
```

`run_all.py` проверяет версии Python и зависимостей, сеть и лимиты, размер решения; генерирует эпизоды
и сверяет golden-хэши; проверяет reset/step, matcher и награду; запускает четыре базовые политики
(не-oracle — в изолированных процессах); атакует песочницу; проверяет таймаут 60 с; запускает все
тесты; пишет `run_all.json` и `run_all.csv`. Код выхода 0 — только если все проверки пройдены.
Флаги: `--quick` (без stage2_eval, таймаут 5 с), `--skip-tests`, `--require-offline`, `--require-limits`.

## Изоляция агента

Агент запускается `auditenv.sandbox.runner.run_isolated(policy, seed, ...)` отдельным процессом
`python -I -B`; наблюдения и действия — JSON Lines через stdin/stdout:

```
→ {"type":"observation","observation":{...},"reward":0.0,"terminated":false,"truncated":false}
← {"action":"list_records","args":{"table":"ledger","cursor":null,"limit":50}}
→ ... {"type":"end"}
```

- Среда, эталон и оценщик живут только в родительском процессе; агенту не передаются seed эпизода,
  truth, provenance, награда в режиме eval и диагностика (TP/FP/FN, причины).
- Агент получает только имя политики и собственный `--policy-seed`; рабочий каталог — пустой временный,
  окружение — минимальное; каталог удаляется после эпизода.
- В контейнере агент работает под uid 10001, файлы truth доступны только root, корневая ФС read-only,
  сети нет. Внутри процесса агента audit hook блокирует subprocess, сокеты, ctypes, запись файлов и
  чтение вне стандартной библиотеки и кода пакета.
- Жёсткий лимит 60 с на эпизод (wall clock), RLIMIT_CPU/FSIZE/NOFILE (и AS/NPROC в Linux), по
  истечении — SIGKILL группе процессов и `truncated=true` с пустым ответом.
- Oracle требует эталон и поэтому выполняется только на стороне оценщика (`run_official`).
- Свой агент: `run_isolated("package.module:PolicyClass", ...)` — класс с `reset(seed)` и `act(observation)`.

## Быстрый старт

```bash
make venv        # Python 3.11 venv + pytest/hypothesis по хэшам (только для тестов)
make test        # весь набор тестов
make generate    # 10 train + 10 dev эпизодов в episodes/
make baselines   # базовые политики, траектории и итоги в trajectories/ (train+dev и stage2_eval)
make audit       # аудит данных → docs/data_audit.md
make reward-analysis  # анализ награды для k = 0..12 → docs/reward_analysis.md
make run-all     # scripts/run_all.py на хосте
make docker-run-all   # официальная проверка в контейнере
```

Набор `stage2_eval` — 30 эпизодов: 6 чистых, 6 с обоими сценариями взаимодействия, 6 с
принудительными FX/cut-off, остальные по умолчанию.

Число ошибок в эпизоде — от 0 до 12 по ТЗ (`max_issues`, по умолчанию 12). Чистая компания:
`config={"max_issues": 0}`. Конфигурация по умолчанию — `configs/generator_v2.json`.

Официальная формула награды не изменялась. При k ≥ 10 она допускает награду ≥ 0 для ответа из 30
выводов, содержащего всю правду; это ограничение спецификации разобрано в
[docs/reward_analysis.md](docs/reward_analysis.md). Реальная атака `flag_everything` получает −1 при
любом k.

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

## Базовые политики (режим eval)

P, R, F1 — micro по сумме TP/FP/FN всех эпизодов; return — средняя официальная награда;
inspection steps — шаги до submit.

**stage2_eval, 30 эпизодов** (`trajectories/summary_stage2_eval.json`):

| policy | P | R | F1 | return | inspection steps |
|---|---|---|---|---|---|
| oracle (только на стороне оценщика) | 1.000 | 1.000 | 1.000 | 1.000 | 0.0 |
| reference_solver (только через инструменты) | 1.000 | 1.000 | 1.000 | 0.969 | 15.5 |
| simple_reconciler | 0.191 | 0.789 | 0.307 | −0.469 | 15.5 |
| silence | 0.000 | 0.000 | 0.000 | −0.600 | 0.0 |
| random_explorer | 0.000 | 0.000 | 0.000 | −0.934 | 22.9 |
| flag_everything | 0.000 | 0.000 | 0.000 | −1.000 | 2.0 |

**train + dev, 20 эпизодов** (`trajectories/summary.json`): oracle 1.000, reference_solver 0.971,
simple_reconciler −0.515, silence −0.600, random_explorer −0.801, flag_everything −1.000.

Все компании, люди и суммы вымышлены. Это программная задача, а не аудиторское заключение.
