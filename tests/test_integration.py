"""Integration tests — verify all phases work together.

Simulates a gummymine-business agent scenario: create a task with full operating
brief declaring memory, scheduling, and human-approval capabilities, then verify
all three capability plugins work correctly against the same task context.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add all plugin paths
PLUGINS_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent.parent))

import store
import spawner
import scheduler
import server as taskpilot_server

# Import capability plugin servers (need separate namespace)
memory_file_path = PLUGINS_DIR / "memory-file"
approval_channel_path = PLUGINS_DIR / "approval-channel"

# Save references before imports that might conflict
_real_store_get_db = store.get_db


def _import_plugin_server(plugin_path, module_name):
    """Import a plugin server module with a unique name to avoid conflicts."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        module_name, plugin_path / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


memory_server = _import_plugin_server(memory_file_path, "memory_server")
approval_server = _import_plugin_server(approval_channel_path, "approval_server")
# Scheduling is now built into taskpilot — use taskpilot_server directly
scheduler_server = taskpilot_server


class FakeCrontab:
    def __init__(self):
        self.content = ""

    def get(self):
        return self.content

    def set(self, content):
        self.content = content
        return True


@pytest.fixture
def integrated_env(tmp_path):
    """Set up a full integrated environment simulating a real task."""
    task_id = "gummymine-business"

    # Create the taskpilot DB
    db_path = str(tmp_path / "taskpilot.db")
    conn = _real_store_get_db(db_path)

    # Create the task with full operating brief
    brief = {
        "objectives": [
            "identify 5 profitable niches from Reddit demand signals",
            "validate demand with pricing research",
            "build content calendar for top 3 niches",
        ],
        "workflows": [
            "mine Reddit for complaints and requests",
            "analyze demand signals and score niches",
            "research pricing for top candidates",
            "draft content strategy per niche",
            "schedule recurring research cycles",
        ],
        "success_criteria": [
            "5 niches identified with demand scores > 7/10",
            "pricing research complete for top 3",
            "content calendar published",
        ],
        "boundaries": [
            "don't spend money without approval",
            "don't post to social media without approval",
            "don't contact anyone directly",
        ],
        "capabilities": ["memory", "scheduling", "human-approval"],
        "schedule": "0 9 * * *",
    }

    task = store.create_task(
        conn, task_id, "Gummymine Business Agent",
        "Run gummymine as a business — research, marketing, pricing, content.",
        plugins=[], operating_brief=brief,
    )
    conn.close()

    fake_cron = FakeCrontab()

    with (
        patch.object(spawner, "TASKPILOT_DIR", tmp_path),
        patch.object(memory_server, "TASKPILOT_DIR", tmp_path),
        patch.object(taskpilot_server, "TASKPILOT_DIR", tmp_path),
        patch.object(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json"),
        patch.object(scheduler, "_get_current_crontab", side_effect=lambda: fake_cron.get()),
        patch.object(scheduler, "_set_crontab", side_effect=lambda c: fake_cron.set(c) or True),
        patch.object(approval_server, "DATA_DIR", tmp_path, create=True),
        patch.object(approval_server, "_post_to_channel", return_value=True, create=True),
    ):
        os.environ["TASKPILOT_TASK_ID"] = task_id
        os.environ["APPROVAL_SESSION_ID"] = task_id

        # Write task config (CLAUDE.md + brief.json)
        spawner.write_task_config(
            task_id,
            "Gummymine Business Agent",
            "Run gummymine as a business — research, marketing, pricing, content.",
            [],
            brief,
        )

        yield {
            "tmp_path": tmp_path,
            "task_id": task_id,
            "task": task,
            "db_path": db_path,
            "fake_cron": fake_cron,
            "brief": brief,
        }

        del os.environ["TASKPILOT_TASK_ID"]
        del os.environ["APPROVAL_SESSION_ID"]


class TestTaskCreation:
    """Verify the task is created correctly with full brief."""

    def test_claude_md_has_all_sections(self, integrated_env):
        td = integrated_env["tmp_path"] / integrated_env["task_id"]
        md = (td / "CLAUDE.md").read_text()

        # Core sections
        assert "# Task: Gummymine Business Agent" in md
        assert "## Mission" in md

        # Brief-driven sections
        assert "## Objectives" in md
        assert "- identify 5 profitable niches" in md
        assert "## Workflows" in md
        assert "1. mine Reddit for complaints" in md
        assert "## Success Criteria" in md
        assert "## Boundaries" in md
        assert "- don't spend money without approval" in md

        # Capability sections
        assert "## Memory" in md
        assert "store_memory" in md
        assert "## Scheduling" in md
        assert "schedule_task" in md
        assert "## Human Approval" in md
        assert "request_approval" in md

        # Always-present sections
        assert "## Autonomy Rules" in md
        assert "## How to Escalate to Human" in md
        assert "## State File" in md
        assert "## On Startup" in md

    def test_brief_json_complete(self, integrated_env):
        td = integrated_env["tmp_path"] / integrated_env["task_id"]
        brief_data = json.loads((td / "brief.json").read_text())
        assert brief_data["task_id"] == "gummymine-business"
        assert brief_data["operating_brief"]["capabilities"] == ["memory", "scheduling", "human-approval"]
        assert len(brief_data["operating_brief"]["objectives"]) == 3

    def test_db_stores_brief(self, integrated_env):
        conn = _real_store_get_db(integrated_env["db_path"])
        task = store.get_task(conn, "gummymine-business")
        conn.close()
        stored_brief = json.loads(task["operating_brief"])
        assert stored_brief["capabilities"] == ["memory", "scheduling", "human-approval"]


class TestMemoryCapability:
    """Verify memory works within the task context."""

    def test_store_and_recall(self, integrated_env):
        memory_server.store_memory(
            "niche-1-pricing",
            "Dog anxiety products: avg $25-45, margins 60-70% on supplements",
        )
        result = memory_server.recall_memory("niche-1-pricing")
        assert result["found"] is True
        assert "Dog anxiety" in result["content"]

    def test_memory_scoped_to_task(self, integrated_env):
        memory_server.store_memory("task-data", "This belongs to gummymine-business")
        mem_dir = integrated_env["tmp_path"] / "gummymine-business" / "memory"
        assert (mem_dir / "task-data.json").exists()

    def test_accumulate_knowledge(self, integrated_env):
        """Simulate multiple research cycles building up knowledge."""
        memory_server.store_memory("cycle-1", "Found 3 promising niches: dog anxiety, home gym, meal prep")
        memory_server.store_memory("cycle-2", "Dog anxiety validated: 500+ monthly searches, low competition")
        memory_server.store_memory("cycle-3", "Pricing research: supplements avg $30, treats avg $15")

        result = memory_server.search_memory("dog anxiety")
        assert result["count"] >= 2  # Found in multiple memories

        listing = memory_server.list_memories()
        assert listing["count"] == 3

    def test_shared_memory_cross_task(self, integrated_env):
        """Store in shared memory, accessible regardless of task context."""
        memory_server.store_memory(
            "market-trend",
            "Pet industry growing 8% YoY",
            shared=True,
        )
        shared_dir = integrated_env["tmp_path"] / "shared-memory"
        assert (shared_dir / "market-trend.json").exists()


class TestSchedulerCapability:
    """Verify scheduling works within the task context."""

    def test_schedule_daily_research(self, integrated_env):
        result = scheduler_server.schedule_task(
            "daily-reddit-mine",
            "gummymine",
            "mine",
            "daily",
        )
        assert result["scheduled"] is True
        assert result["cron_expr"] == "0 9 * * *"

        # Verify crontab has the entry posting via session-bridge
        cron = integrated_env["fake_cron"].content
        assert "daily-reddit-mine" in cron
        assert f"127.0.0.1:8910/sessions/{integrated_env['task_id']}/message" in cron

    def test_multiple_schedules(self, integrated_env):
        scheduler_server.schedule_task("morning-research", "gummymine", "mine", "daily")
        scheduler_server.schedule_task("price-check", "gummymine", "check-prices", "every 6h")
        scheduler_server.schedule_task("weekly-report", "gummymine", "report", "weekly")

        result = scheduler_server.list_scheduled_tasks()
        assert result["count"] == 3

    def test_schedule_cleanup(self, integrated_env):
        scheduler_server.schedule_task("temp-task", "p", "s", "hourly")
        scheduler_server.remove_scheduled_task("temp-task")

        result = scheduler_server.list_scheduled_tasks()
        assert result["count"] == 0
        assert "temp-task" not in integrated_env["fake_cron"].content


class TestApprovalCapability:
    """Verify human-approval works within the task context."""

    def test_social_post_approval(self, integrated_env):
        """Agent wants to post to Reddit — needs approval."""
        req = approval_server.request_approval(
            "Post to r/dogs: '5 Signs Your Dog Has Anxiety (And What Actually Works)'",
            context="Content calendar Week 2, niche: dog anxiety products",
            category="social-post",
        )
        assert req["status"] == "pending"

        # Human approves
        approval_server.record_approval_response(
            req["request_id"],
            approved=True,
            comment="Good content, go ahead",
        )

        status = approval_server.check_approval(req["request_id"])
        assert status["status"] == "approved"

    def test_spending_rejection(self, integrated_env):
        """Agent wants to buy ads — human rejects."""
        req = approval_server.request_approval(
            "Purchase $200 Reddit ads for r/dogs targeting 'dog anxiety'",
            context="Marketing budget experiment",
            category="purchase",
            reversible=False,
        )

        approval_server.record_approval_response(
            req["request_id"],
            approved=False,
            comment="Start with organic content first",
        )

        status = approval_server.check_approval(req["request_id"])
        assert status["status"] == "rejected"

    def test_approval_state_persists(self, integrated_env):
        """Approvals survive file reads (simulating session restart)."""
        req = approval_server.request_approval("Persistent action")
        request_id = req["request_id"]

        # Verify it's in the file
        approvals_file = integrated_env["tmp_path"] / "gummymine-business" / "approvals.json"
        assert approvals_file.exists()
        data = json.loads(approvals_file.read_text())
        assert request_id in data


class TestFullAgentLifecycle:
    """Simulate a complete agent lifecycle using all capabilities together."""

    def test_gummymine_business_day_one(self, integrated_env):
        """Simulate day 1 of the gummymine-business agent."""

        # 1. Agent sets up recurring schedules
        scheduler_server.schedule_task("daily-mine", "gummymine", "mine", "daily")
        scheduler_server.schedule_task("weekly-report", "gummymine", "report", "weekly")

        schedules = scheduler_server.list_scheduled_tasks()
        assert schedules["count"] == 2

        # 2. Agent does initial research and stores findings
        memory_server.store_memory(
            "initial-research",
            "Mined r/dogs, r/dogtraining, r/puppy101. Found 847 posts about anxiety.",
        )
        memory_server.store_memory(
            "niche-scores",
            json.dumps({
                "dog-anxiety": {"score": 9, "posts": 847, "competition": "low"},
                "home-gym-accessories": {"score": 7, "posts": 312, "competition": "medium"},
                "meal-prep-tools": {"score": 6, "posts": 198, "competition": "high"},
            }),
        )

        # 3. Agent wants to post content — requests approval
        post_req = approval_server.request_approval(
            "Post to r/dogs: 'I spent a month researching dog anxiety solutions...'",
            context="First content piece, soft launch for dog-anxiety niche",
            category="social-post",
        )
        assert post_req["status"] == "pending"

        # 4. Human approves
        approval_server.record_approval_response(
            post_req["request_id"],
            approved=True,
            comment="Great first post, publish it",
        )

        # 5. Agent stores the result
        memory_server.store_memory(
            "post-1-result",
            "r/dogs post published. 47 upvotes in first 2 hours, 12 comments.",
        )

        # 6. Verify accumulated knowledge
        all_memories = memory_server.list_memories()
        assert all_memories["count"] == 3

        search = memory_server.search_memory("anxiety")
        assert search["count"] >= 1

        # 7. Agent wants to spend money — human rejects
        ad_req = approval_server.request_approval(
            "Boost r/dogs post with $50 Reddit promotion",
            category="purchase",
        )
        approval_server.record_approval_response(
            ad_req["request_id"],
            approved=False,
            comment="Wait for organic traction first",
        )

        # 8. Verify approval state
        pending = approval_server.list_pending_approvals()
        assert pending["count"] == 0  # All resolved

    def test_crash_recovery_memory_persists(self, integrated_env):
        """Memory persists across simulated crash recovery."""
        # Session 1: store knowledge
        memory_server.store_memory("before-crash", "Important finding from session 1")

        # Session 2: knowledge should still be there
        result = memory_server.recall_memory("before-crash")
        assert result["found"] is True
        assert "Important finding" in result["content"]

    def test_all_capabilities_isolated_per_task(self, integrated_env):
        """Verify each capability stores data in the correct task directory."""
        task_dir = integrated_env["tmp_path"] / "gummymine-business"

        # Memory
        memory_server.store_memory("isolation-test", "memory content")
        assert (task_dir / "memory" / "isolation-test.json").exists()

        # Approvals
        approval_server.request_approval("isolation action")
        assert (task_dir / "approvals.json").exists()

        # Schedules (these are global but tagged by task_id)
        schedules_file = integrated_env["tmp_path"] / "schedules.json"
        scheduler_server.schedule_task("iso-sched", "p", "s", "daily")
        data = json.loads(schedules_file.read_text())
        assert "gummymine-business:iso-sched" in data
