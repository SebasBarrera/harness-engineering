"""``verification.secretsDeclared`` (2.1): which values the task text declares as secrets.

The negative cases copy the shape of the acceptance criteria and code lines of the security
scenario of the 2.0.1 campaign, where ``nearby`` turned JSON keys, exception names and field
names into declared secrets. No value here is a real credential."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governed_harness.checks.model import parse_unified_diff
from governed_harness.checks.secrets import DeclaredSecrets, scan_secrets
from governed_harness.configuration.init_project import initialize_project
from governed_harness.configuration.models import VerificationConfig
from governed_harness.domain.enums import FindingSeverity

DECLARED = "secrets.declared-value"
CRITICAL = FindingSeverity.CRITICAL

CAMPAIGN_CRITERIA = "\n".join(
    [
        'Con ALERTS_PASSWORD="pw": evento {"severity":"critical","message":"Disk full",'
        '"source":"api"} produce una petición POST con la cabecera Authorization.',
        'Con ALERTS_PASSWORD="pw" y un evento sin "severity" se lanza "ValueError"; sin la '
        'variable de entorno se lanza "RuntimeError" con el mensaje "ALERTS_PASSWORD".',
        "Las pruebas definen `ALERTS_PASSWORD` con un valor falso (por ejemplo `secret`) "
        "mediante `monkeypatch` y construyen un `Request`.",
        'Con `ALERTS_PASSWORD="pässword"`, la cabecera `Authorization` contiene el base64 de '
        '`alerts:pässword`. Con `ALERTS_PASSWORD="   "` no se lanza `RuntimeError`.',
        "La contraseña de prueba es `test-pass`; el token es obligatorio.",
    ]
)
CAMPAIGN_LINES = (
    "class _AlertRequest(urllib.request.Request):",
    "    except ValueError:",
    '    severity = event.get("severity")',
    '    message = event.get("message")',
    '    password = os.environ.get("ALERTS_PASSWORD")',
    '        "Authorization": "Basic " + token,',
    'PASSWORD_VAR = "ALERTS_PASSWORD"',
    '        raise RuntimeError("ALERTS_PASSWORD is not set")',
    'SEVERITIES = ("info", "warning", "critical")',
    '    """Orden de validación: severity, message, source y contraseña."""',
    '    assert body == {"severity": "critical", "message": "Disk full", "source": "api"}',
    "def _pw(monkeypatch):",
    '    monkeypatch.setenv("ALERTS_PASSWORD", "secret")',
    '    monkeypatch.setenv("ALERTS_PASSWORD", "test-pw")',
    '    monkeypatch.setenv("ALERTS_PASSWORD", "pässword")',
    'PW = "test-pass"',
)


def declared(path: str, lines: tuple[str, ...], task_text: str, mode: DeclaredSecrets) -> list[int]:
    """Line numbers with a ``secrets.declared-value`` issue."""
    diff = "\n".join(
        [f"--- a/{path}", f"+++ b/{path}", f"@@ -1,0 +1,{len(lines)} @@"]
        + [f"+{line}" for line in lines]
    )
    issues = scan_secrets(
        parse_unified_diff(diff + "\n"),
        task_text=task_text,
        declared_secrets=mode,
    )
    found = [issue for issue in issues if issue.rule_id == DECLARED]
    assert all(issue.severity is CRITICAL for issue in found)
    return [issue.line or 0 for issue in found]


class TestCampaignFalsePositives:
    @pytest.mark.parametrize("path", ["src/alerts/api.py", "tests/acceptance/test_alerts.py"])
    def test_explicit_declares_nothing_from_field_names_and_exceptions(self, path: str) -> None:
        assert declared(path, CAMPAIGN_LINES, CAMPAIGN_CRITERIA, "explicit") == []

    def test_nearby_keeps_the_2_0_1_behaviour(self) -> None:
        # Reproducibility of the 2.0.1 campaign: without the key the same lines are critical.
        lines = declared("src/alerts/api.py", CAMPAIGN_LINES, CAMPAIGN_CRITERIA, "nearby")
        assert len(lines) >= 10


class TestExplicitDeclarations:
    @pytest.mark.parametrize(
        "task_text",
        [
            'In staging ALERTS_PASSWORD="Kq7-river-Stone" protects the endpoint.',
            "Use DB_PASSWORD=Kq7-river-Stone for the shared database.",
            'The request body is {"password": "Kq7-river-Stone"}.',
            "The admin password is Kq7-river-Stone for now.",
            'The api key: "Kq7-river-Stone".',
            "La contraseña es `Kq7-river-Stone` en desarrollo.",
            "Clave: Kq7-river-Stone.",
        ],
    )
    @pytest.mark.parametrize(
        "path", ["src/alerts/settings.py", "tests/conftest.py", "deploy/.env.example"]
    )
    def test_a_declared_secret_written_into_the_code_stays_critical(
        self, task_text: str, path: str
    ) -> None:
        lines = (
            "import os",
            'FALLBACK = "Kq7-river-Stone"',
            '    monkeypatch.setenv("ALERTS_PASSWORD", "Kq7-river-Stone")',
        )
        assert declared(path, lines, task_text, "explicit") == [2, 3]

    def test_the_value_matches_as_a_whole_token(self) -> None:
        task_text = 'ALERTS_PASSWORD="Kq7-river"'
        lines = ('A = "Kq7-river"', 'B = "Kq7-rivers"', 'C = "xKq7-river"', "D = Kq7-river.x")
        assert declared("src/a.py", lines, task_text, "explicit") == [1, 4]
        assert declared("src/a.py", lines, task_text, "nearby") == [1, 2, 3, 4]

    def test_the_message_masks_the_value(self) -> None:
        diff = '--- a/src/a.py\n+++ b/src/a.py\n@@ -1,0 +1,1 @@\n+X = "Kq7-river-Stone"\n'
        issues = scan_secrets(
            parse_unified_diff(diff),
            task_text='ALERTS_PASSWORD="Kq7-river-Stone"',
            declared_secrets="explicit",
        )
        assert issues
        assert all("Kq7-river-Stone" not in issue.message for issue in issues)

    @pytest.mark.parametrize(
        "task_text",
        [
            'ALERTS_PASSWORD="severity" and token="ValueError"',
            'API_TOKEN="ALERTS_PASSWORD", secret: "RuntimeError"',
            "The password is stored in the vault; the token is rotated.",
            'PASSWORD_VAR="Kq7-river-Stone" names the variable.',
            'DB_PASSWORD="https://vault.example/v1/db"',
            'ALERTS_PASSWORD="changeme" and token="dummy-value-1"',
        ],
    )
    def test_not_secrets(self, task_text: str) -> None:
        lines = (
            "severity = 1",
            "except ValueError:",
            "raise RuntimeError(ALERTS_PASSWORD)",
            'URL = "https://vault.example/v1/db"',
            'X = "Kq7-river-Stone"',
            'Y = "changeme"',
            'Z = "dummy-value-1"',
            "stored = rotated = 1",
        )
        assert declared("src/a.py", lines, task_text, "explicit") == []


class TestConfiguration:
    def test_absent_key_is_omitted_from_the_dump(self) -> None:
        config = VerificationConfig.model_validate({"secrets": "context"})
        assert config.secrets_declared is None
        assert "secretsDeclared" not in config.model_dump(by_alias=True)

    def test_init_writes_explicit(self, tmp_path: Path) -> None:
        initialize_project(tmp_path)
        text = (tmp_path / ".harness" / "project.yaml").read_text("utf-8")
        verification = yaml.safe_load(text)["verification"]
        assert verification["secrets"] == "context"
        assert verification["secretsDeclared"] == "explicit"
        assert VerificationConfig.model_validate(verification).secrets_declared == "explicit"
