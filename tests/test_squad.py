"""Tests for the squad profile loader (orc/squad.py)."""

import textwrap

import pytest
from orc.squad import SquadConfig, list_squads, load_squad


class TestLoadSquad:
    def test_load_default(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "default.yaml").write_text(
            textwrap.dedent("""\
                planner: 1
                coder: 1
                qa: 1
                timeout_minutes: 120
            """)
        )
        cfg = load_squad("default", agents_dir=tmp_path)
        assert cfg.planner == 1
        assert cfg.coder == 1
        assert cfg.qa == 1
        assert cfg.timeout_minutes == 120

    def test_load_broad_profile(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "broad.yaml").write_text(
            textwrap.dedent("""\
                planner: 1
                coder: 4
                qa: 2
                timeout_minutes: 180
            """)
        )
        cfg = load_squad("broad", agents_dir=tmp_path)
        assert cfg.coder == 4
        assert cfg.qa == 2
        assert cfg.timeout_minutes == 180

    def test_missing_file_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_squad("nonexistent", agents_dir=tmp_path)

    def test_planner_not_one_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "bad.yaml").write_text(
            textwrap.dedent("""\
                planner: 2
                coder: 1
                qa: 1
                timeout_minutes: 120
            """)
        )
        with pytest.raises(ValueError, match="planner"):
            load_squad("bad", agents_dir=tmp_path)

    def test_zero_count_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "zero.yaml").write_text(
            textwrap.dedent("""\
                planner: 1
                coder: 0
                qa: 1
                timeout_minutes: 120
            """)
        )
        with pytest.raises(ValueError, match="coder"):
            load_squad("zero", agents_dir=tmp_path)

    def test_defaults_applied(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        # timeout_minutes omitted — should default to 120
        (squads_dir / "notimeout.yaml").write_text(
            textwrap.dedent("""\
                planner: 1
                coder: 2
                qa: 1
            """)
        )
        cfg = load_squad("notimeout", agents_dir=tmp_path)
        assert cfg.timeout_minutes == 120

    def test_package_fallback(self):
        """load_squad without agents_dir falls back to package bundled squads."""
        cfg = load_squad("default")
        assert cfg.planner == 1
        assert cfg.coder == 1
        assert cfg.qa == 1

    def test_project_overrides_package(self, tmp_path):
        """Project-level squad takes precedence over package bundled squad."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "default.yaml").write_text(
            textwrap.dedent("""\
                planner: 1
                coder: 3
                qa: 2
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("default", agents_dir=tmp_path)
        assert cfg.coder == 3  # overridden, not the package default of 1


class TestSquadConfig:
    def test_count_method(self):
        cfg = SquadConfig(planner=1, coder=4, qa=2, timeout_minutes=120)
        assert cfg.count("planner") == 1
        assert cfg.count("coder") == 4
        assert cfg.count("qa") == 2

    def test_count_unknown_role_raises(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, timeout_minutes=120)
        with pytest.raises(ValueError):
            cfg.count("unknown_role")

    def test_frozen(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, timeout_minutes=120)
        with pytest.raises((TypeError, AttributeError)):
            cfg.coder = 5  # type: ignore[misc]


class TestListSquads:
    def test_lists_yaml_files(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        (squads_dir / "default.yaml").write_text("planner: 1\ncoder: 1\nqa: 1\n")
        (squads_dir / "broad.yaml").write_text("planner: 1\ncoder: 4\nqa: 2\n")
        names = list_squads(agents_dir=tmp_path)
        assert sorted(names) == ["broad", "default"]

    def test_empty_dir(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir()
        assert list_squads(agents_dir=tmp_path) == []

    def test_no_agents_dir_returns_package_squads(self):
        """Without agents_dir, returns package bundled squads."""
        names = list_squads()
        assert "default" in names
