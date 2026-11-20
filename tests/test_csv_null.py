"""One documented representation of missing references: CSV token `null`, JSON null."""

import csv
import io
import json
import os

import pytest

from auditenv.books import LEDGER_NULLABLE, NULL_TOKEN, books_files, load_books, write_books
from auditenv.env import AuditEnv

from conftest import episode


def ledger_text(directory):
    with open(os.path.join(directory, "ledger.csv"), encoding="utf-8") as fh:
        return fh.read()


def test_missing_references_are_written_as_the_null_token(tmp_path):
    ep = episode(3)
    write_books(ep.public, str(tmp_path))
    rows = list(csv.DictReader(io.StringIO(ledger_text(tmp_path))))
    assert rows and all(v != "" for r in rows for v in r.values())
    nulls = [r for r in rows if NULL_TOKEN in (r["entity_id"], r["invoice_id"], r["payment_id"])]
    expected = [r for r in ep.public.ledger if any(r[c] is None for c in LEDGER_NULLABLE)]
    assert len(nulls) == len(expected) > 0
    for r in rows:
        for col in r:
            if r[col] == NULL_TOKEN:
                assert col in LEDGER_NULLABLE
    for line in open(tmp_path / "invoices.jsonl", encoding="utf-8"):
        assert '"null"' not in line
    payments = [json.loads(line) for line in open(tmp_path / "payments.jsonl", encoding="utf-8")]
    assert any(p["invoice_refs"] is None for p in payments)


def test_write_read_write_round_trip_is_exact(tmp_path):
    ep = episode(7)
    first, second = tmp_path / "a", tmp_path / "b"
    write_books(ep.public, str(first))
    loaded = load_books(str(first))
    assert loaded.ledger == ep.public.ledger and loaded.invoices == ep.public.invoices
    assert loaded.payments == ep.public.payments and loaded.fx == ep.public.fx
    write_books(loaded, str(second))
    for name in os.listdir(first):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert books_files(load_books(str(second))) == books_files(ep.public)


def test_reader_maps_null_to_none_and_observations_use_json_null(tmp_path):
    ep = episode(3)
    write_books(ep.public, str(tmp_path))
    loaded = load_books(str(tmp_path))
    nullable = [r for r in loaded.ledger if r["invoice_id"] is None]
    assert nullable and all(r["invoice_id"] is None for r in nullable)
    env = AuditEnv(episode_factory=lambda s, c: ep)
    env.reset(3)
    target = nullable[0]["row_id"]
    obs = env.step({"action": "get_record", "args": {"table": "ledger", "id": target}})[0]
    assert '"invoice_id":null' in json.dumps(obs, separators=(",", ":"))


@pytest.mark.parametrize("bad, message", [
    (lambda line: line.replace(",null,", ",,", 1), "empty field"),
    (lambda line: line.replace(",KZT,", ",null,", 1) if ",KZT," in line else line.replace(",USD,", ",null,", 1).replace(",EUR,", ",null,", 1), "cannot be null"),
])
def test_reader_rejects_empty_fields_and_nulls_in_required_columns(tmp_path, bad, message):
    ep = episode(3)
    write_books(ep.public, str(tmp_path))
    lines = ledger_text(tmp_path).splitlines(keepends=True)
    target = next(i for i, line in enumerate(lines[1:], start=1) if ",null," in line)
    lines[target] = bad(lines[target])
    (tmp_path / "ledger.csv").write_text("".join(lines), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_books(str(tmp_path))


def test_writer_refuses_values_that_would_be_ambiguous(tmp_path):
    ep = episode(3)
    public = ep.public
    bad_row = dict(public.ledger[0], invoice_id="")
    public_bad = type(public)(public.manifest, public.policy, [bad_row] + public.ledger[1:], public.invoices,
                              public.payments, public.fx)
    with pytest.raises(ValueError):
        write_books(public_bad, str(tmp_path))
    literal = dict(public.ledger[0], entity_id=NULL_TOKEN)
    with pytest.raises(ValueError):
        write_books(type(public)(public.manifest, public.policy, [literal], public.invoices, public.payments, public.fx),
                    str(tmp_path / "x"))


def test_null_representation_is_documented_in_manifest_and_policy():
    ep = episode(3)
    assert "token null in CSV" in ep.public.manifest["null_representation"]
    assert "CSV token null" in ep.public.policy["ledger"]["null_references"]
