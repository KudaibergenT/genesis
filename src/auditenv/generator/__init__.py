"""Episode generator: world -> clean ledger -> invariant check -> mutations -> truth -> render -> verify."""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from auditenv import canon, checks, config as config_mod
from auditenv.books import PublicBooks, write_books
from auditenv.generator.ledger import build_ledger
from auditenv.generator.operators import InternalIssue, inject_duplicate_invoices, inject_payment_allocation
from auditenv.generator.render import render
from auditenv.generator.world import build_world
from auditenv.rng import Rng, resolve_range
from auditenv.version import GENERATOR_VERSION, SCHEMA_VERSION


class GenerationError(RuntimeError):
    pass


@dataclass
class Episode:
    public: PublicBooks
    truth: Dict[str, Any]
    provenance: Dict[str, Any]

    @property
    def episode_id(self) -> str:
        return self.public.manifest["episode_id"]


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
    dup_spec = errors["duplicate_invoice"]
    rng = Rng("errors", seed, world_hash, "duplicate_invoice", canon.dumps(dup_spec))
    issues += inject_duplicate_invoices(world, books, rng, resolve_range(dup_spec["count"], rng),
                                        dup_spec["variants"], alloc, used)
    alloc_spec = errors["payment_allocation"]
    rng = Rng("errors", seed, world_hash, "payment_allocation", canon.dumps(alloc_spec))
    issues += inject_payment_allocation(world, books, rng, resolve_range(alloc_spec["count"], rng),
                                        alloc_spec["variants"], used)

    lines = build_ledger(world, books)
    line_by_key = {ln.key: ln for ln in lines}
    for issue in issues:
        if issue.amount_from_line is not None:
            ln = line_by_key[issue.amount_from_line]
            issue.amount_minor = ln.credit_minor or ln.debit_minor

    public, ids = render(world, books, lines, cfg, world_hash)
    episode_id = public.manifest["episode_id"]

    truth_issues = []
    for issue in issues:
        truth_issues.append({
            "issue_id": "ISS-" + ids.token("issue", issue.key, 8),
            "type": issue.type,
            "affected_ids": sorted(ids.map[k] for k in issue.affected),
            "expected_amount_minor": issue.amount_minor,
            "currency": issue.currency,
            "tolerance_minor": issue.tolerance_minor,
            "evidence_sets": sorted(sorted(ids.map[k] for k in ev) for ev in issue.evidence_sets),
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
        "mutations": [
            {
                "operator": i.type,
                "variant": i.variant,
                "issue_key": i.key,
                "issue_id": "ISS-" + ids.token("issue", i.key, 8),
                "params": i.params,
            }
            for i in issues
        ],
        "id_map": dict(sorted(ids.map.items())),
    }

    problems = checks.verify_episode(public, truth)
    if problems:
        raise GenerationError(f"episode {seed} failed verification: {problems[:5]}")
    return Episode(public=public, truth=truth, provenance=provenance)


def write_episode(episode: Episode, directory: str) -> None:
    write_books(episode.public, os.path.join(directory, "books"))
    private = os.path.join(directory, "truth")
    os.makedirs(private, exist_ok=True)
    for name, obj in (("truth.json", episode.truth), ("provenance.json", episode.provenance)):
        with open(os.path.join(private, name), "w", encoding="utf-8") as fh:
            fh.write(canon.dumps(obj) + "\n")
