"""Writes docs/posting_rules.md: one worked example per posting rule, recomputed by the clean-room
verifier (jury_sim/verify.py) from a generated clean episode. tests/test_posting_examples.py checks
that the committed document equals a fresh run, so the examples never drift from the generator.

    python scripts/posting_examples.py            # rewrite docs/posting_rules.md
    python scripts/posting_examples.py --check    # exit 1 if the document is stale
"""

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import judge_adapter as ja  # noqa: E402
from jury_sim.fileio import load  # noqa: E402
from jury_sim.verify import Books, equalities  # noqa: E402

OUT = os.path.join(ROOT, "docs", "posting_rules.md")
SEEDS = range(701, 760)


def rows_table(rows):
    lines = ["| row_id | timestamp | account | debit_minor | credit_minor | invoice_id | payment_id |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['row_id']} | {r['timestamp']} | {r['account']} | {r['debit_minor']} | {r['credit_minor']} "
                     f"| {r['invoice_id'] or 'null'} | {r['payment_id'] or 'null'} |")
    return "\n".join(lines)


def find_examples():
    found = {}
    with open(os.path.join(ROOT, "configs", "scenarios", "clean.json")) as fh:
        config = json.load(fh)
    for seed in SEEDS:
        with tempfile.TemporaryDirectory() as d:
            ja.generate_episode(seed, config, d)
            data = load(d)
        b = Books(data)
        assert equalities(data) == [], seed
        by_invoice = defaultdict(list)
        for p in data["payments"]:
            for ref in p["invoice_refs"] or []:
                by_invoice[ref["invoice_id"]].append(p)
        fx_rows = {r["payment_id"] for r in data["ledger"] if b.role.get(r["account"]) == "fx"}
        for doc in data["invoices"]:
            if "foreign_invoice" not in found and doc["currency"] != b.base and doc["total_minor"] > 0:
                found["foreign_invoice"] = (seed, data, doc)
            if "credit_note" not in found and doc["total_minor"] < 0:
                found["credit_note"] = (seed, data, doc)
            if "split" not in found and len(by_invoice[doc["invoice_id"]]) >= 2:
                found["split"] = (seed, data, doc)
        for p in data["payments"]:
            if "fx_receipt" not in found and p["payment_id"] in fx_rows and p["amount_minor"] > 0:
                found["fx_receipt"] = (seed, data, p)
            if "refund" not in found and p["amount_minor"] < 0:
                found["refund"] = (seed, data, p)
            if "unapplied" not in found and p["invoice_refs"] is None:
                found["unapplied"] = (seed, data, p)
        for doc in data["invoices"] + data["payments"]:
            ts = doc.get("issued_at") or doc["timestamp"]
            utc_day = datetime.fromisoformat(ts).astimezone(timezone.utc).date()
            if "cutoff_tz" not in found and b.day(ts) != utc_day and b.period(ts) != (
                    "current" if b.start <= utc_day <= b.end else "next" if b.end < utc_day <= b.window_end else "outside"):
                found["cutoff_tz"] = (seed, data, doc)
        if len(found) == 7:
            return found
    raise SystemExit(f"examples not found in seeds {SEEDS.start}-{SEEDS.stop - 1}: {sorted(found)}")


def render():
    ex = find_examples()
    out = ["# Правила проводок: разобранные примеры",
           "",
           "Файл сгенерирован `scripts/posting_examples.py`. Каждый пример взят из чистого эпизода",
           "(`generate_episode(seed, configs/scenarios/clean.json)`), а числа пересчитаны независимым",
           "верификатором `jury_sim/verify.py`, который не импортирует `auditenv`. Правила — те же, что в",
           "`policy.json` каждого эпизода. `tests/test_posting_examples.py` проверяет, что файл совпадает со",
           "свежим прогоном.",
           ""]

    def section(title, rule, key, body):
        seed, data, doc = ex[key]
        out.extend([f"## {title}", "", f"Правило (`policy.json`): {rule}", "",
                    f"Эпизод: seed {seed}, `{data['manifest']['episode_id']}`, базовая валюта "
                    f"{data['manifest']['base_currency']}, смещение {data['manifest']['utc_offset']}.", ""])
        out.extend(body(Books(data), data, doc))
        out.append("")

    def invoice(b, data, doc):
        rate, rate_id = b.rate(doc["currency"], doc["issued_at"])
        rows = b.invoice_postings(doc)
        value = b.convert(doc["total_minor"], doc["currency"], doc["issued_at"])
        return [f"Документ `{doc['doc_id']}` ({doc['invoice_id']}): {doc['total_minor']} минорных единиц {doc['currency']}, "
                f"`issued_at` {doc['issued_at']}, местная дата {b.day(doc['issued_at'])}.",
                f"Курс: строка fx `{rate_id}` = {rate} (последний опубликованный на местную дату или раньше).",
                f"Пересчёт: {doc['total_minor']} × {rate} × 10^({b.exp[b.base]}−{b.exp[doc['currency']]}) → округление "
                f"{data['manifest']['rounding']['mode']} = **{value}** {b.base}.", "", rows_table(rows), ""]

    def receipt(b, data, pay):
        rows = [r for r in data["ledger"] if r["payment_id"] == pay["payment_id"]]
        rate, rate_id = b.rate(pay["currency"], pay["timestamp"])
        cash = b.convert(pay["amount_minor"], pay["currency"], pay["timestamp"])
        lines = [f"Документ `{pay['doc_id']}` ({pay['payment_id']}): {pay['amount_minor']} {pay['currency']}, "
                 f"`timestamp` {pay['timestamp']}, `invoice_refs` {json.dumps(pay['invoice_refs'])}.",
                 f"Деньги по курсу даты поступления `{rate_id}` = {rate}: **{cash}** {b.base}."]
        for ref in pay["invoice_refs"] or []:
            (inv,) = [d for d in data["invoices"] if d["invoice_id"] == ref["invoice_id"]][:1]
            irate, irate_id = b.rate(inv["currency"], inv["issued_at"])
            lines.append(f"Списание AR по {ref['invoice_id']} по курсу счёта `{irate_id}` = {irate}: "
                         f"{b.convert(ref['amount_minor'], inv['currency'], inv['issued_at'], irate)} {b.base}.")
        lines += ["Разница между деньгами и списанием AR — строка 7900.", "", rows_table(rows), ""]
        return lines

    def plain(b, data, doc):
        if "issued_at" in doc:
            rows = b.invoice_postings(doc)
            head = f"Документ `{doc['doc_id']}` ({doc['invoice_id']}): total_minor {doc['total_minor']} {doc['currency']}."
        else:
            rows = [r for r in data["ledger"] if r["payment_id"] == doc["payment_id"]]
            head = (f"Документ `{doc['doc_id']}` ({doc['payment_id']}): amount_minor {doc['amount_minor']} "
                    f"{doc['currency']}, invoice_refs {json.dumps(doc['invoice_refs'])}.")
        return [head, "", rows_table(rows), ""]

    def split(b, data, doc):
        pays = [p for p in data["payments"] if any(r["invoice_id"] == doc["invoice_id"] for r in p["invoice_refs"] or [])]
        ar = [r for r in data["ledger"] if r["invoice_id"] == doc["invoice_id"] and b.role.get(r["account"]) == "ar"]
        return [f"Счёт `{doc['doc_id']}` ({doc['invoice_id']}) на {doc['total_minor']} {doc['currency']} закрыт "
                f"{len(pays)} платежами: " + ", ".join(
                    f"`{p['doc_id']}` ({sum(r['amount_minor'] for r in p['invoice_refs'] if r['invoice_id'] == doc['invoice_id'])})"
                    for p in pays) + ".",
                f"Остаток AR по счёту: {sum(b.signed(r) for r in ar)} — это **не ошибка**.", "", rows_table(ar), ""]

    def cutoff(b, data, doc):
        ts = doc.get("issued_at") or doc["timestamp"]
        utc = datetime.fromisoformat(ts).astimezone(timezone.utc)
        return [f"Документ `{doc['doc_id']}`: время {ts}; в UTC это {utc.isoformat()} (дата {utc.date()}), "
                f"местная дата {b.day(ts)}.",
                f"Период {data['manifest']['period_start']} … {data['manifest']['period_end']}, окно проверки до "
                f"{data['manifest']['review_window_end']}. По местной дате документ относится к периоду "
                f"**{b.period(ts)}**; по UTC получился бы другой ответ — так ловушка `tz_shift` отличает ошибку "
                f"от корректной записи.", ""]

    pol = ex["foreign_invoice"][1]["policy"]
    section("1. Счёт в иностранной валюте", pol["fx"]["rate_date"] + "; " + pol["fx"]["missing_date"] + "; "
            + pol["fx"]["conversion"], "foreign_invoice", invoice)
    section("2. Поступление по валютному счёту и курсовая разница", pol["fx"]["receipts"], "fx_receipt", receipt)
    section("3. Кредит-нота", pol["ledger"]["credit_notes"], "credit_note", plain)
    section("4. Возврат клиенту", pol["ledger"]["credit_notes"], "refund", plain)
    section("5. Разбитый платёж", pol["allocation"]["rule"], "split", split)
    section("6. Платёж без ссылки на счёт", pol["allocation"]["unreferenced"], "unapplied", plain)
    section("7. Отнесение к периоду по местной дате", pol["period"]["assignment"] + "; " + pol["period"]["review_window"],
            "cutoff_tz", cutoff)
    out += ["## Как ошибки нарушают эти правила", "",
            "| тип | какое равенство нарушено | сумма в truth |",
            "|---|---|---|",
            "| duplicate_invoice | два документа с одинаковым содержанием, оба проведены | total_minor копии в валюте документа |",
            "| payment_allocation | платёж ссылается на счёт, но сумма (или часть) осталась в 2400 | остаток в 2400, базовая валюта |",
            "| fx_conversion | проводка ≠ сумма × курс на местную дату документа | модуль разности «проведено − правильно», базовая валюта |",
            "| period_cutoff | проводка в другом периоде, чем местная дата документа | сумма проводки, базовая валюта |",
            "",
            "Независимая проверка любого каталога эпизода: `python jury_sim/verify.py <episode_dir>...`.",
            ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        with open(OUT, encoding="utf-8") as fh:
            return 0 if fh.read() == text else 1
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
