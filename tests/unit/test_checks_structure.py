from __future__ import annotations

from textwrap import dedent

from governed_harness.checks.architecture import (
    ArchitectureLimits,
    check_architecture,
    module_name,
)
from governed_harness.checks.constraints import (
    CONSTRAINT_CHECKS,
    check_constraints,
    constraints_from_task,
    first_party_modules,
)
from governed_harness.checks.interface import check_interface, declared_names
from governed_harness.checks.model import Issue, parse_unified_diff
from governed_harness.checks.security_patterns import check_security_patterns, luhn_valid
from governed_harness.domain.enums import FindingSeverity

MEDIUM = FindingSeverity.MEDIUM


def src(text: str) -> str:
    return dedent(text).lstrip("\n")


def rules(issues: list[Issue]) -> list[str]:
    return [issue.rule_id for issue in issues]


def diff_for(path: str, text: str) -> str:
    """A unified diff that adds ``text`` as a new file at ``path``."""
    lines = text.splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def security(path: str, text: str) -> list[Issue]:
    return check_security_patterns(parse_unified_diff(diff_for(path, text)), {path: text})


# interface -------------------------------------------------------------------------------------

DECLARED = src(
    """
    class Dispatcher:
        def __init__(self, fleet: list[str]) -> None: ...
        def advance(self, order_id: str, *, at: int | None = None) -> str: ...
        def _internal(self) -> None: ...

    def create_order(customer: str, items: list[str], priority: int = ...) -> str: ...
    async def notify(order_id: str) -> None: ...
    def _helper() -> None: ...
    """
)


def interface(implementation: str) -> list[Issue]:
    return check_interface(
        DECLARED,
        src(implementation),
        declared_path="spec/api.pyi",
        implementation_path="src/shop/api.py",
        severity=MEDIUM,
    )


GOOD_IMPLEMENTATION = """
class Dispatcher:
    def __init__(self, fleet):
        self.fleet = fleet

    def advance(self, order_id, *, at=None):
        return order_id

def create_order(customer, items, priority=3):
    return customer

async def notify(order_id):
    return None
"""


def test_interface_matching_implementation_has_no_issues() -> None:
    assert interface(GOOD_IMPLEMENTATION) == []


def test_interface_missing_function_class_and_method() -> None:
    issues = interface(
        """
        class Dispatcher:
            def __init__(self, fleet):
                self.fleet = fleet
        """
    )
    messages = {issue.message for issue in issues if issue.rule_id == "interface.missing"}
    assert any("Dispatcher.advance" in message for message in messages)
    assert any("create_order" in message for message in messages)
    assert any("notify" in message for message in messages)
    method = next(issue for issue in issues if "Dispatcher.advance" in issue.message)
    assert method.line == 1
    assert method.path == "src/shop/api.py"
    assert method.category == "interface"
    assert method.severity is MEDIUM


def test_interface_missing_class() -> None:
    issues = interface("def create_order(customer, items, priority=1): return 1\n")
    assert any(
        issue.rule_id == "interface.missing" and "class Dispatcher" in issue.message
        for issue in issues
    )


def test_interface_signature_mismatch_shows_declared_and_actual() -> None:
    issues = interface(
        GOOD_IMPLEMENTATION.replace(
            "def advance(self, order_id, *, at=None)", "def advance(self, order_id, at)"
        )
    )
    signature = [issue for issue in issues if issue.rule_id == "interface.signature"]
    assert len(signature) == 1
    assert "`advance(self, order_id, *, at=None)`" in signature[0].message
    assert "`advance(self, order_id, at)`" in signature[0].message
    assert signature[0].line == 5
    assert signature[0].recommendation


def test_interface_renamed_parameter_and_kwargs_are_signature_issues() -> None:
    renamed = GOOD_IMPLEMENTATION.replace("customer, items", "client, items")
    assert "interface.signature" in rules(interface(renamed))
    extra = GOOD_IMPLEMENTATION.replace("def notify(order_id)", "def notify(order_id, **kwargs)")
    assert "interface.signature" in rules(interface(extra))


def test_interface_async_mismatch() -> None:
    issues = interface(GOOD_IMPLEMENTATION.replace("async def notify", "def notify"))
    assert rules(issues) == ["interface.signature"]
    assert "async notify(order_id)" in issues[0].message


def test_interface_default_mismatch_and_ellipsis_default() -> None:
    issues = interface(GOOD_IMPLEMENTATION.replace("at=None", "at=0"))
    assert rules(issues) == ["interface.default"]
    assert "at: declared None, actual 0" in issues[0].message
    # ``priority = ...`` is declared: any default matches, but a missing default does not.
    no_default = GOOD_IMPLEMENTATION.replace("priority=3", "priority")
    assert rules(interface(no_default)) == ["interface.default"]


def test_interface_stub_bodies() -> None:
    stubbed = GOOD_IMPLEMENTATION.replace(
        "        return order_id",
        '        """Advance."""\n        raise NotImplementedError("later")',
    ).replace("    return customer", "    ...")
    issues = interface(stubbed)
    assert sorted(rules(issues)) == ["interface.stub", "interface.stub"]
    assert {issue.line for issue in issues} == {5, 9}


def test_interface_ignores_annotations_and_accepts_dataclass_and_inherited_methods() -> None:
    implementation = """
    from dataclasses import dataclass, field

    class Base:
        def advance(self, order_id: str, *, at: int | None = None) -> str:
            return order_id

    @dataclass
    class Dispatcher(Base):
        fleet: list[str] = field(default_factory=list)

    def create_order(customer: str, items: list[str], priority: int = 1) -> str:
        return customer

    async def notify(order_id: str) -> None:
        return None
    """
    # The dataclass gives fleet a default the stub does not declare.
    assert rules(interface(implementation)) == ["interface.default"]
    without_default = implementation.replace(" = field(default_factory=list)", "")
    assert interface(without_default) == []


def test_interface_unparsable_sources() -> None:
    issues = interface("def broken(:\n")
    assert rules(issues) == ["interface.unparsable"]
    assert issues[0].severity is FindingSeverity.INFO
    declared_broken = check_interface(
        "def (", "", declared_path="a.pyi", implementation_path="a.py", severity=MEDIUM
    )
    assert rules(declared_broken) == ["interface.unparsable"]


def test_declared_names() -> None:
    assert declared_names(DECLARED) == ["Dispatcher.advance", "create_order", "notify"]
    assert declared_names("def (") == []


# architecture ----------------------------------------------------------------------------------


def test_module_name() -> None:
    assert module_name("src/shop/orders.py") == "shop.orders"
    assert module_name("shop/__init__.py") == "shop"
    assert module_name("app.py") == "app"


def test_architecture_module_and_function_lines() -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(5))
    text = f"def long():\n{body}\n    return 1\n\ndef short():\n    return 2\n"
    limits = ArchitectureLimits(max_module_lines=5, max_function_lines=4)
    issues = check_architecture({"src/app/a.py": text}, limits, severity=MEDIUM)
    assert rules(issues) == ["architecture.module-lines", "architecture.function-lines"]
    assert "10 lines; the limit is 5" in issues[0].message
    assert "long spans 7 lines; the limit is 4" in issues[1].message
    assert issues[1].line == 1
    relaxed = ArchitectureLimits(max_module_lines=100, max_function_lines=10)
    assert check_architecture({"src/app/a.py": text}, relaxed, severity=MEDIUM) == []


def test_architecture_complexity() -> None:
    text = src(
        """
        class Router:
            def route(self, a, b, items):
                if a and b or a:          # if +1, boolean operands +2
                    return 1
                elif b:                   # +1
                    return 2
                for item in items:        # +1
                    while item:           # +1
                        item -= 1
                try:
                    pass
                except ValueError:        # +1
                    pass
                values = [i for i in items if i]  # +2
                match a:
                    case 1:               # +1
                        pass
                    case _:               # +1
                        pass
                return 3 if a else 4      # +1

                def nested():             # measured on its own
                    if a:
                        return 1
        """
    )
    issues = check_architecture(
        {"src/app/router.py": text}, ArchitectureLimits(max_complexity=5), severity=MEDIUM
    )
    assert rules(issues) == ["architecture.complexity"]
    assert "Router.route has cyclomatic complexity 13; the limit is 5" in issues[0].message
    assert issues[0].line == 2
    relaxed = ArchitectureLimits(max_complexity=13)
    assert check_architecture({"src/app/router.py": text}, relaxed, severity=MEDIUM) == []


def test_architecture_forbidden_imports() -> None:
    limits = ArchitectureLimits(forbidden_imports=(("shop.domain", "shop.adapters"),))
    files = {
        "src/shop/domain/orders.py": "from ..adapters import db\nimport json\n",
        "src/shop/domain/__init__.py": "from . import orders\n",
        "src/shop/domain/prices.py": "import shop.adapters.http as http\n",
        "src/shop/adapters/db.py": "from shop.domain import orders\n",
        "src/shop/domain/money.py": "from shop.adaptersx import thing\n",
    }
    issues = check_architecture(files, limits, severity=MEDIUM)
    assert [(issue.path, issue.line) for issue in issues] == [
        ("src/shop/domain/orders.py", 1),
        ("src/shop/domain/prices.py", 1),
    ]
    assert all(issue.rule_id == "architecture.forbidden-import" for issue in issues)
    assert "shop.domain.orders imports shop.adapters" in issues[0].message


def test_architecture_skips_tests_non_python_and_reports_unparsable() -> None:
    limits = ArchitectureLimits(max_module_lines=1, max_complexity=1)
    files = {
        "tests/test_a.py": "a = 1\nb = 2\n",
        "README.md": "a\nb\nc\n",
        "src/app/bad.py": "def (:\n",
    }
    issues = check_architecture(files, limits, severity=MEDIUM)
    assert rules(issues) == ["architecture.unparsable"]
    assert issues[0].severity is FindingSeverity.INFO


# security patterns -----------------------------------------------------------------------------


def test_security_unsafe_deserialization() -> None:
    text = src(
        """
        import pickle, yaml, marshal
        a = pickle.loads(data)
        b = yaml.load(stream)
        c = yaml.load(stream, Loader=yaml.SafeLoader)
        d = yaml.safe_load(stream)
        e = marshal.loads(raw)
        # pickle.loads(data) in a comment
        """
    )
    issues = security("src/app/store.py", text)
    found = [(issue.rule_id, issue.line) for issue in issues]
    assert found == [
        ("security.unsafe-deserialization", 2),
        ("security.unsafe-deserialization", 3),
        ("security.unsafe-deserialization", 6),
    ]
    assert all(issue.severity is FindingSeverity.HIGH for issue in issues)
    assert all(issue.category == "security" for issue in issues)


def test_security_restricted_unpickler_is_allowed_and_tests_are_skipped() -> None:
    text = src(
        """
        import io, pickle

        class SafeUnpickler(pickle.Unpickler):
            def find_class(self, module, name):
                raise pickle.UnpicklingError(name)

        def load(data):
            return pickle.loads(data)
        """
    )
    assert security("src/app/store.py", text) == []
    assert security("tests/test_store.py", "x = pickle.loads(data)\n") == []


def test_security_card_data_stored() -> None:
    text = src(
        """
        from dataclasses import dataclass

        @dataclass
        class Payment:
            cardNumber: str
            card_last4: str
            cvc_token: str

        def save(db, request):
            card_number = request.card_number   # a local variable is not storage
            record = {"cvv": request.cvv}
            db["security_code"] = request.code
            db.insert(pan=request.pan)
            db.execute("CREATE TABLE cards (id INTEGER, card_number TEXT, masked_pan TEXT)")
            return record
        """
    )
    issues = [
        i for i in security("src/app/payments.py", text) if i.rule_id == "security.card-data-stored"
    ]
    names = [issue.message.split(" ", 1)[0] for issue in issues]
    assert names == ["cardNumber", "cvv", "security_code", "pan"]
    assert issues[0].line == 5
    assert issues[0].recommendation is not None and "last four" in issues[0].recommendation


def test_security_card_data_negative() -> None:
    text = src(
        """
        class Payment:
            card_last_four: str
            card_token: str
            company: str
            span: int

        def charge(card_number: str) -> None:
            fingerprint = card_number[-4:]
        """
    )
    assert security("src/app/payments.py", text) == []


def test_security_card_number_literal() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    text = 'CARD = "4111 1111 1111 1111"\nOTHER = "4111-1111-1111-1112"\nID = 0000000000000\n'
    issues = security("src/app/config.py", text)
    assert [(issue.rule_id, issue.line) for issue in issues] == [
        ("security.card-number-literal", 1)
    ]
    assert "ending in 1111" in issues[0].message
    assert security("tests/fixtures/cards.py", text) == []


def test_security_weak_password_hash() -> None:
    text = src(
        """
        import hashlib
        from hashlib import sha256 as digest

        def store(user, password):
            a = hashlib.md5(password.encode()).hexdigest()
            b = digest(user.password_hash_input).hexdigest()
            c = hashlib.sha256(data).hexdigest()
            d = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 1000)
            e = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=600_000)
            f = hashlib.pbkdf2_hmac("sha256", pwd, salt, iterations=5000)
            return a, b, c, d, e, f
        """
    )
    issues = security("src/app/auth.py", text)
    assert [(issue.rule_id, issue.line) for issue in issues] == [
        ("security.weak-password-hash", 5),
        ("security.weak-password-hash", 6),
        ("security.weak-password-hash", 8),
        ("security.weak-password-hash", 10),
    ]


def test_security_unparsable_python() -> None:
    issues = check_security_patterns([], {"src/app/bad.py": "def (:\n"})
    assert rules(issues) == ["security.unparsable"]
    assert issues[0].severity is FindingSeverity.INFO


# constraints -----------------------------------------------------------------------------------


def test_constraints_from_task_sentences_and_declared_names() -> None:
    sentences = [
        "Use only the Python standard library.",
        "Never read the system clock or use random numbers.",
        "Every public function must have type hints.",
        "Do not use float for money amounts.",
        "No stubs or NotImplementedError left behind.",
    ]
    assert constraints_from_task(sentences) == CONSTRAINT_CHECKS
    assert constraints_from_task(["The clock module exists."]) == ()
    assert constraints_from_task(["Deterministic: no random."]) == ("no-clock-random",)
    assert constraints_from_task([], ["no-stubs", "bogus", "stdlib-only", "no-stubs"]) == (
        "stdlib-only",
        "no-stubs",
    )
    assert constraints_from_task(["Use a float for the ratio."]) == ()


def test_first_party_modules() -> None:
    assert first_party_modules(["src/shop/a.py", "app/b.py", "main.py", "tests/test_c.py"]) == {
        "__future__",
        "shop",
        "app",
        "main",
        "tests",
    }


def test_constraints_non_stdlib_import() -> None:
    files = {
        "src/shop/orders.py": src(
            """
            from __future__ import annotations
            import json
            import requests
            import requests.adapters
            from shop import prices
            from . import money
            from attrs import define
            """
        ),
        "src/shop/prices.py": "X = 1\n",
    }
    issues = check_constraints(["stdlib-only"], files, severity=MEDIUM)
    assert [(issue.rule_id, issue.line) for issue in issues] == [
        ("constraints.non-stdlib-import", 3),
        ("constraints.non-stdlib-import", 7),
    ]
    assert issues[0].category == "constraints"


def test_constraints_clock_or_random() -> None:
    text = src(
        """
        import random, secrets, time, uuid, os
        from datetime import datetime, date

        def run():
            a = time.time()
            b = time.monotonic()
            c = datetime.now()
            d = date.today()
            e = random.randint(1, 6)
            f = uuid.uuid4()
            g = random.Random(42)
            h = secrets.token_hex(8)
            return a, b, c, d, e, f, g, h

        def make_salt():
            return secrets.token_bytes(16)

        def hash_it(password):
            salt = os.urandom(16)
            return salt
        """
    )
    issues = check_constraints(["no-clock-random"], {"src/app/run.py": text}, severity=MEDIUM)
    assert [(issue.rule_id, issue.line) for issue in issues] == [
        ("constraints.clock-or-random", 5),
        ("constraints.clock-or-random", 7),
        ("constraints.clock-or-random", 8),
        ("constraints.clock-or-random", 9),
        ("constraints.clock-or-random", 10),
        ("constraints.clock-or-random", 12),
    ]


def test_constraints_unannotated_public_api() -> None:
    text = src(
        """
        def good(a: int, *args: int, **kw: str) -> int:
            return a

        def bad(a, b: int):
            return a

        def no_return(a: int):
            return a

        def _private(a):
            return a

        class Service:
            def __init__(self, repo: object):
                self.repo = repo

            def run(self, item):
                return item

            def __repr__(self):
                return "Service"

        class _Hidden:
            def run(self, item):
                return item
        """
    )
    issues = check_constraints(["annotated-public-api"], {"src/app/svc.py": text}, severity=MEDIUM)
    assert [issue.line for issue in issues] == [4, 7, 17]
    assert "bad is public but lacks type annotations for: a, return" in issues[0].message
    assert "Service.run" in issues[2].message


def test_constraints_float_money() -> None:
    text = src(
        """
        from typing import Optional

        class Order:
            total: float
            discount_rate: float
            fee: Optional[float] = None
            quantity: int = 1

        def charge(amount: float | None, ratio: float, price=0.0) -> int:
            balance = float(amount)
            count = 1.5
            subtotal_cents = 100
            return subtotal_cents
        """
    )
    issues = check_constraints(["no-float-money"], {"src/app/money.py": text}, severity=MEDIUM)
    names = [issue.message.split(" ", 1)[0] for issue in issues]
    assert names == ["total", "fee", "amount", "price", "balance"]
    assert all(issue.rule_id == "constraints.float-money" for issue in issues)


def test_constraints_stubs() -> None:
    text = src(
        '''
        from abc import ABC, abstractmethod
        from typing import Protocol, overload

        def todo():
            raise NotImplementedError

        def documented():
            """Only a docstring."""

        def real():
            return 1

        class Repo(Protocol):
            def get(self) -> int: ...

        class Base(ABC):
            def hook(self) -> None:
                pass

        class Impl:
            @abstractmethod
            def a(self) -> None: ...

            @overload
            def b(self, x: int) -> int: ...

            def c(self) -> None:
                pass
        '''
    )
    issues = check_constraints(["no-stubs"], {"src/app/x.py": text}, severity=MEDIUM)
    assert [(issue.line, issue.message.split(" ", 1)[0]) for issue in issues] == [
        (4, "todo"),
        (7, "documented"),
        (27, "Impl.c"),
    ]


def test_constraints_skip_tests_unknown_checks_and_report_unparsable() -> None:
    files = {"tests/test_x.py": "import requests\n", "src/app/bad.py": "def (:\n"}
    assert check_constraints(["bogus"], files, severity=MEDIUM) == []
    issues = check_constraints(["stdlib-only"], files, severity=MEDIUM)
    assert rules(issues) == ["constraints.unparsable"]
    assert issues[0].severity is FindingSeverity.INFO
