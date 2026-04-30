"""Tests for taskpilot's host-aware spawn forwarding.

When a task carries `host=<peer>`, taskpilot dispatches the spawn to
that peer's session-bridge daemon rather than launching tmux locally.
The peer's /spawn endpoint returns once the agent registers; taskpilot
records the remote session_id and reports success.

Self-host tasks fall back to the local tmux path. Tasks with no host
field also use the local path (pre-PR-3 behavior unchanged).
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import spawner


def _fake_response(payload, status=200):
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock(
        read=lambda: json.dumps(payload).encode(),
        status=status,
    )
    cm.__exit__.return_value = False
    return cm


# --- lookup_peer_url --------------------------------------------------


def test_lookup_peer_url_returns_remote_url_for_known_host():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
        {"host": "pixel-7-pro", "self": False, "ip": "100.74.17.91", "port": 8910, "capabilities": ["sms-send"]},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        url = spawner.lookup_peer_url("pixel-7-pro")
    assert url == "http://100.74.17.91:8910"


def test_lookup_peer_url_returns_localhost_for_self():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        url = spawner.lookup_peer_url("local-yocal")
    # Self host always reaches via loopback — no point routing through tailscale.
    assert url == "http://127.0.0.1:8910"


def test_lookup_peer_url_returns_none_for_unknown_host():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        assert spawner.lookup_peer_url("ghost") is None


def test_lookup_peer_url_returns_none_when_session_bridge_down():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert spawner.lookup_peer_url("pixel-7-pro") is None


# --- is_self_host ----------------------------------------------------


def test_is_self_host_true_for_self_entry():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        assert spawner.is_self_host("local-yocal") is True


def test_is_self_host_false_for_peer():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
        {"host": "pixel-7-pro", "self": False, "ip": "100.74.17.91", "port": 8910, "capabilities": []},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        assert spawner.is_self_host("pixel-7-pro") is False


# --- spawn_remote ----------------------------------------------------


def test_spawn_remote_posts_to_peer_and_returns_payload():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
        {"host": "pixel-7-pro", "self": False, "ip": "100.74.17.91", "port": 8910, "capabilities": ["sms-send"]},
    ]
    spawn_response = {
        "spawned": True,
        "name": "phone-task",
        "namespace": "taskpilot",
        "session_id": "remote-uuid",
        "tmux_session": "spawn-phone-task.taskpilot",
        "initial_message_delivered": True,
    }

    captured = {}

    def fake_urlopen(req, *args, **kwargs):
        # First call lists hosts; second posts the spawn.
        if req.full_url.endswith("/hosts"):
            return _fake_response(hosts_payload)
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode()) if req.data else None
        captured["method"] = req.method
        return _fake_response(spawn_response)

    task = {
        "task_id": "phone-task",
        "description": "send sms hello",
        "plugins": "[]",
        "model": None,
        "cwd": None,
        "channels": "[]",
        "kind": "task",
        "host": "pixel-7-pro",
    }

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = spawner.spawn_remote(task)

    assert result["spawned"] is True
    assert result["session_id"] == "remote-uuid"
    assert captured["url"] == "http://100.74.17.91:8910/spawn"
    assert captured["method"] == "POST"
    assert captured["data"]["name"] == "phone-task"
    assert captured["data"]["initial_message"] == "send sms hello"


def test_spawn_remote_returns_error_when_peer_unknown():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
    ]
    task = {
        "task_id": "x", "description": "y", "plugins": "[]", "model": None, "cwd": None,
        "channels": "[]", "kind": "task", "host": "ghost-host",
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(hosts_payload)):
        result = spawner.spawn_remote(task)
    assert result.get("spawned") is False
    assert "ghost-host" in result["error"]


def test_spawn_remote_returns_error_when_peer_unreachable():
    hosts_payload = [
        {"host": "local-yocal", "self": True, "ip": "100.0.0.99", "port": 8910, "capabilities": []},
        {"host": "pixel-7-pro", "self": False, "ip": "100.74.17.91", "port": 8910, "capabilities": []},
    ]

    def fake_urlopen(req, *args, **kwargs):
        if req.full_url.endswith("/hosts"):
            return _fake_response(hosts_payload)
        raise urllib.error.URLError("connection refused")

    task = {
        "task_id": "x", "description": "y", "plugins": "[]", "model": None, "cwd": None,
        "channels": "[]", "kind": "task", "host": "pixel-7-pro",
    }
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = spawner.spawn_remote(task)
    assert result.get("spawned") is False
    assert "pixel-7-pro" in result["error"] or "unreachable" in result["error"].lower()


def test_spawn_remote_rejects_kind_service():
    """Remote /spawn doesn't currently install systemd units on the peer."""
    task = {
        "task_id": "x", "description": "y", "plugins": "[]", "model": None, "cwd": None,
        "channels": "[]", "kind": "service", "host": "pixel-7-pro",
    }
    result = spawner.spawn_remote(task)
    assert result.get("spawned") is False
    assert "service" in result["error"].lower()
