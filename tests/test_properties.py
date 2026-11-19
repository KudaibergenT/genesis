"""Property-based tests: arbitrary inputs never crash the env and never beat the oracle."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from auditenv.env import AuditEnv
from auditenv.env.calculator import CalculatorError, evaluate
from auditenv.evaluator import Grader

from conftest import episode

json_scalars = st.none() | st.booleans() | st.integers(-10 ** 6, 10 ** 6) | st.text(max_size=20)
json_values = st.recursive(json_scalars, lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
                           max_leaves=12)
action_names = st.sampled_from(["list_records", "get_record", "get_document", "calculator", "nope"])
actions = st.one_of(
    json_values,
    st.builds(lambda n, a: {"action": n, "args": a}, action_names, json_values),
    st.builds(lambda t, c, l: {"action": "list_records", "args": {"table": t, "cursor": c, "limit": l}},
              st.sampled_from(["ledger", "invoices", "payments", "fx", "x"]), st.none() | st.text(max_size=12),
              st.integers(-5, 80)),
)
SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow], derandomize=True)


@SETTINGS
@given(st.text(max_size=300))
def test_calculator_only_ever_returns_a_number_or_a_calculator_error(text):
    try:
        value = evaluate(text)
    except CalculatorError:
        return
    assert set(value) <= set("0123456789.-")


@SETTINGS
@given(st.lists(actions, max_size=25))
def test_arbitrary_action_sequences_never_crash_or_end_the_episode(seq):
    ep = episode(2)
    env = AuditEnv(episode_factory=lambda s, c: ep)
    env.reset(2)
    remaining = 100
    for action in seq:
        obs, reward, terminated, truncated, _ = env.step(action)
        remaining -= 1
        assert obs["steps_remaining"] == remaining
        assert reward == 0.0 and not terminated and not truncated
        assert (obs["result"] is None) != (obs["error"] is None)


def _correct(ep):
    return [
        {"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": ""}
        for i in ep.truth["issues"]
    ]


@SETTINGS
@given(st.data())
def test_no_combination_of_findings_beats_the_oracle(data):
    ep = episode(3)
    good = _correct(ep)
    docs = [d["doc_id"] for d in ep.public.invoices + ep.public.payments]
    junk = st.builds(
        lambda t, a, amt, ev: {"type": t, "affected_ids": a, "claimed_amount_minor": amt, "currency": ep.public.manifest["base_currency"],
                               "evidence_ids": ev, "explanation": ""},
        st.sampled_from(["duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff"]),
        st.lists(st.sampled_from(docs), min_size=1, max_size=3),
        st.integers(0, 10 ** 9),
        st.lists(st.sampled_from(docs), max_size=10),
    )
    findings = data.draw(st.lists(st.sampled_from(good) | junk, max_size=30))
    findings = data.draw(st.permutations(findings))
    steps = data.draw(st.integers(0, 99))
    grader = Grader(ep.truth)
    g = grader.grade({"episode_id": ep.episode_id, "findings": findings}, steps)
    oracle = grader.grade({"episode_id": ep.episode_id, "findings": good}, 0)
    assert g.reward <= oracle.reward
    distinct = {(f["type"], tuple(sorted(set(f["affected_ids"])))) for f in findings if f in good}
    assert g.metrics["tp"] == len(distinct)
    assert g.metrics["tp"] + g.metrics["fp"] == len(findings)
    assert -1.0 <= g.reward <= 1.0
