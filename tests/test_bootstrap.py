"""Tests for orc/cli/bootstrap.py."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

import orc.main as m

runner = CliRunner()


# ---------------------------------------------------------------------------
# bootstrap command (integration-level tests via CLI)
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert result.exit_code == 0
        for subdir in ("roles", "squads", "vision", "work"):
            assert (tmp_path / ".orc" / subdir).is_dir()

    def test_copies_bundled_roles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        for role in ("planner", "coder", "qa"):
            role_dir = tmp_path / ".orc" / "roles" / role
            assert role_dir.is_dir(), f"Missing {role}/ directory"
            main_file = role_dir / "_main.md"
            assert main_file.exists(), f"Missing {role}/_main.md"
            assert len(main_file.read_text()) > 100

    def test_copies_default_squad(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        squad_file = tmp_path / ".orc" / "squads" / "default.yaml"
        assert squad_file.exists()
        cfg = yaml.safe_load(squad_file.read_text())
        composition = cfg.get("composition") or cfg
        if isinstance(composition, list):
            roles = {e["role"]: e["count"] for e in composition}
            assert roles["planner"] == 1
            assert roles["coder"] == 1
        else:
            assert composition["planner"] == 1
            assert composition["coder"] == 1

    def test_creates_vision_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        readme = tmp_path / ".orc" / "vision" / "README.md"
        assert readme.exists()
        assert "vision" in readme.read_text().lower()

    def test_creates_orc_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        readme = tmp_path / ".orc" / "README.md"
        assert readme.exists()
        assert len(readme.read_text()) > 0

    def test_creates_work_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        readme = tmp_path / ".orc" / "work" / "README.md"
        assert readme.exists()
        assert len(readme.read_text()) > 0

    def test_creates_empty_board(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        board = tmp_path / ".orc" / "work" / "board.yaml"
        assert board.exists()
        data = yaml.safe_load(board.read_text())
        assert data["counter"] == 1
        assert data["open"] == []
        assert data["done"] == []

    def test_creates_justfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        justfile = tmp_path / ".orc" / "justfile"
        assert justfile.exists()
        content = justfile.read_text()
        assert "orc run" in content
        assert "orc status" in content
        assert "orc merge" in content

    def test_creates_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        env_example = tmp_path / ".env.example"
        assert env_example.exists()
        assert "COLONY_TELEGRAM_TOKEN" in env_example.read_text()

    def test_skips_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        sentinel = "# my custom justfile"
        (tmp_path / ".orc" / "justfile").write_text(sentinel)
        runner.invoke(m.app, ["bootstrap"])
        assert (tmp_path / ".orc" / "justfile").read_text() == sentinel

    def test_force_overwrites_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        (tmp_path / ".orc" / "justfile").write_text("# custom")
        runner.invoke(m.app, ["bootstrap", "--force"])
        assert "orc run" in (tmp_path / ".orc" / "justfile").read_text()

    def test_output_reports_created_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Bootstrapped" in result.output
        assert "justfile" in result.output
        assert "Next steps" in result.output

    def test_output_reports_skipped_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Skipped" in result.output


# ---------------------------------------------------------------------------
# bootstrap --upgrade
# ---------------------------------------------------------------------------


class TestBootstrapUpgrade:
    def test_upgrade_fails_without_orc_dir(self, tmp_path, monkeypatch):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir(exist_ok=True)
        monkeypatch.chdir(empty_dir)
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert result.exit_code != 0
        assert ".orc/" in result.output

    def test_upgrade_overwrites_roles_and_squads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        # Mutate a role file and a squad file
        role_file = tmp_path / ".orc" / "roles" / "coder" / "_main.md"
        squad_file = tmp_path / ".orc" / "squads" / "default.yaml"
        role_file.write_text("# custom coder")
        squad_file.write_text("custom: true")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert role_file.read_text() != "# custom coder"
        assert squad_file.read_text() != "custom: true"

    def test_upgrade_preserves_vision(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        vision_doc = tmp_path / ".orc" / "vision" / "my-feature.md"
        vision_doc.write_text("# vision")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert vision_doc.exists()
        assert vision_doc.read_text() == "# vision"

    def test_upgrade_preserves_work(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        board = tmp_path / ".orc" / "work" / "board.yaml"
        board.write_text("counter: 5\nopen: []\ndone: []\n")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert board.read_text() == "counter: 5\nopen: []\ndone: []\n"

    def test_upgrade_preserves_changelog(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        changelog = tmp_path / ".orc" / "orc-CHANGELOG.md"
        changelog.write_text("## my project history\n")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert changelog.read_text() == "## my project history\n"

    def test_upgrade_prompts_for_confirmation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap", "--upgrade"], input="n\n")
        assert result.exit_code != 0

    def test_upgrade_yes_skips_prompt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert result.exit_code == 0
        assert "Upgrade complete" in result.output

    def test_upgrade_reports_updated_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert "Upgraded" in result.output
        assert "justfile" in result.output

    def test_upgrade_reports_preserved_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert "Preserved" in result.output
        assert "vision" in result.output
        assert "work" in result.output


# ---------------------------------------------------------------------------
# bootstrap – git commit of board.yaml
# ---------------------------------------------------------------------------


class TestBootstrapCommitsBoardYaml:
    def test_bootstrap_commits_board_yaml_to_main(self, tmp_path, monkeypatch):
        """git add + git commit are called when board.yaml is newly created."""
        import subprocess
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)

        def fake_run(cmd, **kwargs):
            result = subprocess.CompletedProcess(cmd, 0, b"", b"")
            if cmd[:3] == ["git", "ls-files", "--error-unmatch"]:
                result.returncode = 1  # not tracked
            return result

        with patch("orc.cli.bootstrap.subprocess.run", side_effect=fake_run) as mock_run:
            result = runner.invoke(m.app, ["bootstrap"])

        assert result.exit_code == 0

        calls = [c.args[0] for c in mock_run.call_args_list]
        board_rel = str(Path(".orc") / "work" / "board.yaml")

        assert any(c[:2] == ["git", "add"] and board_rel in c for c in calls)
        assert any(c[:2] == ["git", "commit"] for c in calls)

    def test_bootstrap_skips_commit_when_already_tracked(self, tmp_path, monkeypatch):
        """No git commit is issued when board.yaml is already tracked."""
        import subprocess
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)

        def fake_run(cmd, **kwargs):
            # ls-files returns 0 → already tracked
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch("orc.cli.bootstrap.subprocess.run", side_effect=fake_run) as mock_run:
            result = runner.invoke(m.app, ["bootstrap"])

        assert result.exit_code == 0
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert not any(c[:2] == ["git", "commit"] for c in calls)

    def test_bootstrap_commit_failure_is_non_fatal(self, tmp_path, monkeypatch):
        """Bootstrap exits 0 even when the git commit subprocess fails."""
        import subprocess
        from unittest.mock import patch

        monkeypatch.chdir(tmp_path)

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["git", "ls-files", "--error-unmatch"]:
                return subprocess.CompletedProcess(cmd, 1, b"", b"")  # not tracked
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0, b"", b"")  # add ok
            if cmd[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(cmd, 1, b"", b"error")  # commit fails
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch("orc.cli.bootstrap.subprocess.run", side_effect=fake_run):
            result = runner.invoke(m.app, ["bootstrap"])

        assert result.exit_code == 0
