"""Jury-style paired checks end to end: generate D01-D20, transform files, run oracle and solver."""

import json
import os

import pytest

import judge_adapter as ja
from auditenv.audit import load_episode
from auditenv.books import books_files
from auditenv.paired import ID_SPACES, pair_plan, run_all_pairs, transform_episode


@pytest.fixture(scope="module")
def pairs(tmp_path_factory):
    return run_all_pairs(ja, str(tmp_path_factory.mktemp("paired")))


def test_twenty_pairs_with_the_published_distribution(pairs):
    assert [p["pair"] for p in pairs] == [f"P{n:02d}" for n in range(1, 21)]
    assert [(p["permute"], p["rename"]) for p in pairs] == [pair_plan(n) for n in range(1, 21)]
    assert sum(p["permute"] and not p["rename"] for p in pairs) == 5
    assert sum(p["rename"] and not p["permute"] for p in pairs) == 5
    assert sum(p["rename"] and p["permute"] for p in pairs) == 10


@pytest.mark.parametrize("n", range(1, 21))
def test_pair_passes(pairs, n):
    p = pairs[n - 1]
    assert p["passed"], {k: v for k, v in p.items() if k not in ("oracle_score",)}
    assert p["issue_count"][0] == p["issue_count"][1]
    assert p["issue_types"][0] == p["issue_types"][1]
    assert p["truth_equal_after_inverse_mapping"] and p["evidence_valid_in_copy"]
    assert p["f1"][0] == p["f1"][1] == 1.0 and p["return"][0] == p["return"][1]
    assert p["oracle_score"][0] == p["oracle_score"][1]
    assert p["solver_metrics"][0] == p["solver_metrics"][1] and p["solver_findings_equal_after_inverse_mapping"]
    assert p["reproducible"] and p["reset_seed"] == 0
    if p["permute"]:
        assert p["ledger_rows_permuted"] and p["documents_permuted"]
    if p["rename"]:
        assert all(p["ids_replaced"][s] > 0 for s in ID_SPACES)
        assert p["company_renamed"] and p["old_ids_absent_in_copy"] and p["old_ids_absent_in_observations"]


def test_pairs_cover_every_error_type_and_both_interactions(pairs):
    types = {t for p in pairs for t in p["issue_types"][0]}
    assert types == {"duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff"}
    assert {p["scenario"] for p in pairs} >= {"clean", "interaction_fx_partial_at_cutoff", "interaction_allocation_with_fx"}


def test_transform_changes_every_requested_field(tmp_path):
    src, dst = str(tmp_path / "D"), str(tmp_path / "P")
    with open(os.path.join(ja.ROOT, "configs", "scenarios", "interaction_allocation_with_fx.json")) as fh:
        ja.generate_episode(219, json.load(fh), src)
    t = transform_episode(src, dst, permute=True, rename=True, seed=3)
    a, b = load_episode(src), load_episode(dst)
    text_b = "".join(books_files(b.public).values())
    for r in a.public.ledger[:20]:
        assert r["row_id"] not in text_b and t.mapping[r["row_id"]] in text_b
    for d in a.public.invoices:
        assert t.mapping[d["doc_id"]].startswith("DOC-") and t.mapping[d["invoice_id"]].startswith("INVNO-")
    for p in a.public.payments:
        assert t.mapping[p["doc_id"]].startswith("DOC-") and t.mapping[p["payment_id"]].startswith("PAYNO-")
    assert b.public.manifest["company"] != a.public.manifest["company"]
    assert b.public.manifest["episode_id"] == a.public.manifest["episode_id"]
    assert sorted(b.public.fx, key=json.dumps) == sorted(a.public.fx, key=json.dumps)  # FX keys are not surrogate ids
    for issue_a, issue_b in zip(a.truth["issues"], b.truth["issues"]):
        assert [t.mapping.get(x, x) for x in issue_a["affected_ids"]] == issue_b["affected_ids"]  # list order kept
        assert issue_a["expected_amount_minor"] == issue_b["expected_amount_minor"]
    for line_a, line_b in zip(sorted(a.public.invoices, key=lambda d: t.mapping[d["doc_id"]]),
                              sorted(b.public.invoices, key=lambda d: d["doc_id"])):
        assert line_a["line_items"] == line_b["line_items"]  # line item order is semantic and preserved
