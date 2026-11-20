"""Jury-style paired checks (published definitions, block 4).

For D01..D20: generate the episode with judge_adapter, write a transformed copy (record/document
permutation and/or consistent replacement of row_id, doc_id, invoice_id, payment_id, entity_id
in every file, truth and provenance, plus a new company name), then run the evaluator-side oracle
(with the NEW ids) and the reference solver (isolated process) on both and compare.
"""

import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from auditenv import checks
from auditenv.audit import load_episode, pre_injection_books
from auditenv.books import books_files, fx_key
from auditenv.evaluator import open_ids
from auditenv.generator import Episode, write_episode
from auditenv.rng import Rng
from auditenv.sandbox.runner import run_isolated

ID_SPACES = ("row_id", "doc_id", "invoice_id", "payment_id", "entity_id")
PREFIX = {"row_id": "ROW-", "doc_id": "DOC-", "invoice_id": "INVNO-", "payment_id": "PAYNO-", "entity_id": "ENT-"}
EXPLORATION = (
    {"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}},
    {"name": "list_records", "args": {"table": "invoices", "cursor": None, "limit": 50}},
    {"name": "list_records", "args": {"table": "payments", "cursor": None, "limit": 50}},
    {"name": "calculator", "args": {"expression": "2/3"}},
)


def pair_plan(n: int) -> Tuple[bool, bool]:
    """(permute, rename) for pair n = 1..20: P01-P05 permute, P06-P10 rename, P11-P20 both."""
    return (n <= 5 or n > 10, n > 5)


@dataclass
class Transform:
    mapping: Dict[str, str]           # old id -> new id (all spaces, ids are disjoint between spaces)
    per_space: Dict[str, int]         # number of replaced ids per space
    company: Tuple[str, str]


def _collect_ids(ep: Episode) -> Dict[str, set]:
    space: Dict[str, set] = {k: set() for k in ID_SPACES}
    for r in ep.public.ledger:
        space["row_id"].add(r["row_id"])
        for k in ("entity_id", "invoice_id", "payment_id"):
            if r[k] is not None:
                space[k].add(r[k])
    for d in ep.public.invoices:
        space["doc_id"].add(d["doc_id"]), space["invoice_id"].add(d["invoice_id"]), space["entity_id"].add(d["entity_id"])
    for p in ep.public.payments:
        space["doc_id"].add(p["doc_id"]), space["payment_id"].add(p["payment_id"])
        for ref in p["invoice_refs"] or []:
            space["invoice_id"].add(ref["invoice_id"])
    return space


def _replace(obj: Any, mapping: Dict[str, str]) -> Any:
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [_replace(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: _replace(v, mapping) for k, v in obj.items()}
    return obj


def transform_episode(src: str, dst: str, permute: bool, rename: bool, seed: int) -> Transform:
    ep = load_episode(src)
    public = copy.deepcopy(ep.public)
    truth, provenance = copy.deepcopy(ep.truth), copy.deepcopy(ep.provenance)
    rng = Rng("jury-paired-transform", seed)
    mapping: Dict[str, str] = {}
    per_space = {k: 0 for k in ID_SPACES}
    company = (public.manifest["company"], public.manifest["company"])
    if rename:
        for space, ids in _collect_ids(ep).items():
            fresh = [f"{PREFIX[space]}{n:06d}" for n in range(len(ids))]
            rng.shuffle(fresh)
            for old, new in zip(sorted(ids), fresh):
                if old in mapping:
                    raise ValueError(f"id {old} appears in two id spaces")
                mapping[old] = new
            per_space[space] = len(ids)
        for name in ("ledger", "invoices", "payments"):
            setattr(public, name, _replace(getattr(public, name), mapping))
        truth = _replace(truth, mapping)
        provenance = _replace(provenance, mapping)
        new_company = f"Renamed Holding {seed:02d}"
        company = (public.manifest["company"], new_company)
        public.manifest = dict(public.manifest, company=new_company)
    if permute:
        for name in ("ledger", "invoices", "payments", "fx"):
            rows = list(getattr(public, name))
            rng.shuffle(rows)
            setattr(public, name, rows)
    write_episode(Episode(public=public, truth=truth, provenance=provenance), dst, layout="flat")
    return Transform(mapping, per_space, company)


def _oracle(truth: Dict[str, Any]) -> Dict[str, Any]:
    return {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": "oracle"}
        for i in truth["issues"]]}


def _oracle_run(ja, directory: str):
    env = ja.make_env()
    transcript = [list(env.reset(0, {"mode": "closed", "episode_dir": directory, "team_config": {}}))]
    for action in EXPLORATION:
        transcript.append(list(env.step(action)))
    with open(os.path.join(directory, "truth.json"), encoding="utf-8") as fh:
        findings = _oracle(json.load(fh))
    transcript.append(list(env.step({"name": "submit", "args": {"findings": findings}})))
    score = ja.score_episode(directory, env.submitted_findings(), env.inspection_steps())
    return json.dumps(transcript, sort_keys=True), score, findings


def _canonical_truth(truth: Dict[str, Any], inverse: Dict[str, str]) -> List[Any]:
    return sorted(
        (i["type"], sorted(inverse.get(x, x) for x in i["affected_ids"]), i["expected_amount_minor"], i["currency"],
         sorted(sorted(inverse.get(x, x) for x in s) for s in i["evidence_sets"]))
        for i in truth["issues"])


def _solver(directory: str):
    run = run_isolated("reference_solver", 0, episode=load_episode(directory), mode="eval")
    findings = run.trajectory[-1]["action"]["args"]["findings"] if run.outcome == "submitted" else []
    return run.result["metrics"], run.result["reward"], findings, run.outcome


def run_pair(ja, n: int, scenario: str, seed: int, workdir: str) -> Dict[str, Any]:
    permute, rename = pair_plan(n)
    src, dst = os.path.join(workdir, f"D{n:02d}"), os.path.join(workdir, f"P{n:02d}")
    with open(os.path.join(ja.ROOT, "configs", "scenarios", f"{scenario}.json"), encoding="utf-8") as fh:
        ja.generate_episode(seed, json.load(fh), src)
    t = transform_episode(src, dst, permute, rename, seed=n)
    inverse = {v: k for k, v in t.mapping.items()}
    a, b = load_episode(src), load_episode(dst)
    c: Dict[str, Any] = {"pair": f"P{n:02d}", "episode": f"D{n:02d}", "scenario": scenario, "generation_seed": seed,
                         "reset_seed": 0, "permute": permute, "rename": rename}

    order = lambda rows, key: [inverse.get(r[key], r[key]) for r in rows]  # noqa: E731
    c["ledger_rows_permuted"] = order(b.public.ledger, "row_id") != order(a.public.ledger, "row_id")
    c["documents_permuted"] = (order(b.public.invoices, "doc_id") != order(a.public.invoices, "doc_id")
                               and order(b.public.payments, "doc_id") != order(a.public.payments, "doc_id"))
    c["ids_replaced"] = t.per_space
    dst_text = "".join(books_files(b.public).values()) + json.dumps(b.truth)
    c["old_ids_absent_in_copy"] = not rename or not any(old in dst_text for old in t.mapping)
    c["company_renamed"] = t.company[0] != t.company[1] and b.public.manifest["company"] == t.company[1]
    c["manifest_counts_match"] = (b.public.manifest["counts"]["ledger_rows"] == len(b.public.ledger)
                                  and b.public.manifest["counts"]["documents"] == len(b.public.invoices) + len(b.public.payments))

    c["issue_count"] = (len(a.truth["issues"]), len(b.truth["issues"]))
    c["issue_types"] = (sorted(i["type"] for i in a.truth["issues"]), sorted(i["type"] for i in b.truth["issues"]))
    c["truth_equal_after_inverse_mapping"] = _canonical_truth(b.truth, inverse) == _canonical_truth(a.truth, {})
    c["evidence_valid_in_copy"] = checks.verify_episode(b.public, b.truth) == []
    before = pre_injection_books(b.public, b.provenance["changes"])
    c["pre_injection_valid_in_copy"] = checks.clean_invariants(before) == []

    tr_a1, score_a, oracle_a = _oracle_run(ja, src)
    tr_a2, score_a2, _ = _oracle_run(ja, src)
    tr_b1, score_b, oracle_b = _oracle_run(ja, dst)
    tr_b2, score_b2, _ = _oracle_run(ja, dst)
    known_b = open_ids(b.public)
    c["oracle_uses_new_ids"] = all(set(f["affected_ids"]) | set(f["evidence_ids"]) <= known_b for f in oracle_b["findings"])
    c["oracle_score"] = (score_a, score_b)
    c["reproducible"] = tr_a1 == tr_a2 and tr_b1 == tr_b2 and score_a == score_a2 and score_b == score_b2
    c["old_ids_absent_in_observations"] = not rename or not any(old in tr_b1 for old in t.mapping if len(old) > 4)

    solver_a, reward_a, found_a, out_a = _solver(src)
    solver_b, reward_b, found_b, out_b = _solver(dst)
    key = lambda f, m: (f["type"], sorted(m.get(x, x) for x in f["affected_ids"]), f["claimed_amount_minor"], f["currency"])  # noqa: E731
    c["solver_metrics"] = (solver_a, solver_b)
    c["solver_findings_equal_after_inverse_mapping"] = sorted(key(f, inverse) for f in found_b) == sorted(key(f, {}) for f in found_a)

    passed = [
        c["manifest_counts_match"], c["issue_count"][0] == c["issue_count"][1], c["issue_types"][0] == c["issue_types"][1],
        c["truth_equal_after_inverse_mapping"], c["evidence_valid_in_copy"], c["pre_injection_valid_in_copy"],
        c["oracle_uses_new_ids"], score_a == score_b, score_a["f1"] == 1.0, c["reproducible"],
        solver_a == solver_b and reward_a == reward_b, c["solver_findings_equal_after_inverse_mapping"],
        out_a == out_b == "submitted",
        (not permute) or (c["ledger_rows_permuted"] and c["documents_permuted"]),
        (not rename) or (all(t.per_space[s] > 0 for s in ID_SPACES) and c["old_ids_absent_in_copy"]
                         and c["company_renamed"] and c["old_ids_absent_in_observations"]),
    ]
    c["passed"] = all(passed)
    c["f1"] = (score_a["f1"], score_b["f1"])
    c["return"] = (score_a["return"], score_b["return"])
    return c


def run_all_pairs(ja, workdir: str) -> List[Dict[str, Any]]:
    from auditenv.suites import JUDGE_MATRIX

    return [run_pair(ja, n, scenario, seed, workdir) for n, (scenario, seed) in enumerate(JUDGE_MATRIX, start=1)]
