"""Episode generator: world -> clean ledger -> invariant check -> mutations -> truth -> render -> verify."""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from auditenv import canon, checks, config as config_mod
from auditenv.books import PublicBooks, write_books
from auditenv.generator.ledger import build_ledger
from auditenv.generator.operators import (
    InternalIssue,
    inject_allocation_with_fx,
    inject_duplicate_invoices,
    inject_fx_conversion,
    inject_fx_partial_at_cutoff,
    inject_payment_allocation,
    inject_period_cutoff,
)
from auditenv.generator.render import render
from auditenv.generator.world import build_world
from auditenv.rng import Rng, resolve_range
from auditenv.version import GENERATOR_VERSION, SCHEMA_VERSION


class GenerationError(RuntimeError):
    pass


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


@dataclass
class Episode:
    public: PublicBooks
    truth: Dict[str, Any]
    provenance: Dict[str, Any]

    @property
    def episode_id(self) -> str:
        return self.public.manifest["episode_id"]


def _diff(before: PublicBooks, after: PublicBooks) -> Dict[str, Any]:
    """Every open record the injections added, removed or modified, with full before/after values.
    Applying it in reverse to the open files reproduces the pre-injection books exactly."""
    out: Dict[str, Any] = {}
    for table, key in (("ledger", "row_id"), ("invoices", "doc_id"), ("payments", "doc_id")):
        b = {r[key]: r for r in getattr(before, table)}
        a = {r[key]: r for r in getattr(after, table)}
        out[table] = {
            "added": [a[k] for k in sorted(a.keys() - b.keys())],
            "removed": [b[k] for k in sorted(b.keys() - a.keys())],
            "modified": [{"before": b[k], "after": a[k]} for k in sorted(a.keys() & b.keys()) if a[k] != b[k]],
        }
    if canon.dumps(sorted(before.fx, key=canon.dumps)) != canon.dumps(sorted(after.fx, key=canon.dumps)):
        raise GenerationError("error injection must not change the FX table")
    out["fx"] = "unchanged"
    out["manifest_counts_before"] = before.manifest["counts"]
    return out


def _check_seed(seed: Any) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative int")
    return seed


def generate_episode(seed: int, config: Any = None) -> Episode:
    seed = _check_seed(seed)
    cfg = config_mod.resolve(config)
    world_hash = config_mod.config_hash(config_mod.world_part(cfg))

    world, books, alloc, world_summary = build_world(seed, cfg, world_hash)
    clean_books = books.copy()
    clean_public, _ = render(world, clean_books, build_ledger(world, clean_books), cfg, world_hash)
    clean_violations = checks.clean_invariants(clean_public)
    if clean_violations:
        raise GenerationError(f"clean ledger violates invariants: {clean_violations[:5]}")

    issues: List[InternalIssue] = []
    used: Set[str] = set()
    errors = cfg["errors"]

    def stream(name: str, spec: Any) -> Rng:
        return Rng("errors", seed, world_hash, name, canon.dumps(spec))

    # Counts come from independent streams; the issue budget is then spent in a fixed order.
    inter = cfg["interactions"]
    plan = []
    for name in ("fx_partial_at_cutoff", "allocation_with_fx"):
        rng = stream(name, inter[name])
        plan.append((name, rng, resolve_range(inter[name]["count"], rng), 2))
    for name in ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff"):
        rng = stream(name, errors[name])
        plan.append((name, rng, resolve_range(errors[name]["count"], rng), 1))
    budget = cfg["max_issues"]
    requested = {}
    for name, rng, count, per_issue in plan:
        granted = min(count, budget // per_issue)
        budget -= granted * per_issue
        requested[name] = {"requested": count, "granted": granted}
        before = len(issues)
        if name == "fx_partial_at_cutoff":
            issues += inject_fx_partial_at_cutoff(world, books, rng, granted, alloc, used)
        elif name == "allocation_with_fx":
            variants = list(errors["payment_allocation"]["variants"]) or ["missing", "partial"]
            issues += inject_allocation_with_fx(world, books, rng, granted, variants, used)
        elif name == "duplicate_invoice":
            issues += inject_duplicate_invoices(world, books, rng, granted, errors[name]["variants"], alloc, used)
        elif name == "payment_allocation":
            issues += inject_payment_allocation(world, books, rng, granted, errors[name]["variants"], used)
        elif name == "fx_conversion":
            issues += inject_fx_conversion(world, books, rng, granted, errors[name]["variants"], used)
        else:
            issues += inject_period_cutoff(world, books, rng, granted, errors[name]["variants"], alloc, used)
        requested[name]["injected"] = (len(issues) - before) // per_issue

    lines = build_ledger(world, books)
    line_by_key = {ln.key: ln for ln in lines}
    for issue in issues:
        if issue.amount_from_line is not None:
            ln = line_by_key[issue.amount_from_line]
            issue.amount_minor = ln.credit_minor or ln.debit_minor

    public, ids = render(world, books, lines, cfg, world_hash)
    episode_id = public.manifest["episode_id"]

    def surface(key: str) -> str:
        return key[3:] if key.startswith("fx:") else ids.map[key]

    def issue_id(key: str) -> str:
        return "ISS-" + ids.token("issue", key, 8)

    def to_surface(value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("fx:"):
                return value[3:]
            return ids.map.get(value, value)
        if isinstance(value, dict):
            return {k: to_surface(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [to_surface(v) for v in value]
        return value

    truth_issues = []
    for issue in issues:
        truth_issues.append({
            "issue_id": issue_id(issue.key),
            "type": issue.type,
            "affected_ids": sorted(surface(k) for k in issue.affected),
            "expected_amount_minor": issue.amount_minor,
            "currency": issue.currency,
            "tolerance_minor": issue.tolerance_minor,
            "evidence_sets": sorted(sorted(surface(k) for k in ev) for ev in issue.evidence_sets),
        })
    truth_issues.sort(key=lambda i: (i["type"], i["affected_ids"]))
    truth = {"schema_version": SCHEMA_VERSION, "episode_id": episode_id, "issues": truth_issues}

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "config": cfg,
        "config_hash": config_mod.config_hash(cfg),
        "world_config_hash": world_hash,
        "world": world_summary,
        "issue_budget": {"max_issues": cfg["max_issues"], "per_operator": requested,
                         "injected": len(issues)},
        "mutations": [
            {
                "operator": i.type,
                "variant": i.variant,
                "issue_key": i.key,
                "issue_id": issue_id(i.key),
                "scenario": i.params.get("scenario"),
                "linked_issue_id": issue_id(i.params["linked_issue"]) if "linked_issue" in i.params else None,
                "affected": list(i.affected),
                "affected_ids": sorted(surface(k) for k in i.affected),
                "params": _jsonable(i.params),
                "params_surface": to_surface(_jsonable(i.params)),
            }
            for i in issues
        ],
        "changes": _diff(clean_public, public),
        "id_map": dict(sorted(ids.map.items())),
    }

    problems = checks.verify_episode(public, truth)
    if problems:
        raise GenerationError(f"episode {seed} failed verification: {problems[:5]}")
    return Episode(public=public, truth=truth, provenance=provenance)


def write_episode(episode: Episode, directory: str, layout: str = "split") -> None:
    """layout="split": books/ (open) and truth/ (closed); layout="flat": all eight files in one directory."""
    if layout not in ("split", "flat"):
        raise ValueError("layout must be 'split' or 'flat'")
    write_books(episode.public, directory if layout == "flat" else os.path.join(directory, "books"))
    private = directory if layout == "flat" else os.path.join(directory, "truth")
    os.makedirs(private, exist_ok=True)
    for name, obj in (("truth.json", episode.truth), ("provenance.json", episode.provenance)):
        with open(os.path.join(private, name), "w", encoding="utf-8") as fh:
            fh.write(canon.dumps(obj) + "\n")
