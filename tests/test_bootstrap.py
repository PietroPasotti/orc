"""Tests for orc/cli/bootstrap.py."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import orc.cli.bootstrap as _boot
import orc.main as m

runner = CliRunner()

# Input that accepts all bootstrap prompts with their defaults (one \n per field).
_ACCEPT_DEFAULTS = "\n\n"


def _cache_dir(project_root: Path, orc_cache_root: Path, orc_dir: str = ".orc") -> Path:
    """Return the project cache dir by reading project-id from config.yaml."""
    cfg = yaml.safe_load((project_root / orc_dir / "config.yaml").read_text()) or {}
    return orc_cache_root / str(cfg["project-id"])


@pytest.fixture(autouse=True)
def patch_cache_root(tmp_path, monkeypatch):
    """Redirect _orc_cache_root to a temp dir so tests don't pollute ~/.cache."""
    cache_root = tmp_path / "orc_cache"
    monkeypatch.setattr(_boot, "_orc_cache_root", lambda: cache_root)


# ---------------------------------------------------------------------------
# bootstrap command (integration-level tests via CLI)
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        assert result.exit_code == 0
        assert (tmp_path / ".orc" / "roles").is_dir()
        assert (tmp_path / ".orc" / "squads").is_dir()
        cache = _cache_dir(tmp_path, tmp_path / "orc_cache")
        assert (cache / "vision").is_dir()
        assert (cache / "work").is_dir()

    def test_copies_bundled_roles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        for role in ("planner", "coder", "qa"):
            role_dir = tmp_path / ".orc" / "roles" / role
            assert role_dir.is_dir(), f"Missing {role}/ directory"
            main_file = role_dir / "_main.md"
            assert main_file.exists(), f"Missing {role}/_main.md"
            assert len(main_file.read_text()) > 100

    def test_copies_default_squad(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
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
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        readme = _cache_dir(tmp_path, tmp_path / "orc_cache") / "vision" / "README.md"
        assert readme.exists()
        assert "vision" in readme.read_text().lower()

    def test_creates_orc_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        readme = tmp_path / ".orc" / "README.md"
        assert readme.exists()
        assert len(readme.read_text()) > 0

    def test_creates_work_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        readme = _cache_dir(tmp_path, tmp_path / "orc_cache") / "work" / "README.md"
        assert readme.exists()
        assert len(readme.read_text()) > 0

    def test_creates_empty_board(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        board = _cache_dir(tmp_path, tmp_path / "orc_cache") / "work" / "board.yaml"
        assert board.exists()
        data = yaml.safe_load(board.read_text())
        assert data["counter"] == 1
        assert data["open"] == []
        assert data["done"] == []

    def test_creates_justfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        justfile = tmp_path / ".orc" / "justfile"
        assert justfile.exists()
        content = justfile.read_text()
        assert "orc run" in content
        assert "orc status" in content
        assert "orc merge" in content

    def test_creates_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        env_example = tmp_path / ".env.example"
        assert env_example.exists()
        assert "COLONY_TELEGRAM_TOKEN" in env_example.read_text()

    def test_skips_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        sentinel = "# my custom justfile"
        (tmp_path / ".orc" / "justfile").write_text(sentinel)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        assert (tmp_path / ".orc" / "justfile").read_text() == sentinel

    def test_force_overwrites_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        (tmp_path / ".orc" / "justfile").write_text("# custom")
        runner.invoke(m.app, ["bootstrap", "--force"], input=_ACCEPT_DEFAULTS)
        assert "orc run" in (tmp_path / ".orc" / "justfile").read_text()

    def test_output_reports_created_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        assert "Bootstrapped" in result.output
        assert "justfile" in result.output
        assert "Next steps" in result.output

    def test_output_reports_skipped_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        result = runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        assert "Skipped" in result.output

    def test_output_skipped_absolute_path_outside_project(
        self, tmp_path, tmp_path_factory, monkeypatch
    ):
        """Skipped paths outside the project root (e.g. cache) print absolute paths."""
        monkeypatch.chdir(tmp_path)
        # Put the cache root outside tmp_path so relative_to() raises ValueError
        outside_cache = tmp_path_factory.mktemp("outside_cache")
        monkeypatch.setattr(_boot, "_orc_cache_root", lambda: outside_cache)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        result = runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        assert "Skipped" in result.output
        assert str(outside_cache) in result.output


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


class TestBootstrapInteractivePrompts:
    def test_project_id_defaults_to_dir_name(self, tmp_path, monkeypatch):
        """project-id defaults to the name of the current working directory."""
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        cfg = yaml.safe_load((project_dir / ".orc" / "config.yaml").read_text()) or {}
        assert cfg["project-id"] == "my-project"

    def test_custom_project_id_written_to_config(self, tmp_path, monkeypatch):
        """A custom project-id entered at the prompt is persisted in config.yaml."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input="my-custom-id\n\n")
        cfg = yaml.safe_load((tmp_path / ".orc" / "config.yaml").read_text()) or {}
        assert cfg["project-id"] == "my-custom-id"

    def test_existing_project_id_used_as_default(self, tmp_path, monkeypatch):
        """A project-id already in config.yaml is offered as the prompt default."""
        monkeypatch.chdir(tmp_path)
        # First bootstrap sets the project-id
        runner.invoke(m.app, ["bootstrap"], input="first-id\n\n")
        # Second bootstrap should offer "first-id" as default (accept it)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        cfg = yaml.safe_load((tmp_path / ".orc" / "config.yaml").read_text()) or {}
        assert cfg["project-id"] == "first-id"

    def test_custom_orc_dir_places_files_in_chosen_directory(self, tmp_path, monkeypatch):
        """Entering a custom orc-dir places the config directory at that path."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input="\n.myorc\n")
        assert (tmp_path / ".myorc" / "roles").is_dir()
        assert (tmp_path / ".myorc" / "config.yaml").exists()
        # The roles dir should be in .myorc, NOT in the default .orc
        assert not (tmp_path / ".orc" / "roles").is_dir()

    def test_orc_dir_format_substitution_with_project_id(self, tmp_path, monkeypatch):
        """'{project_id}' in orc-dir is replaced with the entered project-id."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input="myapp\n.{project_id}\n")
        assert (tmp_path / ".myapp" / "roles").is_dir()
        assert (tmp_path / ".myapp" / "config.yaml").exists()

    def test_project_id_cache_dir_uses_prompted_id(self, tmp_path, monkeypatch):
        """The project cache is created under the prompted project-id."""
        monkeypatch.chdir(tmp_path)
        cache_root = tmp_path / "orc_cache"
        monkeypatch.setattr(_boot, "_orc_cache_root", lambda: cache_root)
        runner.invoke(m.app, ["bootstrap"], input="proj-abc\n\n")
        assert (cache_root / "proj-abc" / "vision").is_dir()
        assert (cache_root / "proj-abc" / "work").is_dir()

    def test_orc_dir_unresolvable_format_marker_used_literally(self, tmp_path, monkeypatch):
        """An orc-dir value with an unknown format key is kept as a literal string."""
        monkeypatch.chdir(tmp_path)
        # {unknown} is not a collected field, so format_map raises KeyError → kept literal
        runner.invoke(m.app, ["bootstrap"], input="myapp\n.{unknown_key}\n")
        assert (tmp_path / ".{unknown_key}" / "roles").is_dir()

    def test_prompt_default_with_unresolvable_format_marker_kept_literal(
        self, tmp_path, monkeypatch
    ):
        """A default value with an unknown format key is kept as the literal default."""
        # Monkeypatch BOOTSTRAP_FIELDS to add a field whose default contains a
        # bad format marker, exercising the except branch on the default path.

        bad_field = _boot._BootstrapField(
            key="orc-dir",
            prompt="Orc config directory",
            default=lambda cfg, root: ".{bad_key}",  # always contains bad marker
            applies_format=True,
            save_to_config=False,
        )
        monkeypatch.setattr(_boot, "BOOTSTRAP_FIELDS", (_boot.BOOTSTRAP_FIELDS[0], bad_field))
        monkeypatch.chdir(tmp_path)
        # Accept defaults: project-id stays as dir name, orc-dir stays as ".{bad_key}"
        runner.invoke(m.app, ["bootstrap"], input="\n\n")
        assert (tmp_path / ".{bad_key}" / "roles").is_dir()


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
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
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
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        cache = _cache_dir(tmp_path, tmp_path / "orc_cache")
        vision_doc = cache / "vision" / "my-feature.md"
        vision_doc.write_text("# vision")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert vision_doc.exists()
        assert vision_doc.read_text() == "# vision"

    def test_upgrade_preserves_work(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        cache = _cache_dir(tmp_path, tmp_path / "orc_cache")
        board = cache / "work" / "board.yaml"
        board.write_text("counter: 5\nopen: []\ndone: []\n")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert board.read_text() == "counter: 5\nopen: []\ndone: []\n"

    def test_upgrade_preserves_changelog(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        changelog = tmp_path / ".orc" / "orc-CHANGELOG.md"
        changelog.write_text("## my project history\n")
        runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert changelog.read_text() == "## my project history\n"

    def test_upgrade_prompts_for_confirmation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        result = runner.invoke(m.app, ["bootstrap", "--upgrade"], input="n\n")
        assert result.exit_code != 0

    def test_upgrade_yes_skips_prompt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert result.exit_code == 0
        assert "Upgrade complete" in result.output

    def test_upgrade_reports_updated_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert "Upgraded" in result.output
        assert "justfile" in result.output

    def test_upgrade_reports_preserved_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"], input=_ACCEPT_DEFAULTS)
        changelog = tmp_path / ".orc" / "orc-CHANGELOG.md"
        changelog.write_text("# history\n")
        result = runner.invoke(m.app, ["bootstrap", "--upgrade", "--yes"])
        assert "Preserved" in result.output
        assert "orc-CHANGELOG.md" in result.output
