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

## Подключение жюри (`judge_adapter.py`)

Рабочая директория — корень проекта (`/app` в контейнере). Точки входа:

```bash
docker build -t genesis-auditenv:stage3 .
scripts/judge_container_example.sh   # полный рабочий пример: generate → reset(closed) → step → submit → score
```

Пример запускает внутри контейнера (без сети, 2 CPU, 4 ГБ, `--read-only`, `--cap-drop ALL`) ровно
такую последовательность и печатает JSON с результатом; его выполняет тест
`tests/test_docker.py::test_judge_container_example_runs`. Минимальная форма вызова:

```bash
mkdir -p jury && chmod 0777 jury
docker run --rm --network none --cpus 2 --memory 4g -v "$PWD/jury:/jury" --workdir /app \
  --entrypoint python genesis-auditenv:stage3 -c \
  'import judge_adapter as ja; ja.generate_episode(216, {}, "/jury/D16"); print(sorted(__import__("os").listdir("/jury/D16")))'
```

**Права каталога вывода.** В контейнере адаптер работает от root. При запуске с `--cap-drop ALL` у root
нет `CAP_DAC_OVERRIDE`, и он может писать только в каталог, разрешённый битами прав. Поэтому
смонтированный каталог должен быть доступен на запись всем (`chmod 0777`) или принадлежать root; без
`--cap-drop` ограничения нет. Если каталог недоступен, `generate_episode` сразу выдаёт
`PermissionError` с этой подсказкой, а не падает посреди записи. Политика агента в изолированном
раннере работает отдельно, под uid 10001, и в каталог эпизода не пишет.

**Схемы.** Машиночитаемые JSON Schema лежат в `schemas/` (ссылка на `src/auditenv/schemas/`): manifest,
policy, строки всех таблиц, truth (каноническая и принимаемая форма), provenance, findings, действия и
наблюдения адаптера, результат `score_episode`. Соответствие всех эпизодов и обмена с адаптером
проверяют `tests/test_schemas.py` и `tests/test_truth_contract.py`.

**Контракт truth.json.** Генератор пишет `{"schema_version","episode_id","issues":[...]}`. В каждой
ошибке есть поля §03 (`issue_id`, `type`, `affected_ids`, `expected_amount_minor`, `currency`,
`evidence_sets`) и явный `tolerance_minor`, равный `policy.issue_conventions[type].tolerance_minor`
(FX — 1, остальные — 0). Оценщик принимает и фикстуры только с полями §03:
- отсутствующий `tolerance_minor` = 0 (умолчание жюри);
- отсутствующий `episode_id` берётся из `manifest.json`;
- можно передать просто список ошибок.

Единственный источник τ для оценщика — ключ `tolerance_minor` в ошибке truth. Чтобы задать в фикстуре
τ=1 (пример M08), укажите `"tolerance_minor": 1`. `policy.issue_conventions` публикует для агента те же
значения, которые генератор заранее записывает в truth (FX — 1, остальные — 0), но оценщик policy не
читает. Поэтому правка policy.json не меняет допуск, а τ не может зависеть от ответа агента.

Некорректный truth даёт `TruthFormatError` со списком нарушений схемы.

**Независимая сверка (блок 2).** Правила проводок с числами из реального эпизода разобраны в
`docs/posting_rules.md`: валютный счёт, курсовая разница 7900, кредит-нота, возврат, разбитый платёж,
2400 и cut-off по местной дате. Документ пересчитан верификатором без импорта `auditenv`.
`python jury_sim/verify.py <episode_dir>...` проверяет шесть условий блока 2 для любого плоского каталога:
- равенства до внесения ошибки (книги «до» восстанавливаются из `provenance.changes`);
- расхождение после внесения;
- сумма, валюта и множество ID в truth;
- достаточность каждого набора доказательств;
- отсутствие незаявленных расхождений.

Что верификатор действительно ловит порчу, доказывает `tests/test_independent_verification.py`. В нём
спрятанная ошибка, неверная сумма, неполные доказательства, сдвиг проводки за границу периода, часовой
пояс и испорченная provenance должны давать отказ.

```python
import judge_adapter as ja
ja.generate_episode(seed, config, output_dir)   # config — конфигурация генератора команды (см. ниже)
env = ja.make_env()
obs, info = env.reset(seed, {"mode": "closed", "episode_dir": output_dir, "team_config": {}})
obs, reward, terminated, truncated, info = env.step({"name": "list_records",
                                                     "args": {"table": "ledger", "cursor": None, "limit": 50}})
ja.score_episode(output_dir, findings_json, inspection_steps)
```

- `generate_episode` пишет в `output_dir` восемь файлов: `manifest.json`, `ledger.csv`, `invoices.jsonl`,
  `payments.jsonl`, `fx.csv`, `policy.json` и закрытые `truth.json`, `provenance.json`.
- `reset` в режиме `closed` с `episode_dir` загружает только открытые файлы (эталон в среду не
  загружается вообще); `train` с `episode_dir` дополнительно читает truth для терминальной награды;
  без `episode_dir` эпизод генерируется из `seed` и `team_config`. Повторный reset начинает тот же эпизод.
- Действия: `{"name": ..., "args": {...}}`; у submit `{"findings": <findings.json>}`. Наблюдения:
  первое `{"task","manifest","policy","remaining_steps"}`, далее `{"result","error","remaining_steps"}`.
  Результаты: `list_records` → `{"records", "next_cursor"}`; `get_record` / `get_document` → сама
  запись / документ; `calculator` → десятичная строка (`"0.3"`); `submit` → `{"accepted": true}` либо
  ошибка `invalid_submission` (только структура, без оценки правильности).
- Таблицы: `ledger` (id `row_id`), `invoices` и `payments` (id `doc_id`), `fx` (id
  `date|source_currency|target_currency`, например `2026-03-14|EUR|KZT`). Отсутствующая ссылка — всегда
  явный null: в CSV токен `null` (без кавычек), в JSON/JSONL и наблюдениях — JSON `null`. Пустое поле в
  CSV — ошибка формата (загрузчик её отвергает); `null` допустим только в `entity_id`, `invoice_id`,
  `payment_id` журнала. Это же записано в `manifest.null_representation` и `policy.ledger.null_references`.
- Лимит 60 с от reset: шаг после истечения возвращает `truncated=true`, пустые findings.
  Submit — одно из 100 действий, в `inspection_steps` не входит.
- Закрытый режим: `reward` всегда числовой 0; после эпизода `env.submitted_findings()` и
  `env.inspection_steps()` доступны только процессу жюри.
- `score_episode` → `{"tp","fp","fn","precision","recall","f1","return"}`; невалидный submit →
  `return = -1`, остальные `null`, ошибка в журнале (`logging` `auditenv.judge`, `ja.score_errors()`).

**Схема truth.json:** `{"schema_version","episode_id","issues":[{"issue_id","type","affected_ids",
"expected_amount_minor","currency","tolerance_minor","evidence_sets"}]}`; `evidence_sets` — список
допустимых наборов (вывод должен включать хотя бы один целиком). **provenance.json:** seed, полный
config и его хэш, версия генератора, `mutations` (оператор, вариант, `params` и `params_surface` в
видимых ID, связь ошибок сценария), `changes` — все добавленные/удалённые/изменённые записи журнала и
документы с полными значениями до и после (обратное применение даёт книги до инъекции:
`auditenv.audit.pre_injection_books`), `id_map` (внутренний ключ → видимый ID).

**Matcher** (`src/auditenv/evaluator/matcher.py`, `schema.py`) следует опубликованным соглашениям:
ID сравниваются точно; `affected_ids` и `evidence_ids` — множества (лимит 10 проверяется до
удаления повторов); все evidence_ids должны существовать в открытых данных; валюта и тип — точное
сравнение строк; неизвестный тип, любая строка валюты, отрицательная сумма — FP, а не авария;
неверный тип значения, отсутствующее поле, > 30 выводов, > 10 доказательств, explanation > 1000
символов — невалидный submit (−1); максимальное паросочетание с детерминированным выбором по
(issue_id, индекс вывода); `explanation` не читается.

**Четыре типа ошибок и допуски:**

| тип | affected_ids | сумма и валюта | τ | допустимые доказательства |
|---|---|---|---|---|
| `duplicate_invoice` | doc_id обеих копий счёта | total_minor, валюта счёта | 0 | оба doc_id + по одной проводке (AR или выручка) каждой копии |
| `payment_allocation` | doc_id платежа | сумма в 2400 по платежу, валюта учёта | 0 | doc_id платежа + его строка 2400 |
| `fx_conversion` | doc_id документа | \|проведено − convert(сумма, курс policy)\|, валюта учёта | 1 | doc_id + строка проводки + курс policy (`date\|src\|tgt`) |
| `period_cutoff` | doc_id документа | сумма ошибочно датированной проводки, валюта учёта | 0 | doc_id + эта строка |

**Матрица D01–D20** — конфигурации в `configs/scenarios/` (передаются как `config` в
`generate_episode`):

| эпизоды | конфигурация | что гарантирует генератор |
|---|---|---|
| D01–D05 | `clean.json` (`{"max_issues": 0}`) | truth пуст; в каждой компании есть разбитый платёж, возврат (кредит-нота + отрицательный платёж), законные повторяющиеся суммы (еженедельные подписки), валютный документ поздно вечером последнего дня, документы следующего периода в окне cut-off |
| D06–D08 | `duplicate_invoice.json` | одна ошибка-дубль |
| D09–D11 | `payment_allocation_missing.json`, `payment_allocation_partial.json` | пропуск / частичное распределение |
| D12–D13 | `fx_conversion.json` | курс другой даты, искажённый курс или ошибка разрядности |
| D14–D15 | `period_cutoff.json` | счёт/поступление в чужом периоде, включая ошибку часового пояса |
| D16–D18 | `interaction_fx_partial_at_cutoff.json` | частичный валютный платёж после полуночи последнего дня: cut-off + FX |
| D19–D20 | `interaction_allocation_with_fx.json` | неполное распределение валютного платежа + неверный курс |

Самопроверка по опубликованным определениям: `tests/test_judge_checks.py` (C01–C20, тесты 1–20
блока 3, M01–M12, матрица D01–D20). Парные проверки P01–P20 отдельной командой
`python3.11 scripts/paired_checks.py` (результаты в `results/paired_checks/`): эпизоды D01–D20
генерируются через адаптер, копия получает перестановку строк журнала, документов и курсов и/или
согласованную замену `row_id`, `doc_id`, `invoice_id`, `payment_id`, `entity_id` во всех файлах,
truth и provenance плюс новое название компании; oracle отвечает новыми ID, эталонный решатель
работает в изолированном процессе; сравниваются число и типы ошибок, `affected_ids` после обратного
отображения, F1, награда и повторяемость (`src/auditenv/paired.py`, `tests/test_paired.py`).

**Симулятор жюри.** `python3.11 jury_sim/run.py` (отчёт `results/jury_sim/`) — независимая реализация
опубликованной процедуры, которая не импортирует пакет `auditenv`: генерирует D01–D20 через
`judge_adapter`, сверяет блок 2 собственным кодом по документированным проводкам и policy
(равенства до инъекции по `provenance.changes`, заявленное расхождение, достаточность каждого набора
доказательств, формат null), проверяет 20 тестов блока 3 и 20 парных проверок собственным
преобразователем, а четыре политики организаторов (случайное исследование с фиксированным seed,
простая сверка, массовая пометка, oracle) запускает в отдельных процессах `python -I`, которые не
могут импортировать оценщик (oracle — на стороне жюри).

**Демо.** `python3.11 scripts/demo.py` — скриптовый аудитор в изолированном процессе решает эпизод
со взаимодействующими ошибками; транскрипт сохраняется в `docs/demo_transcript.md`.

**Архитектуры.** `PLATFORM=linux/amd64 scripts/docker_run_all.sh` собирает и проверяет образ для
другой архитектуры (на Apple Silicon — под эмуляцией). Нативная проверка arm64 и x86-64 —
`.github/workflows/docker-multiarch.yml` (раннеры `ubuntu-24.04` и `ubuntu-24.04-arm`).

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
