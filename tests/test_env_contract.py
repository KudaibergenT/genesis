import json

import pytest

from auditenv import canon
from auditenv.books import TABLES
from auditenv.env import AuditEnv

from conftest import CLEAN_CONFIG, episode


def make_env(seed=2, config=None):
    ep = episode(seed, config)
    return AuditEnv(episode_factory=lambda s, c: ep), ep


def lst(table, cursor=None, limit=50):
    return {"action": "list_records", "args": {"table": table, "cursor": cursor, "limit": limit}}


def submit(env, findings):
    return {"action": "submit", "args": {"episode_id": env.episode_id, "findings": findings}}


def test_reset_returns_observation_and_info():
    env, ep = make_env()
    obs, info = env.reset(2)
    assert set(obs) == {"task", "manifest", "policy", "tables", "actions", "steps_remaining"}
    assert obs["steps_remaining"] == 100
    assert obs["manifest"] == ep.public.manifest
    assert set(obs["actions"]) == {"list_records", "get_record", "get_document", "calculator", "submit"}
    assert info["episode_id"] == ep.episode_id
    json.dumps(obs), json.dumps(info)


def test_reset_with_real_generator_and_config():
    env = AuditEnv()
    obs, info = env.reset(3, {"mode": "eval", "render": {"row_order": "shuffled"}})
    assert info["mode"] == "eval"
    assert obs["manifest"]["episode_id"] == info["episode_id"]


def test_step_signature_and_types():
    env, _ = make_env()
    env.reset(2)
    out = env.step(lst("ledger"))
    assert len(out) == 5
    obs, reward, terminated, truncated, info = out
    assert isinstance(obs, dict) and set(obs) == {"result", "error", "steps_remaining"}
    assert type(reward) is float and reward == 0.0
    assert terminated is False and truncated is False
    assert info == {"step": 1}
    json.dumps(obs)


def test_step_before_reset_is_an_error():
    with pytest.raises(RuntimeError):
        AuditEnv().step(lst("ledger"))


def test_pagination_covers_table_exactly_once_with_stable_cursors():
    env, ep = make_env()
    env.reset(2)
    for table in TABLES:
        seen, cursor = [], None
        while True:
            obs = env.step(lst(table, cursor))[0]
            page = obs["result"]
            assert len(page["records"]) <= 50
            seen.extend(page["records"])
            if page["next_cursor"] is None:
                break
            again = env.step(lst(table, page["next_cursor"]))[0]["result"]
            first = env.step(lst(table, page["next_cursor"]))[0]["result"]
            assert again == first
            cursor = page["next_cursor"]
        assert seen == ep.public.tables()[table]
        env.reset(2)


@pytest.mark.parametrize("limit, size", [(500, 50), (7, 7), (1, 1)])
def test_limit_is_capped_at_fifty(limit, size):
    env, _ = make_env()
    env.reset(2)
    assert len(env.step(lst("ledger", limit=limit))[0]["result"]["records"]) == size


@pytest.mark.parametrize("args, code", [
    ({"table": "ledger", "limit": 0}, "invalid_arguments"),
    ({"table": "ledger", "limit": True}, "invalid_arguments"),
    ({"table": "ledger", "limit": "5"}, "invalid_arguments"),
    ({"table": "truth"}, "unknown_table"),
    ({"table": "ledger", "cursor": "c_FAKE"}, "invalid_cursor"),
    ({"table": "ledger", "cursor": 5}, "invalid_cursor"),
    ({"table": "ledger", "where": "1=1"}, "invalid_arguments"),
    ({}, "invalid_arguments"),
])
def test_bad_list_arguments_give_bounded_errors(args, code):
    env, _ = make_env()
    env.reset(2)
    obs = env.step({"action": "list_records", "args": args})[0]
    assert obs["result"] is None and obs["error"]["code"] == code
    assert obs["steps_remaining"] == 99


def test_cursor_cannot_cross_tables():
    env, _ = make_env()
    env.reset(2)
    cursor = env.step(lst("ledger"))[0]["result"]["next_cursor"]
    assert env.step(lst("invoices", cursor))[0]["error"]["code"] == "invalid_cursor"


def test_get_record_and_document():
    env, ep = make_env()
    env.reset(2)
    row = ep.public.ledger[5]
    assert env.step({"action": "get_record", "args": {"table": "ledger", "id": row["row_id"]}})[0]["result"]["record"] == row
    inv = ep.public.invoices[0]
    doc = env.step({"action": "get_document", "args": {"doc_id": inv["doc_id"]}})[0]["result"]
    assert doc["doc_type"] == "invoice" and doc["document"] == inv
    pay = ep.public.payments[0]
    assert env.step({"action": "get_document", "args": {"doc_id": pay["doc_id"]}})[0]["result"]["doc_type"] == "payment"
    fx = ep.public.fx[0]
    key = f"{fx['date']}|{fx['source_currency']}|{fx['target_currency']}"
    assert env.step({"action": "get_record", "args": {"table": "fx", "id": key}})[0]["result"]["record"] == fx
    assert env.step({"action": "get_record", "args": {"table": "ledger", "id": "LNOPE"}})[0]["error"]["code"] == "not_found"
    assert env.step({"action": "get_document", "args": {"doc_id": row["row_id"]}})[0]["error"]["code"] == "not_found"


def test_returned_records_cannot_mutate_environment_state():
    env, ep = make_env()
    env.reset(2)
    record = env.step(lst("ledger", limit=1))[0]["result"]["records"][0]
    record["debit_minor"] = 10 ** 12
    assert env.step(lst("ledger", limit=1))[0]["result"]["records"][0] == ep.public.ledger[0]


def test_calculator_action():
    env, _ = make_env()
    env.reset(2)
    assert env.step({"action": "calculator", "args": {"expression": "12345 * 161.237 / 100"}})[0]["result"] == {"value": "19904.70765"}
    assert env.step({"action": "calculator", "args": {"expression": "__import__('os')"}})[0]["error"]["code"] == "calculator_error"


@pytest.mark.parametrize("action, code", [
    ({"action": "open_shell"}, "unknown_action"),
    ({"action": "sql", "args": {"q": "select * from truth"}}, "unknown_action"),
    ("{not json", "invalid_json"),
    ([1, 2], "invalid_action"),
    (None, "invalid_action"),
    ({"args": {}}, "invalid_action"),
    ({"action": "get_record", "args": {"table": "ledger"}, "debug": True}, "invalid_action"),
    ({"action": "get_record", "args": ["ledger", "x"]}, "invalid_arguments"),
    ({"action": "get_record", "args": {"table": "ledger", "id": 5}}, "invalid_arguments"),
])
def test_invalid_actions_cost_a_step_and_do_not_end_the_episode(action, code):
    env, _ = make_env()
    env.reset(2)
    obs, reward, terminated, truncated, _ = env.step(action)
    assert obs["error"]["code"] == code
    assert (reward, terminated, truncated, obs["steps_remaining"]) == (0.0, False, False, 99)


def test_json_string_actions_are_accepted():
    env, _ = make_env()
    env.reset(2)
    assert env.step(json.dumps(lst("fx", limit=2)))[0]["result"]["table"] == "fx"


def test_hundred_steps_without_submit_truncates_with_empty_answer():
    env, ep = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    for i in range(99):
        obs, reward, terminated, truncated, _ = env.step(lst("fx", limit=1))
        assert not truncated and not terminated and reward == 0.0
    obs, reward, terminated, truncated, info = env.step(lst("fx", limit=1))
    assert truncated is True and terminated is False and obs["steps_remaining"] == 0
    assert reward == pytest.approx(2 * 1 - 1 - 0.002 * 100)
    result = env.closed_result()
    assert result["outcome"] == "truncated" and result["experimental"]["strict_timeout_reward"] == -1.0


def test_truncation_on_erroneous_books_scores_minus_one():
    env, _ = make_env()
    env.reset(2)
    for _ in range(100):
        out = env.step({"action": "calculator", "args": {"expression": "1+1"}})
    assert out[3] is True and out[1] == -1.0


def test_submit_terminates_and_counts_inspection_steps():
    env, ep = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    for _ in range(10):
        env.step(lst("fx", limit=1))
    obs, reward, terminated, truncated, info = env.step(submit(env, []))
    assert terminated is True and truncated is False and info["final"] is True
    assert reward == pytest.approx(1 - 0.002 * 10)
    assert obs["result"] == {"status": "received"}


def test_submit_on_last_step_is_terminated_not_truncated():
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    for _ in range(99):
        env.step(lst("fx", limit=1))
    _, reward, terminated, truncated, _ = env.step(submit(env, []))
    assert terminated and not truncated and reward == pytest.approx(1 - 0.002 * 99)


def test_actions_after_the_end_do_nothing():
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    env.step(submit(env, []))
    before = env.closed_result()
    for action in (submit(env, [{"bogus": 1}]), lst("ledger")):
        obs, reward, terminated, truncated, _ = env.step(action)
        assert obs["error"]["code"] == "episode_finished" and reward == 0.0 and terminated
    assert env.closed_result() == before


@pytest.mark.parametrize("args", [
    None,
    "findings",
    {"findings": []},
    {"episode_id": "ep-wrong", "findings": []},
    {"episode_id": "{eid}", "findings": [{"type": "duplicate_invoice"}]},
])
def test_malformed_submission_terminates_with_minus_one(args):
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    if isinstance(args, dict) and args.get("episode_id") == "{eid}":
        args = dict(args, episode_id=env.episode_id)
    obs, reward, terminated, truncated, _ = env.step({"action": "submit", "args": args})
    assert terminated and not truncated and reward == -1.0
    assert obs["error"]["code"] == "malformed_submission"
    assert env.closed_result()["outcome"] == "malformed"


def test_eval_mode_withholds_reward_and_diagnostics():
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5, {"mode": "eval"})
    obs, reward, terminated, _, info = env.step({"action": "submit", "args": {"bad": 1}})
    assert reward == 0.0 and terminated
    assert obs == {"result": {"status": "received"}, "error": None, "steps_remaining": 99}
    assert env.closed_result()["reward"] == -1.0


def test_eval_mode_hides_reward_on_truncation():
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5, {"mode": "eval"})
    for _ in range(100):
        out = env.step(lst("fx", limit=1))
    assert out[3] is True and out[1] == 0.0
    assert env.closed_result()["reward"] == pytest.approx(0.8)


def test_timeout_truncates_with_empty_answer():
    env, _ = make_env(5, CLEAN_CONFIG)
    env.reset(5)
    env.step(lst("fx"))
    obs, reward, terminated, truncated, _ = env.timeout()
    assert truncated and not terminated and obs["error"]["code"] == "timeout"
    assert reward == pytest.approx(1 - 0.002)


def test_reset_isolates_episode_state():
    env, _ = make_env()
    first, _ = env.reset(2)
    for _ in range(5):
        env.step(lst("ledger"))
    env.step(submit(env, []))
    again, _ = env.reset(2)
    assert again == first
    obs = env.step(lst("ledger"))[0]
    assert obs["steps_remaining"] == 99 and env.closed_result() is None


def _replay(seed, actions, config=None):
    env = AuditEnv()
    transcript = [canon.dumps(env.reset(seed, config))]
    for action in actions:
        transcript.append(canon.dumps(env.step(action)))
    transcript.append(canon.dumps(env.closed_result()))
    return transcript


def test_same_seed_config_and_actions_reproduce_everything():
    env = AuditEnv()
    env.reset(7)
    eid = env.episode_id
    first_page = env.step(lst("ledger"))[0]["result"]
    actions = [
        lst("ledger"),
        lst("ledger", first_page["next_cursor"]),
        {"action": "get_record", "args": {"table": "ledger", "id": first_page["records"][3]["row_id"]}},
        {"action": "calculator", "args": {"expression": "2/3"}},
        {"action": "bogus"},
        {"action": "submit", "args": {"episode_id": eid, "findings": []}},
    ]
    assert _replay(7, actions) == _replay(7, actions)
    assert _replay(7, actions, {"mode": "eval"}) == _replay(7, actions, {"mode": "eval"})
    assert _replay(7, actions) != _replay(8, actions)
