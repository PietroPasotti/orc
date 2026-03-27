"""Tests for the squad profile loader (orc/squad.py)."""

import textwrap

import pytest

from orc.squad import (
    ReviewThreshold,
    SquadConfig,
    list_squads,
    load_all_squads,
    load_squad,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NEW_YAML = textwrap.dedent("""\
    name: default
    description: |
      Default squad: one agent of each type.
    composition:
      - role: planner
        count: 1
        model: claude-sonnet-4.6
      - role: coder
        count: 1
        model: claude-sonnet-4.6
      - role: qa
        count: 1
        model: claude-sonnet-4.6
    timeout_minutes: 120
""")

_MINIMAL_YAML = textwrap.dedent("""\
    composition:
      - role: planner
        count: 1
      - role: coder
        count: 1
      - role: qa
        count: 1
    timeout_minutes: 120
""")


class TestLoadSquad:
    def test_load_new_list_schema(self, tmp_path):
        """New list format with name/count/model per role is parsed correctly."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "default.yaml").write_text(_NEW_YAML)
        cfg = load_squad("default", orc_dir=tmp_path)
        assert cfg.planner == 1
        assert cfg.coder == 1
        assert cfg.qa == 1
        assert cfg.timeout_minutes == 120
        assert cfg.name == "default"
        assert "one agent" in cfg.description
        assert cfg.model("coder") == "claude-sonnet-4.6"
        assert cfg.model("planner") == "claude-sonnet-4.6"
        assert cfg.model("qa") == "claude-sonnet-4.6"

    def test_load_new_list_schema_different_models(self, tmp_path):
        """Each role can specify a different model in the list format."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "mixed.yaml").write_text(
            textwrap.dedent("""\
                name: mixed
                composition:
                  - role: planner
                    count: 1
                    model: claude-opus-4-5
                  - role: coder
                    count: 2
                    model: claude-sonnet-4.6
                  - role: qa
                    count: 1
                    model: claude-haiku-4-5
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("mixed", orc_dir=tmp_path)
        assert cfg.coder == 2
        assert cfg.qa == 1
        assert cfg.model("planner") == "claude-opus-4-5"
        assert cfg.model("coder") == "claude-sonnet-4.6"
        assert cfg.model("qa") == "claude-haiku-4-5"

    def test_load_broad_profile(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "broad.yaml").write_text(
            textwrap.dedent("""\
                name: broad
                description: Wider parallel configuration.
                composition:
                  - role: planner
                    count: 1
                    model: claude-sonnet-4.6
                  - role: coder
                    count: 4
                    model: claude-sonnet-4.6
                  - role: qa
                    count: 2
                    model: claude-sonnet-4.6
                timeout_minutes: 180
            """)
        )
        cfg = load_squad("broad", orc_dir=tmp_path)
        assert cfg.coder == 4
        assert cfg.qa == 2
        assert cfg.timeout_minutes == 180
        assert cfg.name == "broad"
        assert cfg.description == "Wider parallel configuration."

    def test_name_falls_back_to_file_stem(self, tmp_path):
        """When the YAML has no name: key the file stem is used."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "mypro.yaml").write_text(_MINIMAL_YAML)
        cfg = load_squad("mypro", orc_dir=tmp_path)
        assert cfg.name == "mypro"

    def test_dict_composition_raises(self, tmp_path):
        """Dict-format composition (legacy) raises ValueError."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "bad.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  planner: 1
                  coder: 1
                  qa: 1
                timeout_minutes: 120
            """)
        )
        with pytest.raises(ValueError, match="must be a list"):
            load_squad("bad", orc_dir=tmp_path)

    def test_missing_file_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        with pytest.raises(FileNotFoundError):
            load_squad("nonexistent", orc_dir=tmp_path)

    def test_planner_not_one_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "bad.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 2
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                timeout_minutes: 120
            """)
        )
        with pytest.raises(ValueError, match="planner"):
            load_squad("bad", orc_dir=tmp_path)

    def test_zero_count_raises(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "zero.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 0
                  - role: qa
                    count: 1
                timeout_minutes: 120
            """)
        )
        with pytest.raises(ValueError, match="coder"):
            load_squad("zero", orc_dir=tmp_path)

    def test_defaults_applied(self, tmp_path):
        """timeout_minutes omitted — should default to 120."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "notimeout.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 2
                  - role: qa
                    count: 1
            """)
        )
        cfg = load_squad("notimeout", orc_dir=tmp_path)
        assert cfg.timeout_minutes == 120

    def test_package_fallback(self):
        """load_squad without orc_dir falls back to package bundled squads."""
        cfg = load_squad("default")
        assert cfg.planner == 1
        assert cfg.coder == 1
        assert cfg.qa == 1
        assert cfg.name == "default"
        assert cfg.description != ""
        assert cfg.model("coder") == "gemini-2.5-pro"

    def test_project_overrides_package(self, tmp_path):
        """Project-level squad takes precedence over package bundled squad."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "default.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 3
                  - role: qa
                    count: 2
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("default", orc_dir=tmp_path)
        assert cfg.coder == 3  # overridden, not the package default of 1


class TestSquadConfig:
    def test_count_method(self):
        cfg = SquadConfig(planner=1, coder=4, qa=2, merger=1, timeout_minutes=120)
        assert cfg.count("planner") == 1
        assert cfg.count("coder") == 4
        assert cfg.count("qa") == 2

    def test_count_unknown_role_raises(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        with pytest.raises(ValueError):
            cfg.count("unknown_role")

    def test_model_method_returns_configured_model(self):
        cfg = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            merger=1,
            timeout_minutes=120,
            _models={
                "coder": "claude-opus-4-5",
                "planner": "claude-sonnet-4.6",
                "qa": "claude-haiku-4-5",
            },
        )
        assert cfg.model("coder") == "claude-opus-4-5"
        assert cfg.model("planner") == "claude-sonnet-4.6"
        assert cfg.model("qa") == "claude-haiku-4-5"

    def test_model_method_falls_back_to_default(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        assert cfg.model("coder") == "claude-sonnet-4.6"
        assert cfg.model("planner") == "claude-sonnet-4.6"
        assert cfg.model("qa") == "claude-sonnet-4.6"

    def test_model_unknown_role_raises(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        with pytest.raises(ValueError):
            cfg.model("unknown_role")

    def test_frozen(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        with pytest.raises((TypeError, AttributeError)):
            cfg.coder = 5  # type: ignore[misc]

    def test_name_and_description_defaults(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        assert cfg.name == ""
        assert cfg.description == ""

    def test_name_and_description_set(self):
        cfg = SquadConfig(
            planner=1, coder=1, qa=1, merger=1, timeout_minutes=120, name="x", description="y"
        )
        assert cfg.name == "x"
        assert cfg.description == "y"


class TestListSquads:
    def test_lists_yaml_files(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "default.yaml").write_text(_MINIMAL_YAML)
        (squads_dir / "broad.yaml").write_text(_MINIMAL_YAML)
        names = list_squads(orc_dir=tmp_path)
        assert sorted(names) == ["broad", "default"]

    def test_empty_dir(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        assert list_squads(orc_dir=tmp_path) == []

    def test_no_orc_dir_returns_package_squads(self):
        """Without orc_dir, returns package bundled squads."""
        names = list_squads()
        assert "default" in names


class TestLoadAllSquads:
    def test_returns_all_project_profiles(self, tmp_path):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "default.yaml").write_text(_NEW_YAML)
        (squads_dir / "broad.yaml").write_text(
            textwrap.dedent("""\
                name: broad
                description: Wide configuration.
                composition:
                  - role: planner
                    count: 1
                    model: claude-sonnet-4.6
                  - role: coder
                    count: 4
                    model: claude-sonnet-4.6
                  - role: qa
                    count: 2
                    model: claude-sonnet-4.6
                timeout_minutes: 180
            """)
        )
        profiles = load_all_squads(orc_dir=tmp_path)
        names = {p.name for p in profiles}
        assert "default" in names
        assert "broad" in names

    def test_project_overrides_package_profile(self, tmp_path):
        """Project default.yaml shadows the package bundled one."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "default.yaml").write_text(
            textwrap.dedent("""\
                name: default
                description: Overridden.
                composition:
                  - role: planner
                    count: 1
                    model: claude-sonnet-4.6
                  - role: coder
                    count: 5
                    model: claude-opus-4-5
                  - role: qa
                    count: 3
                    model: claude-sonnet-4.6
                timeout_minutes: 60
            """)
        )
        profiles = load_all_squads(orc_dir=tmp_path)
        default = next(p for p in profiles if p.name == "default")
        assert default.coder == 5  # project value, not package default of 1
        assert default.model("coder") == "claude-opus-4-5"

    def test_package_profiles_included_when_no_project_dir(self):
        profiles = load_all_squads()
        assert any(p.name == "default" for p in profiles)

    def test_no_project_dir(self, tmp_path):
        """No squads dir in project → only package profiles."""
        profiles = load_all_squads(orc_dir=tmp_path)
        assert any(p.name == "default" for p in profiles)


# ---------------------------------------------------------------------------
# squad.py coverage gap tests (from test_coverage.py)
# ---------------------------------------------------------------------------


class TestSquadCoverage:
    def test_parse_squad_file_skips_invalid_composition_entries(self, tmp_path):
        """Lines 127, 130: non-dict and unknown-role entries skipped."""
        from orc.squad import _parse_squad_file

        squad_yaml = tmp_path / "test.yaml"
        squad_yaml.write_text(
            "name: test\n"
            "composition:\n"
            "  - not_a_dict\n"
            "  - role: wizard\n    count: 1\n"
            "  - role: coder\n    count: 2\n"
            "timeout_minutes: 60\n"
        )
        cfg = _parse_squad_file("test", squad_yaml)
        assert cfg.coder == 2

    def test_parse_squad_file_invalid_count(self, tmp_path):
        """count < 1 raises ValueError."""
        import pytest

        from orc.squad import _parse_squad_file

        squad_yaml = tmp_path / "bad.yaml"
        squad_yaml.write_text(
            textwrap.dedent("""\
                name: bad
                composition:
                  - role: planner
                    count: 0
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                timeout_minutes: 60
            """)
        )
        with pytest.raises(ValueError, match="planner"):
            _parse_squad_file("bad", squad_yaml)

    def test_load_all_squads_local_dir(self, tmp_path):
        """Local squads dir is scanned."""
        from orc.squad import load_all_squads

        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "local.yaml").write_text(
            textwrap.dedent("""\
                name: local
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                timeout_minutes: 30
            """)
        )
        profiles = load_all_squads(orc_dir=tmp_path)
        names = [p.name for p in profiles]
        assert "local" in names

    def test_load_all_squads_bad_package_squad_skipped(self, tmp_path, monkeypatch):
        """Lines 235-236: bad package squad silently skipped."""
        import orc.squad as _sq
        from orc.squad import load_all_squads

        bad_file = tmp_path / "broken.yaml"
        bad_file.write_text(": : invalid yaml\n")

        class FakeDir:
            def glob(self, pattern):
                return [bad_file]

        monkeypatch.setattr(_sq, "_PACKAGE_SQUADS_DIR", FakeDir())
        profiles = load_all_squads(orc_dir=tmp_path / "nonexistent")
        assert isinstance(profiles, list)

    def test_list_squads_with_orc_dir(self, tmp_path):
        """Line 253: list_squads uses orc_dir squads subdir."""
        from orc.squad import list_squads

        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "alpha.yaml").write_text("")
        result = list_squads(orc_dir=tmp_path)
        assert "alpha" in result

    def test_parse_squad_file_timeout_too_low(self, tmp_path):
        """Line 164: timeout_minutes < 1 raises ValueError."""
        from orc.squad import _parse_squad_file

        squad_yaml = tmp_path / "fast.yaml"
        squad_yaml.write_text(
            "name: fast\ncomposition:\n  - role: coder\n    count: 1\n"
            "  - role: qa\n    count: 1\ntimeout_minutes: 0\n"
        )
        with pytest.raises(ValueError, match="timeout_minutes"):
            _parse_squad_file("fast", squad_yaml)

    def test_load_all_squads_bad_project_squad_skipped(self, tmp_path):
        """Lines 227-228: except block in project-dir scan swallows bad yaml."""
        from orc.squad import load_all_squads

        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "broken.yaml").write_text(": : invalid yaml\n")
        # Should not raise; bad file is silently skipped
        profiles = load_all_squads(orc_dir=tmp_path)
        assert isinstance(profiles, list)

    def test_list_squads_no_squads_subdir(self, tmp_path):
        """Line 253: list_squads returns [] when orc_dir/squads/ doesn't exist."""
        from orc.squad import list_squads

        result = list_squads(orc_dir=tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# ReviewThreshold
# ---------------------------------------------------------------------------

_QA_THRESHOLD_YAML = textwrap.dedent("""\
    name: strict
    composition:
      - role: planner
        count: 1
      - role: coder
        count: 1
      - role: qa
        count: 1
        review-threshold: HIGH
    timeout_minutes: 60
""")


class TestReviewThreshold:
    def test_default_review_threshold(self):
        """SquadConfig defaults to LOW (strictest) review threshold."""
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        assert cfg.review_threshold == ReviewThreshold.LOW

    def test_review_threshold_from_yaml(self, tmp_path):
        """review-threshold on QA role entry is parsed correctly."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "strict.yaml").write_text(_QA_THRESHOLD_YAML)
        cfg = load_squad("strict", orc_dir=tmp_path)
        assert cfg.review_threshold == ReviewThreshold.HIGH

    @pytest.mark.parametrize("value", ["CRITICAL", "HIGH", "MID", "LOW"])
    def test_review_threshold_all_valid_values(self, tmp_path, value):
        """All four threshold levels are accepted."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "t.yaml").write_text(
            textwrap.dedent(f"""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                    review-threshold: {value}
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("t", orc_dir=tmp_path)
        assert cfg.review_threshold == ReviewThreshold(value)

    def test_review_threshold_case_insensitive(self, tmp_path):
        """review-threshold accepts case-insensitive values."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "ci.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                    review-threshold: high
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("ci", orc_dir=tmp_path)
        assert cfg.review_threshold == ReviewThreshold.HIGH

    def test_review_threshold_invalid_raises(self, tmp_path):
        """Invalid review-threshold value raises ValueError."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "bad.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                    review-threshold: EXTREME
                timeout_minutes: 60
            """)
        )
        with pytest.raises(ValueError, match="review-threshold"):
            load_squad("bad", orc_dir=tmp_path)

    def test_review_threshold_omitted_defaults_to_low(self, tmp_path):
        """Omitting review-threshold gives the default LOW."""
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "plain.yaml").write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                  - role: qa
                    count: 1
                timeout_minutes: 60
            """)
        )
        cfg = load_squad("plain", orc_dir=tmp_path)
        assert cfg.review_threshold == ReviewThreshold.LOW

    def test_review_threshold_on_non_qa_role_ignored(self, tmp_path):
        """review-threshold on non-QA roles is silently ignored."""
        from orc.squad import _parse_squad_file

        squad_yaml = tmp_path / "test.yaml"
        squad_yaml.write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                    review-threshold: CRITICAL
                  - role: coder
                    count: 1
                    review-threshold: HIGH
                  - role: qa
                    count: 1
                timeout_minutes: 60
            """)
        )
        cfg = _parse_squad_file("test", squad_yaml)
        assert cfg.review_threshold == ReviewThreshold.LOW

    def test_package_default_has_low_threshold(self):
        """Package bundled default squad has LOW review threshold."""
        cfg = load_squad("default")
        assert cfg.review_threshold == ReviewThreshold.LOW
