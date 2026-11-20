"""Evaluator-side audit of a generated episode: accounting, truth, provenance, evidence."""

import collections
import copy
import json
import os
import re
from typing import Any, Dict, List

from auditenv import checks
from auditenv.books import PublicBooks, books_files, load_books
from auditenv.generator import Episode, generate_episode

STRUCTURAL = {"row_amounts", "row_currency", "entry_unbalanced", "ledger_unbalanced", "dangling_invoice_ref",
              "dangling_payment_ref", "row_out_of_scope", "invoice_arithmetic", "invoice_out_of_scope",
              "payment_out_of_scope", "payment_refs_sum", "payment_ref_unknown_invoice"}
INTERNAL_KEYS = re.compile(r"(inv#|pay#|cust#|num#|bg#|E:|issue:|pid:|interaction:)")


def load_episode(directory: str, with_truth: bool = True) -> Episode:
    """Load an episode written with layout "split" (books/, truth/) or "flat" (one directory)."""
    split = os.path.isdir(os.path.join(directory, "books"))
    books_dir = os.path.join(directory, "books") if split else directory
    private = os.path.join(directory, "truth") if split else directory
    truth = provenance = None
    if with_truth:
        with open(os.path.join(private, "truth.json"), encoding="utf-8") as fh:
            truth = json.load(fh)
        path = os.path.join(private, "provenance.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                provenance = json.load(fh)
    return Episode(public=load_books(books_dir), truth=truth, provenance=provenance)


def pre_injection_books(public: PublicBooks, changes: Dict[str, Any]) -> PublicBooks:
    """Undo provenance["changes"] on the open books: the state before any error was injected."""
    tables = {}
    for table, key in (("ledger", "row_id"), ("invoices", "doc_id"), ("payments", "doc_id")):
        diff = changes[table]
        added = {r[key] for r in diff["added"]}
        before = {m["after"][key]: m["before"] for m in diff["modified"]}
        rows = [before.get(r[key], r) for r in getattr(public, table) if r[key] not in added]
        tables[table] = rows + list(diff["removed"])
    manifest = dict(public.manifest, counts=changes["manifest_counts_before"])
    return PublicBooks(manifest=manifest, policy=public.policy, fx=public.fx, **tables)


def clean_counterpart(ep: Episode) -> Episode:
    cfg = copy.deepcopy(ep.provenance["config"])
    cfg["max_issues"] = 0
    return generate_episode(ep.provenance["seed"], cfg)


def audit_episode(ep: Episode, check_clean_counterpart: bool = True) -> Dict[str, Any]:
    problems: List[str] = []
    public, truth, prov = ep.public, ep.truth, ep.provenance
    ix = checks.Index(public)
    issues = truth["issues"]
    by_id = {i["issue_id"]: i for i in issues}

    # 1. independent verification (equalities, declared discrepancies, evidence sufficiency, uniqueness)
    problems += checks.verify_episode(public, truth)

    # 2. mutations never break the double-entry structure itself
    for v in checks.clean_invariants(public):
        if v.code in STRUCTURAL:
            problems.append(f"structural violation after injection: {v.code} {v.ids}")
    if sum(r["debit_minor"] for r in public.ledger) != sum(r["credit_minor"] for r in public.ledger):
        problems.append("ledger does not balance")

    # 3. the same company without injections satisfies every accounting equality
    if check_clean_counterpart:
        clean = clean_counterpart(ep)
        if checks.clean_invariants(clean.public):
            problems.append("clean counterpart violates invariants")
        if checks.detect_issues(clean.public):
            problems.append("clean counterpart has detectable discrepancies")
        if clean.public.fx != public.fx:
            problems.append("injection changed the FX table")

    # 4. provenance <-> truth
    mutations = prov["mutations"]
    if len(mutations) != len(issues) or prov["issue_budget"]["injected"] != len(issues):
        problems.append("provenance and truth disagree on the number of issues")
    if len(issues) > prov["issue_budget"]["max_issues"]:
        problems.append("issue budget exceeded")
    by_mutation = {m["issue_id"]: m for m in mutations}
    for m in mutations:
        issue = by_id.get(m["issue_id"])
        if issue is None or issue["type"] != m["operator"] or sorted(issue["affected_ids"]) != sorted(m["affected_ids"]):
            problems.append(f"mutation {m['issue_id']} does not match its truth issue")
        if m["linked_issue_id"] is not None:
            other = by_mutation.get(m["linked_issue_id"])
            if other is None or other["linked_issue_id"] != m["issue_id"] or sorted(other["affected_ids"]) != sorted(m["affected_ids"]) \
                    or other["scenario"] != m["scenario"] or other["operator"] == m["operator"]:
                problems.append(f"interaction link of {m['issue_id']} is not reciprocal")

    # 5. truth content and evidence minimality
    conventions = public.policy["issue_conventions"]
    for issue in issues:
        amount = issue["expected_amount_minor"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            problems.append(f"{issue['issue_id']}: amount must be a positive integer")
        if issue["currency"] not in public.manifest["currency_exponents"]:
            problems.append(f"{issue['issue_id']}: unknown currency")
        if issue["tolerance_minor"] != conventions[issue["type"]]["tolerance_minor"]:
            problems.append(f"{issue['issue_id']}: tolerance differs from the published convention")
        for doc_id in issue["affected_ids"]:
            if doc_id not in ix.invoices and doc_id not in ix.payments:
                problems.append(f"{issue['issue_id']}: affected id {doc_id} is not a document")
        for evidence in issue["evidence_sets"]:
            for k in range(len(evidence)):
                try:
                    checks.verify_evidence(public, issue["type"], evidence[:k] + evidence[k + 1:])
                    problems.append(f"{issue['issue_id']}: evidence {evidence} is not minimal")
                    break
                except checks.EvidenceError:
                    pass

    # 6. nothing closed or internal in the open files
    text = "".join(books_files(public).values())
    if INTERNAL_KEYS.search(text):
        problems.append("internal keys leaked into open files")
    if any(i["issue_id"] in text for i in issues):
        problems.append("issue ids leaked into open files")

    stats = {
        "issues": len(issues),
        "types": dict(collections.Counter(i["type"] for i in issues)),
        "variants": dict(collections.Counter(f"{m['operator']}/{m['variant']}" for m in mutations)),
        "scenarios": sorted({m["scenario"] for m in mutations if m["scenario"]}),
        "ledger_rows": len(public.ledger),
        "documents": len(public.invoices) + len(public.payments),
        "timezone": public.manifest["timezone"],
        "rounding": public.manifest["rounding"]["mode"],
        "base_currency": public.manifest["base_currency"],
    }
    return {"episode_id": ep.episode_id, "problems": problems, "stats": stats}
