"""Tests for the spawner — config writing, CLAUDE.md generation, capability resolution."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import spawner


class TestSlugify:
    def test_basic(self):
        assert spawner.slugify("Sell my lawnmower") == "sell-my-lawnmower"

    def test_special_chars(self):
        assert spawner.slugify("Run gummymine (v2)!") == "run-gummymine-v2"

    def test_truncation(self):
        long_name = "a" * 100
        assert len(spawner.slugify(long_name)) <= 50

    def test_strips_leading_trailing_hyphens(self):
        assert spawner.slugify("--test--") == "test"


class TestBuildClaudeMd:
    def test_minimal_brief(self):
        md = spawner._build_claude_md("Test Task", "Do something", {})
        assert "# Task: Test Task" in md
        assert "## Mission\nDo something" in md
        assert "## Autonomy Rules" in md
        assert "## On Startup" in md
        # Should NOT have optional sections
        assert "## Objectives" not in md
        assert "## Workflows" not in md
        assert "## Boundaries" not in md
        assert "## Memory" not in md

    def test_full_brief(self):
        brief = {
            "objectives": ["find niches", "validate demand"],
            "workflows": ["research", "analyze", "report"],
            "success_criteria": ["5 niches identified"],
            "boundaries": ["don't spend money", "no social posting"],
            "capabilities": ["memory", "scheduling", "human-approval"],
        }
        md = spawner._build_claude_md("Business Agent", "Run gummymine as a business", brief)

        assert "## Objectives" in md
        assert "- find niches" in md
        assert "- validate demand" in md

        assert "## Workflows" in md
        assert "1. research" in md
        assert "2. analyze" in md
        assert "3. report" in md

        assert "## Success Criteria" in md
        assert "- 5 niches identified" in md

        assert "## Boundaries" in md
        assert "- don't spend money" in md

        # Capability sections describe intent — tool names live in the MCP
        # servers' own descriptions, not in CLAUDE.md.
        assert "## Memory" in md
        assert "across sessions" in md
        assert "Use an available skill or tool" in md

        assert "## Scheduling" in md
        assert "recurring workflows" in md

        assert "## Human Approval" in md
        assert "request human approval" in md

        # And critically — no hardcoded tool names that would silently drift
        # if a capability provider changes its API.
        assert "store_memory" not in md
        assert "schedule_task(" not in md  # the MCP tool name with args
        assert "request_approval" not in md

    def test_memory_only_when_declared(self):
        md = spawner._build_claude_md("Test", "desc", {"capabilities": []})
        assert "## Memory" not in md

    def test_capabilities_gate_their_sections(self):
        """Each capability section appears only when declared."""
        md = spawner._build_claude_md("Test", "desc", {"capabilities": []})
        assert "## Memory" not in md
        assert "## Scheduling" not in md
        assert "## Human Approval" not in md

    def test_always_has_core_sections(self):
        md = spawner._build_claude_md("Test", "desc", {})
        assert "## Autonomy Rules" in md
        assert "## How to Escalate to Human" in md
        assert "## State File" in md
        assert "## Channel Communication" in md
        assert "## On Startup" in md


class TestWriteTaskConfig:
    def test_writes_files(self, tmp_path):
        with patch.object(spawner, 'TASKPILOT_DIR', tmp_path):
            td = spawner.write_task_config("test-task", "Test", "Do something", [])
            assert (td / "CLAUDE.md").exists()
            assert (td / "brief.json").exists()

    def test_brief_json_has_operating_brief(self, tmp_path):
        brief = {"objectives": ["goal1"], "capabilities": ["memory"]}
        with patch.object(spawner, 'TASKPILOT_DIR', tmp_path):
            td = spawner.write_task_config("t1", "T1", "desc", [], operating_brief=brief)
            data = json.loads((td / "brief.json").read_text())
            assert data["operating_brief"] == brief
            assert data["task_id"] == "t1"

    def test_brief_json_backward_compat(self, tmp_path):
        with patch.object(spawner, 'TASKPILOT_DIR', tmp_path):
            td = spawner.write_task_config("t2", "T2", "desc", ["/p1"])
            data = json.loads((td / "brief.json").read_text())
            assert data["operating_brief"] == {}
            assert data["plugins"] == ["/p1"]

    def test_claude_md_dynamic_content(self, tmp_path):
        brief = {
            "objectives": ["find 5 niches"],
            "boundaries": ["no spending"],
            "capabilities": ["memory"],
        }
        with patch.object(spawner, 'TASKPILOT_DIR', tmp_path):
            td = spawner.write_task_config("t3", "Niche Finder", "Find niches", [], brief)
            md = (td / "CLAUDE.md").read_text()
            assert "- find 5 niches" in md
            assert "- no spending" in md
            assert "## Memory" in md

    def test_creates_directory(self, tmp_path):
        with patch.object(spawner, 'TASKPILOT_DIR', tmp_path):
            td = spawner.write_task_config("new-dir", "New", "desc", [])
            assert td.is_dir()


class TestResolveCapabilities:
    """resolve_capabilities now hard-delegates to softwaresoftware. All tests
    mock _import_softwaresoftware to control what find_satisfier returns."""

    @pytest.fixture()
    def sw(self, tmp_path):
        """A mocked softwaresoftware (resolver, registry) pair, scriptable per-test."""
        from unittest.mock import MagicMock
        sw_resolver = MagicMock()
        sw_registry = MagicMock()
        with patch.object(spawner, "_import_softwaresoftware",
                          return_value=(sw_resolver, sw_registry)):
            yield sw_resolver, sw_registry, tmp_path

    def test_resolves_installed_provider(self, sw):
        sw_resolver, sw_registry, tmp_path = sw
        sw_resolver.find_satisfier.return_value = {"type": "plugin", "name": "memory-file"}
        sw_registry.get_plugin_install_path.return_value = tmp_path / "memory-file"
        assert spawner.resolve_capabilities(["memory"]) == [str(tmp_path / "memory-file")]
        sw_resolver.find_satisfier.assert_called_once_with("memory")

    def test_dedupes_one_plugin_many_capabilities(self, sw):
        """A plugin satisfying multiple requested capabilities appears once."""
        sw_resolver, sw_registry, tmp_path = sw
        sw_resolver.find_satisfier.return_value = {"type": "plugin", "name": "multi"}
        sw_registry.get_plugin_install_path.return_value = tmp_path / "multi"
        assert spawner.resolve_capabilities(["memory", "notification"]) == [str(tmp_path / "multi")]

    def test_skips_mcp_and_host_satisfiers(self, sw):
        """type=mcp / type=host / type=none don't add a --plugin-dir."""
        sw_resolver, sw_registry, _ = sw
        sw_resolver.find_satisfier.side_effect = [
            {"type": "mcp", "name": "slack-mcp"},
            {"type": "host", "host": "pixel-7-pro", "self": False},
            {"type": "none"},
        ]
        assert spawner.resolve_capabilities(["notification", "send-sms", "unknown"]) == []
        sw_registry.get_plugin_install_path.assert_not_called()

    def test_skips_when_install_path_missing(self, sw):
        """find_satisfier says plugin, but registry returns no path → skip."""
        sw_resolver, sw_registry, _ = sw
        sw_resolver.find_satisfier.return_value = {"type": "plugin", "name": "ghost"}
        sw_registry.get_plugin_install_path.return_value = None
        assert spawner.resolve_capabilities(["memory"]) == []

    def test_empty_capabilities_short_circuits(self):
        """No capabilities → return [] without touching softwaresoftware."""
        # No mock — would explode if _import_softwaresoftware was called.
        assert spawner.resolve_capabilities([]) == []

    def test_raises_when_softwaresoftware_missing(self, tmp_path):
        """Hard-fail when softwaresoftware isn't installed — no fallback."""
        with patch.object(spawner, "INSTALLED_PLUGINS_PATH", tmp_path / "missing.json"):
            with pytest.raises(RuntimeError, match="softwaresoftware"):
                spawner.resolve_capabilities(["memory"])


SESSION_BRIDGE = "session-bridge@softwaresoftware-plugins"
TASKPILOT = "taskpilot@softwaresoftware-plugins"
LITEFRAME = "liteframe@softwaresoftware-plugins"
NOTIFY_SLACK = "notify-slack@softwaresoftware-plugins"


class TestPrepareSandbox:
    """prepare_sandbox builds a curated $HOME for the spawned agent."""

    @pytest.fixture
    def fake_home(self, tmp_path, monkeypatch):
        """A stand-in user $HOME with the real-home files prepare_sandbox reads.

        Yields (user_home, read_sandbox_settings) — the latter loads the
        generated sandbox settings.json for a given task id.
        """
        user_home = tmp_path / "userhome"
        claude = user_home / ".claude"
        (claude / "sessions").mkdir(parents=True)
        plugins_dir = claude / "plugins"
        plugins_dir.mkdir()
        (claude / ".credentials.json").write_text('{"token": "abc"}')

        installed = {
            "plugins": {
                SESSION_BRIDGE: {}, TASKPILOT: {},
                LITEFRAME: {}, NOTIFY_SLACK: {},
            }
        }
        (plugins_dir / "installed_plugins.json").write_text(json.dumps(installed))

        settings = {
            "enabledPlugins": {LITEFRAME: True, NOTIFY_SLACK: True},
            "pluginConfigs": {
                NOTIFY_SLACK: {"options": {"webhook": "https://hooks/x"}},
                LITEFRAME: {"options": {"subdomain": "demo"}},
            },
            "extraKnownMarketplaces": {"softwaresoftware-plugins": {"source": "gh"}},
        }
        (claude / "settings.json").write_text(json.dumps(settings))
        (user_home / ".claude.json").write_text(json.dumps({
            "oauthAccount": {"id": "u1"},
            "mcpServers": {"global-mcp": {}},
            "projects": {"/some/proj": {}},
        }))

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
        monkeypatch.setattr(spawner, "TASKPILOT_DIR", tmp_path / "taskpilot")
        monkeypatch.setattr(spawner, "INSTALLED_PLUGINS_PATH",
                            plugins_dir / "installed_plugins.json")
        monkeypatch.setattr(spawner, "CLAUDE_JSON", user_home / ".claude.json")

        def read_sandbox_settings(task_id):
            return json.loads(
                (spawner.sandbox_home(task_id) / ".claude" / "settings.json").read_text()
            )

        return user_home, read_sandbox_settings

    def test_defaults_always_enabled(self, fake_home):
        """session-bridge and taskpilot enable even when allowed_plugins is empty."""
        _, read_settings = fake_home
        spawner.prepare_sandbox("t1", allowed_plugins=[])
        enabled = read_settings("t1")["enabledPlugins"]
        assert enabled == {SESSION_BRIDGE: True, TASKPILOT: True}

    def test_allowed_plugins_curated_in(self, fake_home):
        """An explicitly allowed plugin is enabled alongside the defaults."""
        _, read_settings = fake_home
        spawner.prepare_sandbox("t2", allowed_plugins=[LITEFRAME])
        enabled = read_settings("t2")["enabledPlugins"]
        assert enabled == {SESSION_BRIDGE: True, TASKPILOT: True, LITEFRAME: True}
        assert NOTIFY_SLACK not in enabled

    def test_plugin_config_carried_for_enabled(self, fake_home):
        """An enabled plugin's userConfig is carried forward from the user's settings."""
        _, read_settings = fake_home
        spawner.prepare_sandbox("t3", allowed_plugins=[LITEFRAME])
        configs = read_settings("t3")["pluginConfigs"]
        assert configs == {LITEFRAME: {"options": {"subdomain": "demo"}}}

    def test_plugin_config_dropped_for_disabled(self, fake_home):
        """A plugin left inert does not get its pluginConfigs entry leaked in."""
        _, read_settings = fake_home
        spawner.prepare_sandbox("t4", allowed_plugins=[LITEFRAME])
        assert NOTIFY_SLACK not in read_settings("t4")["pluginConfigs"]

    def test_marketplaces_carried_forward(self, fake_home):
        """extraKnownMarketplaces is carried so '@<marketplace>' keys resolve."""
        _, read_settings = fake_home
        spawner.prepare_sandbox("t5", allowed_plugins=[])
        assert read_settings("t5")["extraKnownMarketplaces"] == {
            "softwaresoftware-plugins": {"source": "gh"}
        }

    def test_claude_json_strips_mcps_and_projects(self, fake_home):
        """The sandbox .claude.json keeps account state but not global MCPs/projects."""
        spawner.prepare_sandbox("t6", allowed_plugins=[])
        cj = json.loads((spawner.sandbox_home("t6") / ".claude.json").read_text())
        assert cj["oauthAccount"] == {"id": "u1"}
        assert cj["mcpServers"] == {}
        assert "projects" not in cj or cj["projects"] == {}
