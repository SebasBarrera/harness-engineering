"""Engineering principles as deterministic proxies and layer rules in any language (#56)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from governed_harness.checks import parse_unified_diff
from governed_harness.checks.layers import LayerRules, check_layers, imports
from governed_harness.checks.principles import (
    PrincipleLimits,
    check_principles,
    duplication,
    inheritance_depth,
    nesting,
    reformat_only,
    unused_public,
)
from governed_harness.domain.enums import FindingSeverity
from governed_harness.intake.project_kind import detect_project_kind
from governed_harness.orchestration.architecture import (
    render_adr,
    similarity,
    validate_options,
    validate_survey,
)
from governed_harness.orchestration.project_setup import (
    parse_architecture,
    parse_standards,
    parse_testing,
)
from tests.conftest import GIT_ENV, GIT_ISOLATION

MEDIUM = FindingSeverity.MEDIUM


def added_file(path: str, text: str) -> str:
    lines = text.splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


BLOCK = "".join(f"    total = total + item.price * {n}\n" for n in range(8))


def test_duplication_finds_a_copied_block() -> None:
    copy = "def b(items):\n    total = 0\n" + BLOCK + "    return total\n"
    original = "def a(items):\n    total = 0\n" + BLOCK + "    return total\n"
    diff = parse_unified_diff(added_file("src/b.py", copy))
    issues = duplication(
        diff, {"src/b.py": copy}, {"src/a.py": original}, window=6, severity=MEDIUM
    )
    assert [item.rule_id for item in issues] == ["principles.dry.duplication"]
    assert "src/a.py" in issues[0].message
    tests = parse_unified_diff(added_file("tests/test_b.py", copy))
    assert (
        duplication(
            tests, {"tests/test_b.py": copy}, {"src/a.py": original}, window=6, severity=MEDIUM
        )
        == []
    )


def test_nesting_and_inheritance_and_unused_public() -> None:
    deep = (
        "def f(x):\n"
        + "".join("    " * (n + 1) + f"if x > {n}:\n" for n in range(7))
        + ("    " * 8 + "return x\n")
    )
    diff = parse_unified_diff(added_file("src/deep.py", deep))
    assert nesting(diff, {"src/deep.py": deep}, severity=MEDIUM)[0].rule_id == (
        "principles.kiss.nesting"
    )
    chain = "class A:\n    pass\nclass B(A):\n    pass\nclass C(B):\n    pass\n"
    new = "from base import C\n\nclass D(C):\n    pass\n\nclass E(D):\n    pass\n"
    diff = parse_unified_diff(added_file("src/e.py", new))
    found = inheritance_depth(
        diff, {"src/e.py": new}, {"src/base.py": chain}, limit=3, severity=MEDIUM
    )
    assert [item.rule_id for item in found] == ["principles.composition.inheritance"]
    assert "E -> D -> C -> B -> A" in found[0].message
    java = "class Service extends Base {}\n"
    diff = parse_unified_diff(added_file("src/Service.java", java))
    corpus = {"src/Base.java": "class Base extends Root {}\nclass Root extends Top {}\n"}
    assert inheritance_depth(diff, {"src/Service.java": java}, corpus, limit=2, severity=MEDIUM)
    code = "def used() -> int:\n    return 1\n\ndef helper_nobody_calls() -> int:\n    return 2\n"
    diff = parse_unified_diff(added_file("src/api.py", code))
    corpus = {"src/main.py": "from api import used\nused()\n"}
    unused = unused_public(diff, {"src/api.py": code}, corpus, severity=FindingSeverity.LOW)
    assert [item.message.split()[0] for item in unused] == ["helper_nobody_calls"]


def test_reformat_only_files_and_combined_check() -> None:
    diff = parse_unified_diff(
        "--- a/src/other.py\n+++ b/src/other.py\n@@ -1,2 +1,2 @@\n"
        "-def f(a,b):\n-    return a+b\n+def f(a, b):\n+    return a + b\n"
    )
    assert [item.rule_id for item in reformat_only(diff, severity=MEDIUM)] == [
        "principles.boy-scout.reformat-only"
    ]
    real = parse_unified_diff(
        "--- a/src/other.py\n+++ b/src/other.py\n@@ -1,2 +1,2 @@\n"
        "-def f(a,b):\n-    return a+b\n+def f(a, b):\n+    return a - b\n"
    )
    assert reformat_only(real, severity=MEDIUM) == []
    issues = check_principles(
        real,
        {"src/other.py": "def f(a, b):\n    return a - b\n"},
        {},
        PrincipleLimits(unused_public=False),
        severity=MEDIUM,
    )
    assert issues == []


def test_layer_rules_across_languages() -> None:
    rules = LayerRules.from_mapping(
        [
            {"name": "domain", "paths": ["src/shop/domain/**"], "modules": ["shop.domain"]},
            {"name": "infra", "paths": ["src/shop/infra/**"], "modules": ["shop.infra"]},
            {"name": "web", "paths": ["web/src/ui/**"]},
            {"name": "api", "paths": ["web/src/api/**"]},
        ],
        {"infra": ["domain"], "web": ["api"]},
    )
    files = {
        "src/shop/domain/order.py": "from shop.infra.db import save\n",
        "src/shop/infra/db.py": "from shop.domain import order\n",
        "web/src/api/client.ts": "import { Button } from '../ui/button';\n",
        "web/src/ui/page.tsx": "import { get } from '../api/client';\n",
    }
    found = check_layers(files, rules, severity=FindingSeverity.HIGH)
    assert sorted((item.path, item.rule_id) for item in found) == [
        ("src/shop/domain/order.py", "architecture.layer-violation"),
        ("web/src/api/client.ts", "architecture.layer-violation"),
    ]
    assert ("shop.domain", "shop.infra") in rules.forbidden_pairs()
    java = LayerRules.from_mapping(
        [
            {"name": "domain", "modules": ["com.shop.domain"]},
            {"name": "adapters", "modules": ["com.shop.adapters"]},
        ],
        {"adapters": ["domain"]},
    )
    source = "package com.shop.domain;\nimport com.shop.adapters.Db;\n"
    hit = check_layers(
        {"src/main/java/com/shop/domain/Order.java": source}, java, severity=FindingSeverity.HIGH
    )
    assert len(hit) == 1
    assert list(imports("main.go", 'import (\n  "fmt"\n  "example.com/shop/infra"\n)\n')) == [
        (2, "module", "fmt"),
        (3, "module", "example.com/shop/infra"),
    ]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_ISOLATION, *args], cwd=root, check=True, env=GIT_ENV)


def test_project_kind(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "src").mkdir(parents=True)
    (empty / "src" / "__init__.py").write_text("")
    (empty / "src" / "main.py").write_text("def main():\n    pass\n")
    assert detect_project_kind(empty).kind == "new"
    _git(empty, "init", "-q")
    assert detect_project_kind(empty).reason == "the Git repository has no commit"
    (empty / "src" / "orders.py").write_text("def total(items):\n    return sum(items)\n")
    _git(empty, "add", ".")
    _git(empty, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
    kind = detect_project_kind(empty)
    assert (kind.kind, kind.source_files, kind.commits) == ("existing", 1, 1)


def test_setup_answers_and_architecture_payloads() -> None:
    assert parse_testing("We do TDD here") == "tdd"
    assert parse_testing("Gherkin scenarios please") == "bdd"
    assert parse_testing("normal unit tests") == "conventional"
    assert parse_architecture("Ports and adapters (hexagonal)") == "hexagonal"
    assert parse_architecture("a modular monolith") == "modular-monolith"
    assert parse_standards("default", ["python"]) == ["python"]
    assert parse_standards("typescript and react", []) == ["typescript", "react"]
    survey = validate_survey(
        {
            "style": "Layered",
            "summary": "Three layers.",
            "layers": [{"name": "Domain", "paths": ["src/domain/**"]}],
            "allow": {},
        }
    )
    assert survey["style"] == "layered"
    assert survey["layers"][0]["name"] == "domain"
    options = validate_options(
        {
            "options": [
                {
                    "id": "hex",
                    "style": "hexagonal",
                    "title": "Hexagonal",
                    "benefits": ["testable core"],
                    "costs": ["more interfaces"],
                    "recommended": True,
                    "layers": [
                        {"name": "core", "paths": ["src/core/**"]},
                        {"name": "adapters", "paths": ["src/adapters/**"]},
                    ],
                    "allow": {"adapters": ["core"]},
                }
            ]
        }
    )
    adr = render_adr(options[0], decided_by="human.lead", rationale="Fits.", digest="sha256:x")
    assert "ADR-0001" in adr
    assert "adapters (src/adapters/**) may depend on: core" in adr
    assert similarity(["a", "b"], ["a", "b", "c"]) == 2 / 3
