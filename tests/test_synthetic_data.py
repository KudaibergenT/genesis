"""Section 12: all companies, people, accounts and money are fictional."""

import glob
import json
import os
import re

import pytest

from auditenv.audit import load_episode
from auditenv.books import books_files
from auditenv.generator import generate_episode
from auditenv.generator.world import SYNTHETIC_MARKER

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+ \(synthetic\)$")
LEGAL_FORMS = re.compile(r"\b(LLC|LLP|Ltd|GmbH|JSC|BV|Inc|Corp|PLC|AG|SA|TOO|AO)\b")
CARD_LIKE = re.compile(r"\b\d{13,19}\b")
EMAIL_OR_PHONE = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\+\d[\d\s()-]{8,}")


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(ROOT, "episodes", "*", "seed_*"))))
def test_committed_episodes_are_marked_synthetic(path):
    ep = load_episode(path)
    assert NAME.match(ep.public.manifest["company"]) and SYNTHETIC_MARKER in ep.public.manifest["company"]
    assert "no real financial or personal data" in ep.public.manifest["synthetic_data"]


def test_generated_names_are_invented_and_never_pose_as_registered_firms():
    names = set()
    for seed in range(300):
        name = generate_episode(seed, {"max_issues": 0}).public.manifest["company"]
        assert NAME.match(name), name
        assert not LEGAL_FORMS.search(name), name
        names.add(name)
    assert len(names) >= 295


@pytest.mark.parametrize("seed", range(0, 60, 6))
def test_open_files_contain_no_personal_or_card_data(seed):
    text = "".join(books_files(generate_episode(seed).public).values())
    assert not CARD_LIKE.search(text)
    assert not EMAIL_OR_PHONE.search(text)
    assert not LEGAL_FORMS.search(text)


def test_counterparties_are_identified_only_by_synthetic_ids():
    ep = generate_episode(3)
    for doc in ep.public.invoices:
        assert re.fullmatch(r"CUST-[A-Z2-7]{6}", doc["entity_id"])
    assert json.dumps(ep.public.invoices).count("(synthetic)") == 0
