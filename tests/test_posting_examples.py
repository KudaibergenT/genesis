"""docs/posting_rules.md is reproduced exactly by the clean-room verifier from fresh episodes."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_posting_rules_document_is_current():
    out = subprocess.run([sys.executable, "scripts/posting_examples.py", "--check"], cwd=ROOT, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, "docs/posting_rules.md is stale: run python scripts/posting_examples.py\n" + out.stderr


def test_posting_rules_cover_every_rule():
    text = open(os.path.join(ROOT, "docs", "posting_rules.md"), encoding="utf-8").read()
    for heading in ("Счёт в иностранной валюте", "курсовая разница", "Кредит-нота", "Возврат клиенту",
                    "Разбитый платёж", "Платёж без ссылки", "по местной дате", "Как ошибки нарушают"):
        assert heading in text
    assert "|проведено" not in text
