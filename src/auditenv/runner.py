"""Evaluator-side episode runner. Records trajectories (no truth) and keeps grades separate."""

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from auditenv import canon
from auditenv.env import AuditEnv
from auditenv.generator import Episode, generate_episode
from auditenv.policies import Policy

EPISODE_TIME_LIMIT_S = 60.0


@dataclass
class EpisodeRun:
    episode_id: str
    policy: str
    seed: int
    trajectory: List[Dict[str, Any]]
    result: Dict[str, Any]
    steps: int
    wall_time_s: float


def _record(episode_id: str, step: int, action: Any, obs: Dict[str, Any], reward: float,
            terminated: bool, truncated: bool) -> Dict[str, Any]:
    try:
        action = canon.json_copy(action)
    except (TypeError, ValueError):
        action = repr(action)[:2000]
    return {
        "episode_id": episode_id,
        "step": step,
        "action": action,
        "observation": obs,
        "reward": reward,
        "terminated": terminated,
        "truncated": truncated,
    }


def run_episode(policy: Policy, seed: int, config: Optional[Dict[str, Any]] = None, mode: str = "train",
                time_limit_s: float = EPISODE_TIME_LIMIT_S, episode: Optional[Episode] = None,
                policy_seed: Optional[int] = None) -> EpisodeRun:
    """In-process run (training/development). Official evaluation uses auditenv.sandbox.runner."""
    gen_config = {k: v for k, v in (config or {}).items() if k != "mode"}
    if episode is None:
        episode = generate_episode(seed, gen_config)
    env = AuditEnv(episode_factory=lambda _seed, _cfg: episode)
    if policy.requires_truth:
        policy.bind_truth(episode.truth)  # type: ignore[attr-defined]
    policy.reset(seed if policy_seed is None else policy_seed)
    start = time.perf_counter()
    obs, info = env.reset(seed, {"mode": mode})
    episode_id = info["episode_id"]
    trajectory = [_record(episode_id, 0, None, obs, 0.0, False, False)]
    while True:
        if time.perf_counter() - start > time_limit_s:
            obs, reward, terminated, truncated, info = env.timeout()
            trajectory.append(_record(episode_id, info["step"], None, obs, reward, terminated, truncated))
            break
        action = policy.act(obs)
        obs, reward, terminated, truncated, info = env.step(action)
        trajectory.append(_record(episode_id, info["step"], action, obs, reward, terminated, truncated))
        if terminated or truncated:
            break
    wall = time.perf_counter() - start
    result = env.closed_result() or {}
    return EpisodeRun(episode_id, policy.name, seed, trajectory, result, info["step"], wall)


def write_jsonl(path: str, records: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(canon.dumps(r) + "\n")
