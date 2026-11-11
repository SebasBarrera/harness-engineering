"""The ``security`` risk flag read in context (``governance.riskProfile``), and the 1.x list."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from governed_harness.agents.risk_terms import (
    legacy_security_words,
    security_terms,
    sensitive_path,
)
from governed_harness.configuration.models import GovernanceConfig


@pytest.mark.parametrize(
    "text",
    [
        "Hash the users' passwords with bcrypt before storing them",
        "Add authentication to the export endpoint",
        "Only authorized administrators may delete an invoice",
        "Encrypt the users' e-mail addresses at rest",
        "Validate the bearer token of the request",
        "Refresh the access token when it expires",
        "Rotate the token when the user signs out",
        "Store the API secret in the vault",
        "Mask the credit card number on the receipt",
        "Refund the payment when the order is cancelled",
        "Load the database credentials from the environment",
        "Cifrar los datos personales del usuario",
        "Guardar la contraseña con un hash",
        "Pedir autenticación antes de exportar",
    ],
)
def test_real_security_tasks_are_flagged(text: str) -> None:
    assert security_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "Count the tokens in the tokenizer output",
        "Tokenize the input string on whitespace",
        "Rename the product card component",
        "Discard empty lines of the report",
        "Add a cards view to the dashboard",
        "Show the total of the monthly payments",
        "Keep the secret sauce section of the README short",
        "Fix the typo in the README",
    ],
)
def test_loose_words_do_not_flag_a_task(text: str) -> None:
    assert security_terms(text) == ()


def test_the_1x_list_flags_loose_words_as_before() -> None:
    assert legacy_security_words("Discard empty lines")  # "card" in "discard"
    assert legacy_security_words("Tokenize the input")
    assert not legacy_security_words("Fix the typo in the README")


def test_a_weak_term_counts_in_a_sensitive_file() -> None:
    assert security_terms("Keep the token order", ["src/auth/tokens.py"]) == (
        "token@src/auth/tokens.py",
    )
    assert security_terms("Keep the token order", ["src/sample/tokenizer.py"]) == ()
    assert security_terms("Fix the typo", ["src/auth/tokens.py"]) == ()


def test_sensitive_paths_are_read_by_component() -> None:
    assert sensitive_path("src/payments/refund.py")
    assert sensitive_path("app\\security\\rules.ts")
    assert sensitive_path("src/user_session.py")
    assert not sensitive_path("src/tokenizer.py")
    assert not sensitive_path("src/authority_report.py")


def test_the_terms_say_why() -> None:
    assert security_terms("Validate the bearer token") == ("bearer token", "token~bearer")
    assert security_terms("Store the API secret") == ("secret~store",)


def test_risk_profile_values() -> None:
    assert GovernanceConfig.model_validate({"riskProfile": "auto"}).risk_profile == "auto"
    assert GovernanceConfig.model_validate({}).risk_profile is None
    assert "riskProfile" not in GovernanceConfig.model_validate({}).model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        GovernanceConfig.model_validate({"riskProfile": "minimal"})
