from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from governed_harness.evidence.hashing import sha256_bytes
from governed_harness.runtime.sandbox import (
    SandboxHost,
    SandboxPath,
    SandboxUnavailable,
    build_sandbox,
    denied_writes,
    resolve_write_paths,
    seatbelt_profile,
)


def _host(tmp_path: Path, system: str = "Darwin", **tools: str | None) -> SandboxHost:
    home = tmp_path / "home"
    temp = tmp_path / "temp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    return SandboxHost(system=system, home=home, temp_dir=temp, **tools)


def _real(path: Path) -> str:
    return os.path.realpath(path)


# ----- write paths and profiles ---------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_write_paths_expand_home_resolve_links_and_always_include_workspace_and_temp(
    tmp_path: Path,
) -> None:
    host = _host(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "real-cache"
    target.mkdir()
    (host.home / "link-cache").symlink_to(target)
    paths = resolve_write_paths(
        workspace, ["~/link-cache", "~/.tool.json*", "~/.claude", str(workspace)], host
    )
    assert paths == (
        SandboxPath(_real(workspace), "subpath", "workspace"),
        SandboxPath(_real(host.temp_dir), "subpath", "tempdir"),
        SandboxPath(_real(target), "subpath", "~/link-cache"),
        SandboxPath(str(Path(_real(host.home)) / ".tool.json"), "prefix", "~/.tool.json*"),
        SandboxPath(_real(host.home / ".claude"), "subpath", "~/.claude"),
    )


def test_seatbelt_profile_denies_writes_and_reopens_only_the_allowed_paths() -> None:
    profile = seatbelt_profile(
        (
            SandboxPath('/work/space "x"', "subpath", "workspace"),
            SandboxPath("/home/u/.claude.json", "prefix", "~/.claude.json*"),
        )
    )
    lines = profile.splitlines()
    assert lines[:3] == ["(version 1)", "(allow default)", "(deny file-write*)"]
    assert '(subpath "/work/space \\"x\\"")' in profile
    assert '(regex #"^/home/u/\\.claude\\.json")' in profile
    assert '(literal "/dev/null")' in profile
    assert '(regex #"^/dev/tty")' in profile
    assert "network" not in profile and "process" not in profile


def test_macos_plan_wraps_with_sandbox_exec_and_digests_the_profile(tmp_path: Path) -> None:
    host = _host(tmp_path, sandbox_exec="/usr/bin/sandbox-exec")
    plan = build_sandbox(tmp_path, ["~/.claude"], host)
    assert plan.mechanism == "sandbox-exec"
    assert plan.prefix == ("/usr/bin/sandbox-exec", "-p", plan.profile)
    assert plan.profile_digest == sha256_bytes(plan.profile.encode())
    record = plan.evidence()
    assert record["mechanism"] == "sandbox-exec" and record["platform"] == "Darwin"
    assert record["profileDigest"] == plan.profile_digest
    assert [item["source"] for item in record["allowedPaths"]] == [  # type: ignore[union-attr]
        "workspace",
        "tempdir",
        "~/.claude",
    ]


def test_linux_plan_binds_root_read_only_and_existing_paths_writable(tmp_path: Path) -> None:
    host = _host(tmp_path, system="Linux", bwrap="/usr/bin/bwrap")
    (host.home / ".tool.json").write_text("{}")
    (host.home / ".tool.json.lock").write_text("")
    plan = build_sandbox(tmp_path, ["~/.tool.json*", "~/.missing"], host)
    assert plan.mechanism == "bwrap"
    assert plan.prefix[:9] == (
        "/usr/bin/bwrap",
        "--ro-bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        "--die-with-parent",
        "--bind",
    )
    assert plan.prefix[-1] == "--"
    assert json.loads(plan.profile) == list(plan.prefix[1:-1])
    home = Path(_real(host.home))
    assert [item.path for item in plan.allowed_paths] == [
        _real(tmp_path),
        _real(host.temp_dir),
        str(home / ".tool.json"),
        str(home / ".tool.json.lock"),
    ]
    assert [item.path for item in plan.skipped_paths] == [str(home / ".missing")]


@pytest.mark.parametrize(
    ("system", "tools", "reason"),
    [
        ("Linux", {}, r"bwrap \(bubblewrap\) is not installed"),
        ("Darwin", {}, "/usr/bin/sandbox-exec is not available"),
        ("Windows", {}, "no agent sandbox mechanism is supported on Windows"),
        ("Windows", {"bwrap": "bwrap", "sandbox_exec": "x"}, "supported on Windows"),
    ],
)
def test_no_mechanism_means_unavailable(
    tmp_path: Path, system: str, tools: dict[str, str], reason: str
) -> None:
    with pytest.raises(SandboxUnavailable, match=reason):
        build_sandbox(tmp_path, [], _host(tmp_path, system=system, **tools))


def test_detect_reports_the_mechanism_of_this_host() -> None:
    host = SandboxHost.detect()
    assert host.home == Path.home()
    if sys.platform == "darwin":
        assert host.system == "Darwin"
        expected = "/usr/bin/sandbox-exec" if Path("/usr/bin/sandbox-exec").exists() else None
        assert host.sandbox_exec == expected
    else:
        assert host.sandbox_exec is None


# ----- denied writes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "paths"),
    [
        (b"/bin/sh: /Users/u/out.txt: Operation not permitted\n", ("/Users/u/out.txt",)),
        (
            b"PermissionError: [Errno 1] Operation not permitted: '/home/u/x.txt'\n",
            ("/home/u/x.txt",),
        ),
        (
            b"Error: EPERM: operation not permitted, open '/Users/u/a.txt.tmp.1'\n",
            ("/Users/u/a.txt.tmp.1",),
        ),
        (b"touch: cannot touch '/srv/x': Read-only file system\n", ("/srv/x",)),
        (b"Sandbox: sh(1) deny(1) file-write-create /Users/u/y\n", ("/Users/u/y",)),
        (b"blocked by the sandbox\n", ()),
    ],
)
def test_denied_writes_name_the_path(stderr: bytes, paths: tuple[str, ...]) -> None:
    assert denied_writes(stderr) == (True, paths)


def test_other_failures_are_not_denied_writes() -> None:
    assert denied_writes(b"Traceback: ValueError: bad input /tmp/x\n") == (False, ())
