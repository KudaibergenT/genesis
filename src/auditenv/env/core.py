"""AuditEnv: JSON-compatible, Gymnasium-style contract.

reset(seed, config) -> (observation, info)
step(action)        -> (observation, reward, terminated, truncated, info)

The environment only ever holds the open books. The closed truth lives inside the
Grader; observations, errors and info are built exclusively from open data.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from auditenv import canon
from auditenv.books import PRIMARY_KEYS, TABLES, fx_key
from auditenv.env.calculator import CalculatorError, evaluate
from auditenv.evaluator import Grade, Grader, open_ids, validate_submission
from auditenv.evaluator.schema import MAX_EVIDENCE_IDS, MAX_EXPLANATION_CHARS, MAX_FINDINGS
from auditenv.generator import Episode, generate_episode
from auditenv.rng import derive_key
from auditenv.version import ENV_VERSION

MAX_STEPS = 100
PAGE_LIMIT = 50
MODES = ("train", "eval")

TASK = (
    "You are auditing one company for one reporting period. Investigate the open records with the "
    "tools, then submit findings for real discrepancies only. Each finding needs the issue type, the "
    "canonical affected_ids, the amount in minor units, the currency and evidence_ids that prove it. "
    "Conventions per issue type are in policy.issue_conventions. Clean books exist: submitting no "
    "findings is correct when nothing is wrong. Every action, including errors, uses one step; "
    "submit ends the episode."
)

ACTIONS = {
    "list_records": {"args": {"table": "one of " + ", ".join(TABLES), "cursor": "null or next_cursor from a previous page",
                              "limit": f"1..{PAGE_LIMIT} (larger values are clamped)"}},
    "get_record": {"args": {"table": "one of " + ", ".join(TABLES),
                            "id": "primary key: ledger.row_id, invoices.doc_id, payments.doc_id, fx 'date|source|target'"}},
    "get_document": {"args": {"doc_id": "doc_id of an invoice or payment"}},
    "calculator": {"args": {"expression": "decimal arithmetic with + - * / and parentheses"}},
    "submit": {"args": {"episode_id": "current episode_id",
                        "findings": f"list (max {MAX_FINDINGS}) of {{type, affected_ids, claimed_amount_minor, "
                                    f"currency, evidence_ids (max {MAX_EVIDENCE_IDS}), explanation (max "
                                    f"{MAX_EXPLANATION_CHARS} chars)}}"}},
}

ARG_KEYS = {
    "list_records": ({"table"}, {"cursor", "limit"}),
    "get_record": ({"table", "id"}, set()),
    "get_document": ({"doc_id"}, set()),
    "calculator": ({"expression"}, set()),
}


def _jsonable(obj: Any) -> bool:
    try:
        canon.json_copy(obj)
        return True
    except (TypeError, ValueError, RecursionError):
        return False


class ActionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _err(code: str, message: str) -> ActionError:
    return ActionError(code, message)


class AuditEnv:
    max_steps = MAX_STEPS
    page_limit = PAGE_LIMIT

    def __init__(self, episode_factory: Optional[Callable[[int, Any], Episode]] = None) -> None:
        self._factory = episode_factory or generate_episode
        self._ready = False

    # ------------------------------------------------------------------ contract
    def reset(self, seed: int, config: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if config is not None and not isinstance(config, dict):
            raise TypeError("config must be a dict")
        config = dict(config or {})
        mode = config.pop("mode", "train")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        episode = self._factory(seed, config)
        self._load(episode, mode)
        obs = {
            "task": TASK,
            "manifest": self._manifest,
            "policy": self._policy,
            "tables": {t: {"rows": len(self._tables[t]), "primary_key": PRIMARY_KEYS[t]} for t in TABLES},
            "actions": ACTIONS,
            "steps_remaining": self.max_steps,
        }
        info = {"episode_id": self._episode_id, "mode": mode, "max_steps": self.max_steps, "env_version": ENV_VERSION}
        return canon.json_copy(obs), info

    def step(self, action: Any) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        if not self._ready:
            raise RuntimeError("reset() must be called before step()")
        if self._done:
            obs = {"result": None, "error": {"code": "episode_finished", "message": "episode has ended; call reset()"},
                   "steps_remaining": 0}
            return obs, 0.0, self._terminated, self._truncated, {"step": self._steps, "final": True}

        self._steps += 1
        result: Any = None
        error: Optional[Dict[str, str]] = None
        reward = 0.0
        try:
            name, args = self._parse(action)
            if name == "submit":
                return self._submit(args)
            result = self._dispatch(name, args)
        except ActionError as exc:
            error = {"code": exc.code, "message": exc.message}

        remaining = self.max_steps - self._steps
        truncated = remaining <= 0
        if truncated:
            grade = self._finish(None, outcome="truncated", inspection_steps=self._steps)
            reward = self._visible_reward(grade)
        obs = {"result": result, "error": error, "steps_remaining": max(remaining, 0)}
        info = {"step": self._steps}
        if truncated:
            info["final"] = True
        return canon.json_copy(obs), reward, False, truncated, info

    def timeout(self) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        """Wall-clock limit reached (called by the runner): truncate with an empty answer."""
        if not self._ready:
            raise RuntimeError("reset() must be called before timeout()")
        if self._done:
            return self.step(None)
        grade = self._finish(None, outcome="truncated", inspection_steps=self._steps)
        obs = {"result": None, "error": {"code": "timeout", "message": "time limit reached"},
               "steps_remaining": max(self.max_steps - self._steps, 0)}
        return obs, self._visible_reward(grade), False, True, {"step": self._steps, "final": True}

    def closed_result(self) -> Optional[Dict[str, Any]]:
        """Evaluator-side grade of the finished episode. Never exposed to the policy."""
        return None if self._grade is None else self._grade.as_dict()

    def final_submission(self) -> Optional[Dict[str, Any]]:
        """What the policy submitted, as sent, for the evaluator (never shown to the policy).

        Includes a structurally invalid submit (e.g. missing or foreign episode_id), so that
        score_episode grades it as an invalid submit (return -1) rather than as empty findings.
        None if the episode ended without submit (truncated) or has not ended."""
        if self._grade is None or self._grade.outcome not in ("submitted", "malformed"):
            return None
        return self._final_submission

    @property
    def inspection_steps(self) -> Optional[int]:
        return None if self._grade is None else self._inspection_steps

    @property
    def episode_id(self) -> str:
        return self._episode_id

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------ internals
    def _load(self, episode: Episode, mode: str) -> None:
        public = episode.public
        self._grader = (Grader(episode.truth, open_ids(public), episode_id=public.manifest["episode_id"])
                        if episode.truth is not None else None)
        self._episode_id = public.manifest["episode_id"]
        self._manifest = canon.json_copy(public.manifest)
        self._policy = canon.json_copy(public.policy)
        self._tables: Dict[str, List[Dict[str, Any]]] = {t: canon.json_copy(rows) for t, rows in public.tables().items()}
        self._index: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for table, rows in self._tables.items():
            if table == "fx":
                self._index[table] = {fx_key(r): r for r in rows}
            else:
                self._index[table] = {r[PRIMARY_KEYS[table]]: r for r in rows}
        self._documents = {r["doc_id"]: ("invoice", r) for r in self._tables["invoices"]}
        self._documents.update({r["doc_id"]: ("payment", r) for r in self._tables["payments"]})
        self._cursor_key = derive_key("cursor", self._episode_id)
        self._cursors: Dict[str, Tuple[str, int]] = {}
        self._mode = mode
        self._steps = 0
        self._done = False
        self._terminated = False
        self._truncated = False
        self._grade: Optional[Grade] = None
        self._final_submission: Optional[Dict[str, Any]] = None
        self._inspection_steps: Optional[int] = None
        self._ready = True

    def _parse(self, action: Any) -> Tuple[str, Dict[str, Any]]:
        if isinstance(action, str):
            try:
                action = json.loads(action)
            except (ValueError, RecursionError):
                raise _err("invalid_json", "action is not valid JSON") from None
        if not isinstance(action, dict) or not isinstance(action.get("action"), str):
            raise _err("invalid_action", "action must be an object {\"action\": name, \"args\": {...}}")
        if set(action) - {"action", "args"}:
            raise _err("invalid_action", "action object may only contain 'action' and 'args'")
        name = action["action"]
        args = action.get("args", {})
        if name not in ACTIONS:
            raise _err("unknown_action", "unknown action; valid: " + ", ".join(ACTIONS))
        if name == "submit":
            return name, args
        if not isinstance(args, dict):
            raise _err("invalid_arguments", "args must be an object")
        required, optional = ARG_KEYS[name]
        if not required <= set(args) or set(args) - required - optional:
            raise _err("invalid_arguments", f"{name} takes required {sorted(required)} and optional {sorted(optional)}")
        return name, args

    def _dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "list_records":
            return self._list_records(args.get("table"), args.get("cursor"), args.get("limit"))
        if name == "get_record":
            return self._get_record(args["table"], args["id"])
        if name == "get_document":
            return self._get_document(args["doc_id"])
        if name == "calculator":
            try:
                return {"value": evaluate(args["expression"])}
            except CalculatorError as exc:
                raise _err("calculator_error", str(exc)) from None
        raise AssertionError("unreachable")

    def _table(self, table: Any) -> str:
        if not isinstance(table, str) or table not in TABLES:
            raise _err("unknown_table", "table must be one of " + ", ".join(TABLES))
        return table

    def _cursor_for(self, table: str, offset: int) -> str:
        token = "c_" + canon.keyed_token(self._cursor_key, f"{table}|{offset}", 16)
        self._cursors[token] = (table, offset)
        return token

    def _list_records(self, table: Any, cursor: Any, limit: Any) -> Dict[str, Any]:
        table = self._table(table)
        if limit is None:
            limit = self.page_limit
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise _err("invalid_arguments", f"limit must be an integer in 1..{self.page_limit}")
        limit = min(limit, self.page_limit)
        offset = 0
        if cursor is not None:
            if not isinstance(cursor, str) or cursor not in self._cursors:
                raise _err("invalid_cursor", "cursor is not a cursor returned by list_records")
            cursor_table, offset = self._cursors[cursor]
            if cursor_table != table:
                raise _err("invalid_cursor", "cursor belongs to a different table")
        rows = self._tables[table]
        page = rows[offset:offset + limit]
        nxt = offset + len(page)
        return {
            "table": table,
            "records": page,
            "next_cursor": self._cursor_for(table, nxt) if nxt < len(rows) else None,
            "total_rows": len(rows),
        }

    def _get_record(self, table: Any, record_id: Any) -> Dict[str, Any]:
        table = self._table(table)
        if not isinstance(record_id, str):
            raise _err("invalid_arguments", "id must be a string")
        record = self._index[table].get(record_id)
        if record is None:
            raise _err("not_found", "no record with this id in this table")
        return {"table": table, "record": record}

    def _get_document(self, doc_id: Any) -> Dict[str, Any]:
        if not isinstance(doc_id, str):
            raise _err("invalid_arguments", "doc_id must be a string")
        found = self._documents.get(doc_id)
        if found is None:
            raise _err("not_found", "no document with this doc_id")
        doc_type, record = found
        return {"doc_id": doc_id, "doc_type": doc_type, "document": record}

    def _submit(self, args: Any) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        grade = self._finish(args, outcome="submitted", inspection_steps=self._steps - 1)
        result: Dict[str, Any] = {"status": "received"}
        error = None
        if self._mode == "train" and grade.outcome == "malformed":
            error = {"code": "malformed_submission", "message": "; ".join(grade.errors)[:500]}
        obs = {"result": result, "error": error, "steps_remaining": max(self.max_steps - self._steps, 0)}
        return canon.json_copy(obs), self._visible_reward(grade), True, False, {"step": self._steps, "final": True}

    def _finish(self, submission: Any, outcome: str, inspection_steps: int) -> Grade:
        self._final_submission = canon.json_copy(submission) if outcome == "submitted" and _jsonable(submission) else None
        self._inspection_steps = inspection_steps
        if self._grader is None:  # closed episode loaded without truth: grading happens in the evaluator
            valid, errors = (True, []) if outcome == "truncated" else validate_submission(submission, self._episode_id)
            grade = Grade(reward=0.0, valid=valid, outcome=outcome if valid else "malformed",
                          inspection_steps=inspection_steps, metrics={}, errors=errors)
            self._grade = grade
            self._done = True
            self._terminated = outcome == "submitted"
            self._truncated = outcome == "truncated"
            return grade
        try:
            grade = self._grader.grade(submission, inspection_steps=inspection_steps, outcome=outcome)
        except Exception as exc:  # an evaluator fault must never reach the agent
            grade = Grade(
                reward=-1.0,
                valid=False,
                outcome="evaluator_error",
                inspection_steps=inspection_steps,
                metrics={"tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "reward": -1.0},
                errors=[f"evaluator raised {type(exc).__name__}: {exc}"[:500]],
            )
        self._grade = grade
        self._done = True
        self._terminated = outcome == "submitted"
        self._truncated = outcome == "truncated"
        return grade

    def _visible_reward(self, grade: Grade) -> float:
        if self._mode != "train" or grade.outcome == "evaluator_error":
            return 0.0
        return grade.reward
