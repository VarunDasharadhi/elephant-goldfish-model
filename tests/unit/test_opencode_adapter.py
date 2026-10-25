# Copyright 2026 Google LLC.
# SPDX-License-Identifier: Apache-2.0

"""Offline checks for the experimental OpenCode V1 installer."""

import hashlib
import json
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = runpy.run_path(str(ROOT / "adapters/opencode/install.py"))


def test_install_preserves_resources_and_native_contract(tmp_path: Path) -> None:
    destination = tmp_path / "EGM candidate ü"
    config_path = ADAPTER["install"](destination)
    config = json.loads(config_path.read_text())
    identity = json.loads((destination / "installation.json").read_text())
    canonical = json.loads((ROOT / "plugin.json").read_text())
    assert (identity["name"], identity["version"]) == (canonical["name"], canonical["version"])
    assert identity["host_version"] == "1.18.29"
    for relative, digest in identity["sha256"].items():
        installed = destination / relative
        source = ROOT / installed.relative_to(destination / "bundle")
        assert installed.read_bytes() == source.read_bytes()
        assert hashlib.sha256(installed.read_bytes()).hexdigest() == digest
    for directory in ("skills", "agents", "resources", "docs"):
        for source in (ROOT / directory).rglob("*"):
            if source.is_file():
                assert f"bundle/{source.relative_to(ROOT)}" in identity["sha256"]

    assert config["default_agent"] == "egm"
    assert config["autoupdate"] is False
    assert "model" not in config
    assert len(config["command"]) == 7
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        key = ADAPTER["name"](skill)
        alias = Path(config["skills"]["paths"][0]) / key
        assert alias.resolve() == destination / "bundle/skills" / skill.parent.name
        assert (alias / "SKILL.md").read_bytes() == skill.read_bytes()
        command = config["command"][key]
        assert command["agent"] == "egm"
        assert str(alias.resolve() / "SKILL.md") in command["template"]
        assert "$ARGUMENTS" in command["template"]

    assert len(config["agent"]) == 8
    for filename in ADAPTER["REVIEWERS"]:
        persona = destination / "bundle/agents" / f"{filename}.md"
        agent = config["agent"][ADAPTER["name"](persona)]
        assert agent["mode"] == "subagent"
        assert agent["permission"] == {
            "*": "deny", "read": {"*": "allow", "mcp:*": "deny"},
            "glob": "allow", "grep": "allow",
            "external_directory": {"*": "deny", f"{destination}/bundle/**": "allow"},
        }
        # V1 maps resource reads and both list tools to read. Last match wins.
        assert list(agent["permission"]["read"].items()) == [
            ("*", "allow"), ("mcp:*", "deny"),
        ]
        assert str(persona) in agent["prompt"]
        assert "Read-only Protocol" not in agent["prompt"]
    primary = config["agent"]["egm"]
    assert primary["permission"]["*"] == "ask"
    assert set(primary["permission"]["task"]) == {"*", *set(config["agent"]) - {"egm"}}
    assert "Omit task_id" in primary["prompt"]
    assert "foreground completion" in primary["prompt"]


def test_stage_required_reports_preserve_context_boundary(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    config = json.loads(ADAPTER["install"](destination).read_text())
    bundle = destination / "bundle"
    canonical = (bundle / "skills/egm_review/SKILL.md").read_text()
    assert "include `zoom_out_report.md` path in" in canonical
    guide = " ".join((bundle / "docs/opencode.md").read_text().split())
    prompt = config["agent"]["egm-zoom-out"]["prompt"]
    for instructions in (prompt, guide):
        assert (
            "Read prior reports only when explicitly supplied and required by the canonical stage."
        ) in instructions
        assert "Do not retrieve unsolicited history, memory, or earlier reviewer findings." in instructions
    assert "Do not pass parent history, private reasoning, or memory." in guide


def test_existing_destination_is_untouched(tmp_path: Path) -> None:
    sentinel = tmp_path / "existing.txt"
    sentinel.write_text("other worker")
    with pytest.raises(FileExistsError):
        ADAPTER["install"](tmp_path)
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text() == "other worker"


@pytest.mark.parametrize("suffix", ["bad*", "bad?", "bad[glob]", "{env:HOME}", "bad\npath"])
def test_template_paths_are_rejected(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError):
        ADAPTER["install"](tmp_path / suffix)
    assert not list(tmp_path.iterdir())


def test_source_destination_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the source"):
        ADAPTER["install"](ROOT / "unused-opencode-test-destination")


def test_command_interpolation_paths_are_rejected(tmp_path: Path) -> None:
    # V1 replaces positional placeholders and $ARGUMENTS across the template.
    for component in ("$ARGUMENTS", "$1"):
        with pytest.raises(ValueError, match="template or glob"):
            ADAPTER["install"](tmp_path / component / "candidate")
    assert not list(tmp_path.iterdir())


def test_symlink_parent_cannot_bypass_path_validation(tmp_path: Path) -> None:
    target = tmp_path / "unsafe*"
    target.mkdir()
    alias = tmp_path / "safe"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        ADAPTER["install"](alias / "candidate")
    assert not list(target.iterdir())


def test_dangling_destination_is_untouched(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    destination.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        ADAPTER["install"](destination)
    assert destination.is_symlink()
    assert not (tmp_path / "absent").exists()
