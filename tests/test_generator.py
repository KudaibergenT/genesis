import collections
import re

import pytest

from auditenv import canon, checks
from auditenv.books import books_files, load_books
from auditenv.config import ConfigError, resolve
from auditenv.generator import generate_episode, write_episode

from conftest import CLEAN_CONFIG, episode

SEEDS = range(60)
INTERNAL_KEY_PATTERN = re.compile(r"(inv#|pay#|cust#|num#|bg#|E:|issue:|pid:)")


@pytest.mark.parametrize("seed", SEEDS)
def test_episode_passes_independent_verification(seed):
    ep = episode(seed)
    assert checks.verify_episode(ep.public, ep.truth) == []


@pytest.mark.parametrize("seed", range(0, 60, 6))
def test_files_round_trip_and_still_verify(tmp_path, seed):
    ep = episode(seed)
    write_episode(ep, str(tmp_path))
    loaded = load_books(str(tmp_path / "books"))
    assert canon.dumps(loaded.ledger) == canon.dumps(ep.public.ledger)
    assert canon.dumps(loaded.invoices) == canon.dumps(ep.public.invoices)
    assert canon.dumps(loaded.payments) == canon.dumps(ep.public.payments)
    assert checks.verify_episode(loaded, ep.truth) == []


@pytest.mark.parametrize("seed", range(20))
def test_clean_companies_satisfy_every_accounting_equality(seed):
    ep = episode(seed, CLEAN_CONFIG)
    assert ep.truth["issues"] == []
    assert checks.clean_invariants(ep.public) == []
    assert checks.detect_issues(ep.public) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_sizes_within_declared_limits(seed):
    ep = episode(seed)
    counts = ep.public.manifest["counts"]
    assert 200 <= counts["ledger_rows"] <= 2000
    assert 20 <= counts["documents"] <= 200
    assert 0 <= len(ep.truth["issues"]) <= 12
    assert counts["ledger_rows"] == len(ep.public.ledger)


def test_generation_is_byte_identical_for_same_seed_and_config():
    a = generate_episode(11)
    b = generate_episode(11)
    assert books_files(a.public) == books_files(b.public)
    assert canon.dumps(a.truth) == canon.dumps(b.truth)
    assert canon.dumps(a.provenance) == canon.dumps(b.provenance)


def test_different_seeds_give_different_books():
    assert books_files(episode(1).public) != books_files(episode(2).public)


def test_error_types_are_independently_configurable():
    only_dup = {"errors": {"duplicate_invoice": {"count": 2}, "payment_allocation": {"count": 0}}}
    only_alloc = {"errors": {"duplicate_invoice": {"count": 0}, "payment_allocation": {"count": 2}}}
    for seed in range(10):
        assert collections.Counter(i["type"] for i in episode(seed, only_dup).truth["issues"]) == {"duplicate_invoice": 2}
        assert collections.Counter(i["type"] for i in episode(seed, only_alloc).truth["issues"]) == {"payment_allocation": 2}


def test_injecting_errors_keeps_the_clean_history():
    for seed in range(10):
        clean = episode(seed, CLEAN_CONFIG).public
        dirty = episode(seed).public
        content = lambda books: {d["doc_id"]: (d.get("total_minor"), d.get("amount_minor"), d["currency"])  # noqa: E731
                                 for d in books.invoices + books.payments}
        clean_docs, dirty_docs = content(clean), content(dirty)
        assert all(dirty_docs.get(k) == v for k, v in clean_docs.items())
        assert clean.fx == dirty.fx


def test_all_variants_occur():
    variants = collections.Counter()
    for seed in SEEDS:
        for m in episode(seed).provenance["mutations"]:
            variants[(m["operator"], m["variant"])] += 1
    for key in [("duplicate_invoice", "same_number"), ("duplicate_invoice", "new_number"),
                ("payment_allocation", "missing"), ("payment_allocation", "partial")]:
        assert variants[key] > 0, key


def test_distractors_are_present():
    weekly = unidentified = split = 0
    for seed in range(20):
        books = episode(seed).public
        groups = collections.defaultdict(set)
        for d in books.invoices:
            groups[(d["entity_id"], d["total_minor"])].add(d["line_items"][0]["service_period"])
        weekly += any(len(p) > 1 for p in groups.values())
        unidentified += any(p["invoice_refs"] is None for p in books.payments)
        refs = collections.Counter(r["invoice_id"] for p in books.payments for r in (p["invoice_refs"] or []))
        split += any(n > 1 for n in refs.values())
    assert weekly >= 15 and unidentified == 20 and split >= 10


def test_no_internal_keys_or_truth_in_open_files():
    for seed in range(20):
        ep = episode(seed)
        text = "".join(books_files(ep.public).values())
        assert not INTERNAL_KEY_PATTERN.search(text)
        for issue in ep.truth["issues"]:
            assert issue["issue_id"] not in text


def test_injected_documents_are_not_positionally_marked():
    positions = []
    for seed in SEEDS:
        ep = episode(seed)
        order = [d["doc_id"] for d in ep.public.invoices]
        for issue in ep.truth["issues"]:
            if issue["type"] == "duplicate_invoice":
                positions.extend(order.index(d) / max(len(order) - 1, 1) for d in issue["affected_ids"])
    assert positions and 0.25 < sum(positions) / len(positions) < 0.75


def test_ids_share_one_format_regardless_of_injection():
    ep = episode(2)
    assert all(re.fullmatch(r"D[A-Z2-7]{9}", d["doc_id"]) for d in ep.public.invoices + ep.public.payments)
    assert all(re.fullmatch(r"L[A-Z2-7]{10}", r["row_id"]) for r in ep.public.ledger)


def test_timestamps_have_offsets_and_money_is_integer():
    ep = episode(3)
    for r in ep.public.ledger:
        assert re.search(r"[+-]\d\d:\d\d$", r["timestamp"])
        assert type(r["debit_minor"]) is int and type(r["credit_minor"]) is int
    for d in ep.public.invoices:
        assert re.search(r"[+-]\d\d:\d\d$", d["issued_at"])
    for f in ep.public.fx:
        assert isinstance(f["rate"], str) and re.fullmatch(r"\d+(\.\d+)?", f["rate"])


@pytest.mark.parametrize("bad", [
    {"nope": 1},
    {"errors": {"fx_conversion": {"count": 1}}},
    {"errors": {"duplicate_invoice": {"count": 13}}},
    {"errors": {"duplicate_invoice": {"count": [7, 7]}, "payment_allocation": {"count": [6, 6]}}},
    {"render": {"row_order": "random"}},
    {"size": {"invoices": 5}},
    {"schema_version": "9.9"},
])
def test_invalid_configs_are_rejected(bad):
    with pytest.raises(ConfigError):
        resolve(bad)


@pytest.mark.parametrize("bad_seed", [-1, 1.5, True, "3", None])
def test_invalid_seeds_are_rejected(bad_seed):
    with pytest.raises(ValueError):
        generate_episode(bad_seed)
