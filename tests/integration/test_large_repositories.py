"""Snapshots for large repositories: listing through Git, the manifest baseline, the sealed digest
cache, the guard over ignored files and the orphan sweep of harness gc."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.models import Finding
from governed_harness.orchestration.engine import EngineServices
from governed_harness.orchestration.retention import RetentionCollector
from governed_harness.runtime.snapshots import (
    CACHE_FILE,
    SnapshotCache,
    SnapshotSettings,
    SnapshotStore,
)

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "    - path: tests/test_pricing.py\n"
    "      operation: append\n"
    "      content: |\n"
    "\n"
    "        def test_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)

SECRET = "API_TOKEN=do-not-store-0123456789"


def set_workspace(root: Path, **keys: Any) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("snapshot", "baseline", "snapshotCache"):
        config["workspace"].pop(key, None)
    config["workspace"].update(keys)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def start(root: Path) -> tuple[HarnessApplication, Any]:
    task_file = root.parent / f"task-{root.name}.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(root, task_file)
    return application, application.start_run(root, task.task_id)


def stored_bytes(root: Path) -> bytes:
    return b"".join(
        path.read_bytes() for path in (root / ".harness" / "artifacts").rglob("*") if path.is_file()
    )


def add_ignored_secret(root: Path) -> None:
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    gitignore.write_text(existing + ".env\n", encoding="utf-8")
    (root / ".env").write_text(SECRET + "\n", encoding="utf-8")


def test_git_snapshot_never_reads_ignored_files(python_workspace: Path) -> None:
    add_ignored_secret(python_workspace)
    application, run = start(python_workspace)
    assert run.current_phase.value == "DECISION"
    assert SECRET.encode() not in stored_bytes(python_workspace)
    with application._services(python_workspace) as services:
        baseline = json.loads(
            services.artifacts.get(services.state.get_flag(f"baseline:{run.execution_id}") or "")
        )
    assert baseline["format"] == "manifest/1"
    assert ".env" not in baseline["files"]
    tracked = baseline["files"]["src/sample/pricing.py"]
    assert "text" not in tracked  # given back by Git when it enters a diff
    assert baseline["files"][".gitignore"]["text"]  # untracked: kept in the manifest


def test_walk_mode_keeps_the_1_0_baseline(python_workspace: Path) -> None:
    add_ignored_secret(python_workspace)
    set_workspace(python_workspace)
    application, run = start(python_workspace)
    with application._services(python_workspace) as services:
        baseline = json.loads(
            services.artifacts.get(services.state.get_flag(f"baseline:{run.execution_id}") or "")
        )
    assert "format" not in baseline
    assert baseline["files"][".env"]["text"].startswith("API_TOKEN")


def test_manifest_and_text_baselines_bind_the_same_digest(
    python_workspace: Path, tmp_path: Path
) -> None:
    twin = tmp_path / "twin" / python_workspace.name
    shutil.copytree(python_workspace, twin, symlinks=True)
    shutil.rmtree(twin / ".harness" / "artifacts", ignore_errors=True)
    for name in ("state.db", "state.db-wal", "state.db-shm"):
        (twin / ".harness" / name).unlink(missing_ok=True)
    set_workspace(python_workspace)
    _, legacy = start(python_workspace)
    _, scaled = start(twin)
    assert legacy.change_set_digest == scaled.change_set_digest
    assert legacy.change_set_digest is not None


def test_the_cache_is_sealed(python_workspace: Path) -> None:
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        flags: dict[str, str] = {}
        seal = (lambda: flags.get("seal"), lambda value: flags.__setitem__("seal", value))
        store = SnapshotStore(
            python_workspace,
            services.artifacts,
            SnapshotSettings(git_listing=True, manifest=True, cache=True),
            services.paths.harness_dir,
            seal,
        )
        target = python_workspace / "src" / "sample" / "pricing.py"
        old = os.stat(target)
        os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns - 10_000_000_000))
        first = store.take()
        cache_path = services.paths.harness_dir / CACHE_FILE
        assert "src/sample/pricing.py" in json.loads(cache_path.read_text())["entries"]
        assert SnapshotCache(cache_path, seal).entries  # the seal matches
        value = json.loads(cache_path.read_text())
        value["entries"]["src/sample/pricing.py"][4] = "sha256:" + "0" * 64
        cache_path.write_text(json.dumps(value))
        assert SnapshotCache(cache_path, seal).entries == {}  # edited: ignored
        assert store.take().files["src/sample/pricing.py"].digest == (
            first.files["src/sample/pricing.py"].digest
        )
    finally:
        services.close()


def test_an_edit_that_restores_the_modification_time_is_seen(python_workspace: Path) -> None:
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        store = SnapshotStore(
            python_workspace,
            services.artifacts,
            SnapshotSettings(git_listing=True, manifest=True, cache=True),
            services.paths.harness_dir,
        )
        target = python_workspace / "src" / "sample" / "pricing.py"
        stat = os.stat(target)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns - 10_000_000_000))
        stat = os.stat(target)
        before = store.take().files["src/sample/pricing.py"].digest
        time.sleep(0.01)
        target.write_text(target.read_text().replace("subtotal", "SUBTOTAL"), encoding="utf-8")
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert os.stat(target).st_size == stat.st_size
        assert store.take().files["src/sample/pricing.py"].digest != before
    finally:
        services.close()


def test_guard_watches_ignored_files_under_git_snapshots(
    python_workspace: Path, tmp_path: Path
) -> None:
    add_ignored_secret(python_workspace)
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        "for patch in request['task']['implementation']['patches']:\n"
        "    target = Path(patch['path'])\n"
        "    if patch['operation'] == 'replace':\n"
        "        target.write_text(patch['content'])\n"
        "    else:\n"
        "        target.write_text(target.read_text() + patch['content'])\n"
        "Path('.env').write_text('API_TOKEN=changed-by-the-agent\\n')\n"
        "print(json.dumps({'status': 'PASSED', 'summary': 'done'}))\n",
        encoding="utf-8",
    )
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = "fixture"
    config["agentProviders"] = {"fixture": {"kind": "command", "command": ["python", str(agent)]}}
    config["runtime"]["agentSandbox"] = "off"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, run = start(python_workspace)
    with application._services(python_workspace) as services:
        findings = services.state.list("finding", Finding, execution_id=run.execution_id)
    guarded = [item for item in findings if item.rule_id == "workspace.out-of-changeset-write"]
    assert guarded and ".env" in guarded[0].message
    assert guarded[0].severity.value == "CRITICAL"


def test_gc_removes_orphan_artifacts(python_workspace: Path) -> None:
    application, run = start(python_workspace)
    application.decide_gate(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=run.change_set_digest or "",
        actor_id="human.reviewer",
        rationale="ok",
    )
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        orphan = services.artifacts.put(b"left behind by an interrupted write")
        recent = services.artifacts.put(b"written a moment ago")
        old = time.time() - 7200
        for ref in (orphan,):
            os.utime(services.artifacts.meta_root / f"{ref.digest.split(':')[1]}.json", (old, old))
        plan = RetentionCollector(services).plan(now=datetime.now(UTC))
        assert plan.orphans == [orphan.uri]
        assert recent.uri not in plan.orphans
        assert plan.removed_runs == [] and plan.pruned_runs == {}
        report = plan.as_dict(applied=False)
        assert report["orphansRemoved"] == 1 and report["orphanBytes"] > 0
        RetentionCollector(services).apply(plan)
        assert not services.artifacts.verify(orphan.uri)
        assert services.artifacts.verify(recent.uri)
        # Every artifact of the run is still referenced and verifies.
        later = RetentionCollector(services).plan(now=datetime.now(UTC) + timedelta(hours=2))
        assert later.orphans == [recent.uri]
    finally:
        services.close()
    verified = HarnessApplication().verify(python_workspace, run.execution_id)
    assert verified["valid"], verified
