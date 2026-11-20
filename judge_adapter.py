"""Jury adapter for "Genesis FinTech: определения проверок" (published 2026-11-20).

Entry points (working directory: the project root, /app in the container):

    generate_episode(seed, config, output_dir) -> None
    make_env() -> object with reset(seed, config) and step(action)
    score_episode(episode_dir, findings, inspection_steps) -> dict

The adapter only translates envelopes and field names; it never chooses actions.
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from auditenv.audit import load_episode  # noqa: E402
from auditenv.env import AuditEnv  # noqa: E402
from auditenv.evaluator import Grader, open_ids  # noqa: E402
from auditenv.generator import Episode  # noqa: E402
from auditenv.generator import generate_episode as _generate  # noqa: E402
from auditenv.generator import write_episode  # noqa: E402

TIME_LIMIT_S = 60.0
MODES = {"closed": "eval", "train": "train"}
METRIC_KEYS = ("tp", "fp", "fn", "precision", "recall", "f1")
TABLES_NOTE = (" Tables: ledger (id: row_id), invoices (id: doc_id), payments (id: doc_id), fx (id: "
               "'date|source_currency|target_currency'). Money is in integer minor units.")

log = logging.getLogger("auditenv.judge")
_score_errors: List[str] = []


# ---------------------------------------------------------------------------- generation
def generate_episode(seed: int, config: Dict[str, Any], output_dir: str) -> None:
    """Writes manifest.json, ledger.csv, invoices.jsonl, payments.jsonl, fx.csv, policy.json and the
    closed truth.json and provenance.json into output_dir. `config` is the team generator config."""
    team_config = {k: v for k, v in (config or {}).items() if k not in ("mode", "episode_dir")}
    episode = _generate(seed, team_config.get("team_config", team_config) or None)
    write_episode(episode, output_dir, layout="flat")


# ---------------------------------------------------------------------------- scoring (closed)
def score_episode(episode_dir: str, findings: Dict[str, Any], inspection_steps: int) -> Dict[str, Any]:
    """truth.json may contain only the fields of case section 03 (see src/auditenv/truth.py):
    a missing tolerance_minor means 0, a missing episode_id is taken from manifest.json."""
    episode = load_episode(episode_dir)
    grader = Grader(episode.truth, open_ids(episode.public), episode_id=episode.public.manifest["episode_id"])
    grade = grader.grade(findings, inspection_steps)
    if not grade.valid:
        _score_errors.append("; ".join(grade.errors))
        log.warning("invalid submit in %s: %s", episode_dir, "; ".join(grade.errors))
        return {**{k: None for k in METRIC_KEYS}, "return": -1.0}
    return {**{k: grade.metrics[k] for k in METRIC_KEYS}, "return": grade.reward}


def score_errors() -> List[str]:
    """Evaluator journal of structural errors seen by score_episode."""
    return list(_score_errors)


# ---------------------------------------------------------------------------- environment
class JudgeEnv:
    def __init__(self) -> None:
        self._env: Optional[AuditEnv] = None
        self._deadline = 0.0

    def reset(self, seed: int, config: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        config = dict(config or {})
        mode = config.get("mode", "train")
        if mode not in MODES:
            raise ValueError("mode must be 'closed' or 'train'")
        episode_dir = config.get("episode_dir")
        if episode_dir:
            # Closed runs never load the truth into the environment; scoring happens in score_episode.
            episode = load_episode(episode_dir, with_truth=(mode == "train"))
        else:
            episode = _generate(seed, config.get("team_config") or None)
            if mode == "closed":
                episode = Episode(public=episode.public, truth=None, provenance=None)
        self._env = AuditEnv(episode_factory=lambda _s, _c: episode)
        obs, info = self._env.reset(seed, {"mode": MODES[mode]})
        self._deadline = time.monotonic() + TIME_LIMIT_S
        first = {"task": obs["task"] + TABLES_NOTE, "manifest": obs["manifest"], "policy": obs["policy"],
                 "remaining_steps": obs["steps_remaining"]}
        return first, info

    def step(self, action: Any) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        if self._env is None:
            raise RuntimeError("reset() must be called before step()")
        if time.monotonic() > self._deadline:
            obs, reward, terminated, truncated, info = self._env.timeout()
            return self._observation(None, obs), reward, terminated, truncated, info
        name, internal = _translate_action(action)
        obs, reward, terminated, truncated, info = self._env.step(internal)
        return self._observation(name, obs, terminated), reward, terminated, truncated, info

    def _observation(self, name: Optional[str], obs: Dict[str, Any], terminated: bool = False) -> Dict[str, Any]:
        result, error = obs["result"], obs["error"]
        finished_before = error is not None and error.get("code") == "episode_finished"
        if name == "submit" and terminated and not finished_before:
            outcome = (self._env.closed_result() or {}).get("outcome")
            if outcome == "malformed":
                errors = (self._env.closed_result() or {}).get("errors") or []
                result, error = None, {"code": "invalid_submission", "message": "; ".join(errors)[:300]}
            else:
                result, error = {"accepted": True}, None
        elif error is None and result is not None:
            if name == "list_records":
                result = {"records": result["records"], "next_cursor": result["next_cursor"]}
            elif name == "get_record":
                result = result["record"]
            elif name == "get_document":
                result = result["document"]
            elif name == "calculator":
                result = result["value"]
        return {"result": result, "error": error, "remaining_steps": obs["steps_remaining"]}

    # Evaluator-side accessors (never part of an observation).
    def submitted_findings(self) -> Optional[Dict[str, Any]]:
        return None if self._env is None else self._env.final_submission()

    def inspection_steps(self) -> Optional[int]:
        return None if self._env is None else self._env.inspection_steps


def make_env() -> JudgeEnv:
    return JudgeEnv()


def _translate_action(action: Any) -> Tuple[Optional[str], Any]:
    """{"name": n, "args": {...}} -> internal {"action": n, "args": ...}; submit args {"findings": F} -> F."""
    if isinstance(action, (str, bytes)):
        try:
            action = json.loads(action)
        except (ValueError, RecursionError):
            return None, action
    if not isinstance(action, dict) or "name" not in action:
        return None, action
    name = action["name"]
    if set(action) - {"name", "args"}:
        return None, {"action": name, "args": action.get("args"), "unexpected": True}
    args = action.get("args", {})
    if name == "submit":
        inner = args["findings"] if isinstance(args, dict) and set(args) == {"findings"} else None
        return name, {"action": "submit", "args": inner}
    return (name if isinstance(name, str) else None), {"action": name, "args": args}
