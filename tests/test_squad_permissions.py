"""Tests for squad permission config parsing, resolution, and merging."""

from __future__ import annotations

import textwrap

import pytest

from orc.squad import (
    _ORC_DEFAULT_ALLOW_TOOLS,
    PermissionConfig,
    SquadConfig,
    _merge_permissions,
    _parse_permission_block,
    _parse_squad_file,
    load_squad,
)


class TestPermissionConfig:
    def test_defaults(self):
        p = PermissionConfig()
        assert p.mode == "confined"
        assert p.allow_tools == ()
        assert p.deny_tools == ()
        assert p.is_yolo is False

    def test_yolo_mode(self):
        p = PermissionConfig(mode="yolo")
        assert p.is_yolo is True

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="confined.*yolo"):
            PermissionConfig(mode="turbo")

    def test_frozen(self):
        p = PermissionConfig()
        with pytest.raises((TypeError, AttributeError)):
            p.mode = "yolo"  # type: ignore[misc]

    def test_allow_and_deny_tools(self):
        p = PermissionConfig(
            mode="confined",
            allow_tools=("read", "write"),
            deny_tools=("shell(git push:*)",),
        )
        assert "read" in p.allow_tools
        assert "write" in p.allow_tools
        assert "shell(git push:*)" in p.deny_tools


class TestMergePermissions:
    def test_both_confined_merges_allow(self):
        base = PermissionConfig(mode="confined", allow_tools=("read",))
        override = PermissionConfig(mode="confined", allow_tools=("write",))
        result = _merge_permissions(base, override)
        assert result.mode == "confined"
        assert "read" in result.allow_tools
        assert "write" in result.allow_tools

    def test_both_confined_merges_deny(self):
        base = PermissionConfig(mode="confined", deny_tools=("shell(git push:*)",))
        override = PermissionConfig(mode="confined", deny_tools=("shell(rm:*)",))
        result = _merge_permissions(base, override)
        assert "shell(git push:*)" in result.deny_tools
        assert "shell(rm:*)" in result.deny_tools

    def test_base_yolo_wins(self):
        base = PermissionConfig(mode="yolo")
        override = PermissionConfig(mode="confined", allow_tools=("read",))
        result = _merge_permissions(base, override)
        assert result.is_yolo

    def test_override_yolo_wins(self):
        base = PermissionConfig(mode="confined", allow_tools=("read",))
        override = PermissionConfig(mode="yolo")
        result = _merge_permissions(base, override)
        assert result.is_yolo

    def test_deduplication(self):
        base = PermissionConfig(mode="confined", allow_tools=("read", "write"))
        override = PermissionConfig(mode="confined", allow_tools=("write", "orc"))
        result = _merge_permissions(base, override)
        assert result.allow_tools.count("write") == 1

    def test_order_preserved(self):
        base = PermissionConfig(mode="confined", allow_tools=("a", "b"))
        override = PermissionConfig(mode="confined", allow_tools=("c",))
        result = _merge_permissions(base, override)
        assert result.allow_tools == ("a", "b", "c")


class TestParsePermissionBlock:
    def test_none_returns_default(self):
        p = _parse_permission_block(None, "test")
        assert p == PermissionConfig()

    def test_empty_dict_returns_default(self):
        p = _parse_permission_block({}, "test")
        assert p == PermissionConfig()

    def test_valid_confined(self):
        raw = {"mode": "confined", "allow_tools": ["read", "write"], "deny_tools": ["shell(rm:*)"]}
        p = _parse_permission_block(raw, "test")
        assert p.mode == "confined"
        assert p.allow_tools == ("read", "write")
        assert p.deny_tools == ("shell(rm:*)",)

    def test_valid_yolo(self):
        p = _parse_permission_block({"mode": "yolo"}, "test")
        assert p.is_yolo

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="confined.*yolo"):
            _parse_permission_block({"mode": "bananas"}, "test context")

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            _parse_permission_block("yolo", "test")

    def test_allow_tools_not_list_raises(self):
        with pytest.raises(ValueError, match="allow_tools"):
            _parse_permission_block({"allow_tools": "read"}, "test")

    def test_deny_tools_not_list_raises(self):
        with pytest.raises(ValueError, match="deny_tools"):
            _parse_permission_block({"deny_tools": "shell"}, "test")


class TestSquadConfigPermissions:
    def test_permissions_defaults_to_orc_defaults(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        p = cfg.permissions("coder")
        assert p.mode == "confined"
        for tool in _ORC_DEFAULT_ALLOW_TOOLS:
            assert tool in p.allow_tools

    def test_permissions_unknown_role_raises(self):
        cfg = SquadConfig(planner=1, coder=1, qa=1, merger=1, timeout_minutes=120)
        with pytest.raises(ValueError, match="Unknown role"):
            cfg.permissions("wizard")

    def test_squad_level_permissions_merged(self):
        squad_perm = PermissionConfig(mode="confined", allow_tools=("shell(just:*)",))
        cfg = SquadConfig(
            planner=1, coder=1, qa=1, merger=1, timeout_minutes=120, _permissions=squad_perm
        )
        p = cfg.permissions("coder")
        assert "shell(just:*)" in p.allow_tools
        # Orc defaults still present
        assert "orc" in p.allow_tools

    def test_role_level_permissions_merged(self):
        role_perm = PermissionConfig(mode="confined", allow_tools=("shell(npm:*)",))
        cfg = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            merger=1,
            timeout_minutes=120,
            _role_permissions={"coder": role_perm},
        )
        p_coder = cfg.permissions("coder")
        p_qa = cfg.permissions("qa")
        assert "shell(npm:*)" in p_coder.allow_tools
        assert "shell(npm:*)" not in p_qa.allow_tools

    def test_yolo_mode_short_circuits(self):
        squad_perm = PermissionConfig(mode="yolo")
        role_perm = PermissionConfig(mode="confined", allow_tools=("read",))
        cfg = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            merger=1,
            timeout_minutes=120,
            _permissions=squad_perm,
            _role_permissions={"coder": role_perm},
        )
        p = cfg.permissions("coder")
        assert p.is_yolo

    def test_role_yolo_overrides_squad_confined(self):
        squad_perm = PermissionConfig(mode="confined", allow_tools=("read",))
        role_perm = PermissionConfig(mode="yolo")
        cfg = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            merger=1,
            timeout_minutes=120,
            _permissions=squad_perm,
            _role_permissions={"coder": role_perm},
        )
        assert cfg.permissions("coder").is_yolo
        assert not cfg.permissions("qa").is_yolo


class TestParseSquadFilePermissions:
    def test_no_permissions_block_gives_defaults(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text(
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
        cfg = _parse_squad_file("s", f)
        p = cfg.permissions("coder")
        assert p.mode == "confined"
        assert "orc" in p.allow_tools

    def test_squad_level_permissions_parsed(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text(
            textwrap.dedent("""\
                permissions:
                  mode: confined
                  allow_tools:
                    - "shell(just:*)"
                  deny_tools:
                    - "shell(git push:*)"
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
        cfg = _parse_squad_file("s", f)
        p = cfg.permissions("coder")
        assert "shell(just:*)" in p.allow_tools
        assert "shell(git push:*)" in p.deny_tools

    def test_role_level_permissions_parsed(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text(
            textwrap.dedent("""\
                composition:
                  - role: planner
                    count: 1
                  - role: coder
                    count: 1
                    permissions:
                      allow_tools:
                        - "shell(npm:*)"
                  - role: qa
                    count: 1
                timeout_minutes: 60
            """)
        )
        cfg = _parse_squad_file("s", f)
        assert "shell(npm:*)" in cfg.permissions("coder").allow_tools
        assert "shell(npm:*)" not in cfg.permissions("qa").allow_tools

    def test_yolo_mode_parsed(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text(
            textwrap.dedent("""\
                permissions:
                  mode: yolo
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
        cfg = _parse_squad_file("s", f)
        assert cfg.permissions("coder").is_yolo

    def test_invalid_permissions_mode_raises(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text(
            textwrap.dedent("""\
                permissions:
                  mode: turbo
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
        with pytest.raises(ValueError, match="confined.*yolo"):
            _parse_squad_file("s", f)

    def test_load_squad_default_has_confined_permissions(self):
        cfg = load_squad("default")
        p = cfg.permissions("coder")
        assert p.mode == "confined"
        assert "orc" in p.allow_tools
