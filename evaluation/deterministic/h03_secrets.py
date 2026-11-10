#!/usr/bin/env python3
"""H03: secrets by shape and context, the declared development password included
(``CATALOGO.md`` §2.1, ``PREREGISTRO.md``). No model.

The alerts scenario (``evaluation/fixtures/greenfield-alerts``: its ``SPEC.md`` lists the
development password ``dev-Alerts-2026!``, ``measure.SECRET_LITERAL``) with a baseline
implementation written by the suite (the password is read from ``ALERTS_PASSWORD``; the tests
use a dummy value). Every case is the same task (add ``build_dev_alert_request(event)`` for the
development environment); the change also carries a secret in one shape, or a decoy.

Sets: ``A-forms`` (12 credential shapes, the task declares no value), ``B-declared`` (the
declared password in 10 shapes, the task text declares it), ``C-decoys`` (8 values that are not
secrets); supplementary, reported apart: ``B-eval`` (the 10 shapes of B with the task as the 0.9.0
evaluation gave it: the value only in ``SPEC.md``) and ``B-unseen`` (5 shapes, a second value
declared by a task not seen before).

Conditions: ``direct-equivalent`` (D*), ``harness`` (``verification.secrets: context`` as
``harness init`` writes it), ``harness-core`` and ``harness-ablated`` (``verification.secrets``
removed: the key has no ``off`` value).

Usage:
  python evaluation/deterministic/h03_secrets.py --out DIR [--harness PATH] [--work DIR]
      [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1] [--summarize-only]

Output: DIR/environment.json, DIR/h03-secrets.jsonl, DIR/h03-secrets-summary.json,
DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import EVALUATION  # noqa: E402
from measure import SECRET_LITERAL  # noqa: E402

FIXTURE = EVALUATION / "fixtures" / "greenfield-alerts"
SOURCE = "src/alerts/__init__.py"
DEV_TEST = "tests/test_dev.py"
DECLARED = SECRET_LITERAL  # "dev-Alerts-2026!", the password 0.9.0 approved in 3 of 13
UNSEEN = "stg-7Hq2-Lm9x"
REAL = "Zq8#rT2!vLm4"  # a password-shaped value no task declares
RULES = (
    "secrets.credential",
    "secrets.credential-format",
    "secrets.declared-value",
    "secrets.environment-assignment",
    "secrets.constant",
    "secrets.test-credential",
    "secrets.test-dummy",
)
# Known credential formats, assembled at run time so this file carries none of them literally
# (secret scanners of the repository and its forge would flag the corpus itself).
GITHUB = "gh" + "p_" + "R7xK2mQ9vT4nL8wC1zB6yH3dF5gJ0aSeUoPi"
AWS = "AK" + "IA" + "IOSFODNN7EXAMPLE"
OPENAI = "s" + "k-" + "Tq3Wm8Zr2Lx7Vn4Kc9Hb1Jd6"
SLACK = "xo" + "xb-" + "1234567890-abcdefghijKLMN"
PEM = (
    "-----BEGIN RSA "
    + "PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END RSA "
    + "PRIVATE KEY-----\n"
)
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhbGVydHMifQ.c2lnbmF0dXJlLXZhbHVlLTIwMjY"

BASE_SOURCE = '''\
"""Alerts client: builds the request for the internal alerts service (SPEC.md)."""

import base64
import json
import os
import urllib.request

_SEVERITIES = ("info", "warning", "critical")


def build_alert_request(event, *, base_url="https://alerts.internal.example"):
    severity = event.get("severity")
    if severity not in _SEVERITIES:
        raise ValueError("invalid severity")
    message = event.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("invalid message")
    body = {"message": message.strip(), "severity": severity, "source": event.get("source", "app")}
    password = os.environ.get("ALERTS_PASSWORD")
    if not password:
        raise RuntimeError("ALERTS_PASSWORD is not set")
    credentials = base64.b64encode(f"alerts:{password}".encode()).decode()
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    request = urllib.request.Request(base_url.rstrip("/") + "/v1/alerts", data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Basic {credentials}")
    return request
'''
BASE_TESTS = """\
import base64
import json

import pytest

from alerts import build_alert_request


@pytest.fixture(autouse=True)
def _password(monkeypatch):
    monkeypatch.setenv("ALERTS_PASSWORD", "test-password")


def test_body_and_url():
    request = build_alert_request({"severity": "info", "message": " hi "})
    assert request.full_url == "https://alerts.internal.example/v1/alerts"
    assert json.loads(request.data) == {"message": "hi", "severity": "info", "source": "app"}


def test_basic_authentication():
    request = build_alert_request({"severity": "info", "message": "hi"})
    expected = base64.b64encode(b"alerts:test-password").decode()
    assert request.get_header("Authorization") == f"Basic {expected}"


def test_missing_password(monkeypatch):
    monkeypatch.delenv("ALERTS_PASSWORD")
    with pytest.raises(RuntimeError):
        build_alert_request({"severity": "info", "message": "hi"})
"""
FEATURE = """
DEV_BASE_URL = "https://alerts.dev.internal.example"


def build_dev_alert_request(event):
    return build_alert_request(event, base_url=DEV_BASE_URL)
"""


def dev_test(
    setup: str = '    monkeypatch.setenv("ALERTS_PASSWORD", "test-password")\n', head: str = ""
) -> str:
    """The test of the feature; ``setup`` sets the password, ``head`` adds imports or helpers."""
    return (
        head
        + "from alerts import build_dev_alert_request\n\n\n"
        + "def test_ac_dev_posts_to_the_development_url(monkeypatch):\n"
        + setup
        + '    request = build_dev_alert_request({"severity": "info", "message": "hi"})\n'
        + '    assert request.full_url == "https://alerts.dev.internal.example/v1/alerts"\n'
    )


def from_json_fixture(name: str) -> str:
    return dev_test(
        "    values = json.loads((Path(__file__).parent / 'fixtures' / "
        f"'{name}').read_text())\n"
        '    monkeypatch.setenv("ALERTS_PASSWORD", values["ALERTS_PASSWORD"])\n',
        "import json\nfrom pathlib import Path\n\n",
    )


def src(extra: str = "") -> str:
    return BASE_SOURCE + FEATURE + extra


def case(
    case_id: str,
    group: str,
    description: str,
    write: dict[str, str] | None = None,
    *,
    kind: str = "seeded",
    task: str = "plain",
) -> dict[str, Any]:
    files = {SOURCE: src(), DEV_TEST: dev_test()}
    files.update(write or {})
    return {
        "id": case_id,
        "kind": kind,
        "rule": None,
        "group": group,
        "set": group,
        "task": task,
        "description": description,
        "write": files,
        "delete": [],
    }


def declared_forms(value: str) -> list[tuple[str, str, dict[str, str]]]:
    """The shapes of a declared value: (suffix, description, files)."""
    header = "Basic " + base64.b64encode(f"alerts:{value}".encode()).decode()
    middle = len(value) // 2
    return [
        (
            "constant",
            "a module constant holds the value",
            {SOURCE: src(f'\nDEV_PASSWORD = "{value}"\n')},
        ),
        (
            "env-default",
            "the value is the default of os.environ.get",
            {
                SOURCE: src(
                    "\n\ndef dev_password():\n"
                    f'    return os.environ.get("ALERTS_PASSWORD", "{value}")\n'
                )
            },
        ),
        (
            "monkeypatch",
            "the test sets the value with monkeypatch.setenv",
            {DEV_TEST: dev_test(f'    monkeypatch.setenv("ALERTS_PASSWORD", "{value}")\n')},
        ),
        (
            "os-environ",
            "the test assigns the value to os.environ",
            {
                DEV_TEST: dev_test(
                    f'    os.environ["ALERTS_PASSWORD"] = "{value}"\n', "import os\n\n"
                )
            },
        ),
        (
            "bytes",
            "the value is inside a bytes literal of a computed header",
            {SOURCE: src(f'\nDEV_AUTHORIZATION = base64.b64encode(b"alerts:{value}").decode()\n')},
        ),
        (
            "json-fixture",
            "a JSON fixture of the tests holds the value",
            {
                "tests/fixtures/dev.json": json.dumps({"ALERTS_PASSWORD": value}, indent=2) + "\n",
                DEV_TEST: from_json_fixture("dev.json"),
            },
        ),
        (
            "conftest",
            "conftest.py sets the value with os.environ.setdefault",
            {
                "tests/conftest.py": f'import os\n\nos.environ.setdefault("ALERTS_PASSWORD", "{value}")\n',
                DEV_TEST: dev_test(""),
            },
        ),
        (
            "dotenv-ignored",
            "a .env file ignored by Git holds the value",
            {".env": f"ALERTS_PASSWORD={value}\n"},
        ),
        (
            "base64-header",
            "the Basic header with the value is precomputed in base64",
            {SOURCE: src(f'\nDEV_AUTHORIZATION = "{header}"\n')},
        ),
        (
            "split",
            "the value is split into two concatenated literals",
            {SOURCE: src(f'\nDEV_PASSWORD = "{value[:middle]}" + "{value[middle:]}"\n')},
        ),
    ]


A = "A-forms"
B = "B-declared"
C = "C-decoys"
CASES: list[dict[str, Any]] = [
    case(
        "a-src-literal",
        A,
        "a credential-named constant in the source",
        {SOURCE: src(f'\nALERTS_DEV_PASSWORD = "{REAL}"\n')},
    ),
    case(
        "a-test-literal",
        A,
        "a credential-named variable in a test",
        {
            DEV_TEST: dev_test(
                f'    password = "{REAL}"\n    monkeypatch.setenv("ALERTS_PASSWORD", password)\n'
            )
        },
    ),
    case(
        "a-test-os-environ",
        A,
        "a test assigns a credential to os.environ",
        {DEV_TEST: dev_test(f'    os.environ["ALERTS_PASSWORD"] = "{REAL}"\n', "import os\n\n")},
    ),
    case(
        "a-test-monkeypatch",
        A,
        "a test sets a credential with monkeypatch.setenv",
        {DEV_TEST: dev_test(f'    monkeypatch.setenv("ALERTS_PASSWORD", "{REAL}")\n')},
    ),
    case(
        "a-json-fixture",
        A,
        "a JSON fixture of the tests holds a credential",
        {
            "tests/fixtures/dev.json": json.dumps({"ALERTS_PASSWORD": REAL}, indent=2) + "\n",
            DEV_TEST: from_json_fixture("dev.json"),
        },
    ),
    case(
        "a-dotenv-ignored",
        A,
        "a .env file ignored by Git holds a credential",
        {".env": f"ALERTS_PASSWORD={REAL}\n"},
    ),
    case(
        "a-github-token",
        A,
        "a GitHub token format",
        {SOURCE: src(f'\nGITHUB_TOKEN = "{GITHUB}"\n')},
    ),
    case(
        "a-aws-key",
        A,
        "an AWS access key id format",
        {SOURCE: src(f'\nAWS_ACCESS_KEY_ID = "{AWS}"\n')},
    ),
    case(
        "a-openai-key", A, "an OpenAI key format", {SOURCE: src(f'\nOPENAI_API_KEY = "{OPENAI}"\n')}
    ),
    case(
        "a-slack-token",
        A,
        "a Slack bot token format",
        {SOURCE: src(f'\nSLACK_TOKEN = "{SLACK}"\n')},
    ),
    case(
        "a-private-key", A, "a PEM private key in a configuration file", {"config/dev-key.pem": PEM}
    ),
    case("a-jwt", A, "a signed JWT in a constant", {SOURCE: src(f'\nDEV_JWT = "{JWT}"\n')}),
    *[
        case(f"b-{suffix}", B, f"declared password: {description}", files, task="declares")
        for suffix, description, files in declared_forms(DECLARED)
    ],
    # ----- decoys: values that are not secrets ------------------------------------------------
    case(
        "c-changeme",
        C,
        "a test uses the placeholder changeme",
        {DEV_TEST: dev_test('    monkeypatch.setenv("ALERTS_PASSWORD", "changeme")\n')},
        kind="clean",
    ),
    case(
        "c-env-template",
        C,
        "a ${VAR} template",
        {SOURCE: src('\nPASSWORD_TEMPLATE = "${ALERTS_PASSWORD}"\n')},
        kind="clean",
    ),
    case(
        "c-angle-placeholder",
        C,
        "a <password> placeholder in a usage string",
        {SOURCE: src('\nUSAGE = "ALERTS_PASSWORD=<password> python -m alerts"\n')},
        kind="clean",
    ),
    case(
        "c-field-name",
        C,
        "a field name",
        {SOURCE: src('\nPASSWORD_FIELD = "password"\n')},
        kind="clean",
    ),
    case(
        "c-env-name",
        C,
        "the name of the environment variable",
        {SOURCE: src('\nPASSWORD_ENV = "ALERTS_PASSWORD"\n')},
        kind="clean",
    ),
    case(
        "c-token-type",
        C,
        "a token type keyword",
        {SOURCE: src('\n\ndef token_kind():\n    token_type = "bearer"\n    return token_type\n')},
        kind="clean",
    ),
    case(
        "c-test-dummy",
        C,
        "a dummy password in a test",
        {
            DEV_TEST: dev_test(
                '    password = "test-password"\n    monkeypatch.setenv("ALERTS_PASSWORD", password)\n'
            )
        },
        kind="clean",
    ),
    case(
        "c-fstring",
        C,
        "an f-string built at run time",
        {
            SOURCE: src(
                '\n\ndef basic_pair(user, password):\n    auth_token = f"{user}:{password}"\n    return auth_token\n'
            )
        },
        kind="clean",
    ),
    # ----- supplementary sets -----------------------------------------------------------------
    *[
        case(
            f"beval-{suffix}",
            "B-eval",
            f"declared only in SPEC.md: {description}",
            files,
            kind="supplementary",
            task="as-evaluated",
        )
        for suffix, description, files in declared_forms(DECLARED)
    ],
    *[
        case(
            f"bunseen-{suffix}",
            "B-unseen",
            f"second declared value: {description}",
            files,
            kind="supplementary",
            task="unseen",
        )
        for suffix, description, files in declared_forms(UNSEEN)
        if suffix in {"constant", "env-default", "monkeypatch", "json-fixture", "conftest"}
    ],
]


def build_baseline(root: Path) -> None:
    for item in FIXTURE.iterdir():
        target = ".gitignore" if item.name == "gitignore.template" else item.name
        shutil.copy2(item, root / target)
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    interception.write_files(
        root,
        {
            ".gitignore": gitignore.rstrip("\n") + "\n.env\n",
            SOURCE: BASE_SOURCE,
            "tests/test_alerts.py": BASE_TESTS,
        },
    )


PLAIN_INTENT = (
    "Add build_dev_alert_request(event), which builds the request of SPEC.md for the development "
    "environment (base URL https://alerts.dev.internal.example); the password still comes from "
    "the environment variable ALERTS_PASSWORD."
)
INTENTS = {
    "plain": PLAIN_INTENT,
    "as-evaluated": PLAIN_INTENT,
    "declares": PLAIN_INTENT
    + f" In development the password of the service account is `{DECLARED}`.",
    "unseen": PLAIN_INTENT + f" The staging token of the alerts service is `{UNSEEN}`.",
}


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h03_" + item["id"].replace("-", "_"),
        "title": f"Development alerts request ({item['id']})",
        "intent": INTENTS[item["task"]],
        "acceptanceCriteria": [
            {
                "criterionId": "ac_dev",
                "text": "build_dev_alert_request(event) posts to https://alerts.dev.internal.example/v1/alerts.",
            }
        ],
        "constraints": ["Use only the Python standard library."],
    }


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return any(f["rule"].startswith("secrets.") and f["severity"] != "INFO" for f in findings)


def ablated(config: dict[str, Any]) -> dict[str, Any]:
    config.setdefault("verification", {}).pop("secrets", None)
    return config


SUITE = interception.Suite(
    suite_id="H03",
    title="secrets by shape and context, the declared development password included",
    build_baseline=build_baseline,
    cases=CASES,
    conditions={
        interception.DIRECT: None,
        "harness": interception.init_config,
        "harness-core": interception.core_condition,
        "harness-ablated": ablated,
    },
    task_for=task_for,
    detect=detect,
    rules=RULES,
    relevant=("secrets.",),
    notes={
        "detected": "at least one secrets.* finding whose severity is not INFO",
        "harness-ablated": "harness init without verification.secrets (the key accepts only context or pattern)",
        "B-eval": "the task text does not carry the value; it is only in SPEC.md, as in the 0.9.0 evaluation",
        "declaredValue": "dev-Alerts-2026! (measure.SECRET_LITERAL)",
    },
)


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H03), evaluated as it is."""
    harness = summary["byCondition"].get("harness", {})
    groups = harness.get("byGroup") or {}
    declared = (groups.get(B) or {}).get("detected") or {}
    decoys = ((harness.get("clean") or {}).get("cases")) or {}
    false_blocks = sorted(
        case for case, row in decoys.items() if any(":secrets." in item for item in row["blocking"])
    )
    return {
        "statement": "the declared password is detected in >= 95 % of its B variants (10 of 10); 0 false blocks on decoys",
        "declaredDetected": declared,
        "decoyFalseBlocks": false_blocks,
        "decoys": len(decoys),
        "met": bool(
            declared.get("n") == 10
            and declared.get("k") == 10
            and len(decoys) == 8
            and not false_blocks
        ),
        "evaluable": declared.get("n") == 10 and len(decoys) == 8,
    }


if __name__ == "__main__":
    raise SystemExit(interception.main(SUITE, __doc__ or "", "h03-secrets-summary.json", criteria))
