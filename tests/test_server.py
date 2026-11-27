"""Tests for the integrated Web UI, REST API, and Prometheus server."""

import json
from unittest.mock import MagicMock
from urllib.request import Request, urlopen

import pytest

from app.server import start_integrated_server


@pytest.fixture
def test_server(tmp_path):
    """Start an ephemeral test server on an open port."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    # Write a sample report
    sample_report = {
        "timestamp": "2026-11-23T12:00:00",
        "summary": {
            "total_groups": 10,
            "healthy": 8,
            "warning": 1,
            "failed": 1,
        },
        "groups": [
            {
                "server": "192.0.2.10",
                "group": "postgres_prod",
                "folder_date": "2026-11-22",
                "health": "PASS",
                "total_bytes": 1048576,
                "file_count": 2,
                "age_hours": 3.5,
                "integrity": "PASS",
                "size_anomaly": False,
            }
        ],
        "retention_candidates": [],
        "restore_results": [
            {
                "server": "192.0.2.10",
                "group": "postgres_prod",
                "file": "base.sql.gz",
                "success": True,
                "message": "Tables verified: 48",
                "duration_seconds": 12.4,
            }
        ],
        "system_storage": {
            "total_bytes": 1000000000,
            "used_bytes": 400000000,
            "free_bytes": 600000000,
            "percent_used": 40.0,
            "mount_point": "/backups",
        },
        "system_files": {
            "total_count": 3,
            "total_bytes": 4096,
            "scripts_count": 1,
            "logs_count": 2,
            "sample_files": [],
        },
    }
    (state_dir / "latest-report.json").write_text(json.dumps(sample_report), encoding="utf-8")
    (state_dir / "ai-summary.txt").write_text("All critical databases healthy.", encoding="utf-8")

    trigger_mock = MagicMock()

    # Use port 0 to bind to an ephemeral OS-assigned free port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    free_port = sock.getsockname()[1]
    sock.close()

    server = start_integrated_server(
        host="127.0.0.1",
        port=free_port,
        state_dir=str(state_dir),
        trigger_scan_fn=trigger_mock,
    )

    yield f"http://127.0.0.1:{free_port}", trigger_mock

    server.shutdown()
    server.server_close()


class TestIntegratedServer:
    """Test integrated server endpoints."""

    def test_dashboard_ui(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/") as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "Backwatch" in content
            assert "Backup Health Monitor" in content
            assert "https://github.com/cyraxabir/BackWatch" in content
            assert "<!DOCTYPE html>" in content
            assert "df -hT" in content
            assert "Folder & Project Tree" in content
            assert "renderProjectionGraph" in content

    def test_prometheus_metrics(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/metrics") as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            # Should contain prometheus metrics
            assert "backup_" in content or "# HELP" in content

    def test_api_report(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/api/report") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["summary"]["total_groups"] == 10
            assert data["groups"][0]["server"] == "192.0.2.10"
            assert "system_storage" in data
            assert data["system_storage"]["percent_used"] == 40.0
            assert "system_files" in data
            assert data["system_files"]["total_count"] == 3
            assert "restore_results" in data
            assert len(data["restore_results"]) == 1
            assert data["restore_results"][0]["success"] is True

    def test_api_summary(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/api/summary") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["healthy"] == 8
            assert data["failed"] == 1

    def test_api_ai_summary(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/api/ai-summary") as resp:
            assert resp.status == 200
            text = resp.read().decode("utf-8")
            assert text == "All critical databases healthy."

    def test_healthz(self, test_server):
        base_url, _ = test_server
        with urlopen(f"{base_url}/healthz") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"

    def test_trigger_scan_post(self, test_server):
        base_url, trigger_mock = test_server
        req = Request(f"{base_url}/api/scan", data=b"", method="POST")
        with urlopen(req) as resp:
            assert resp.status == 202
            data = json.loads(resp.read().decode("utf-8"))
            assert "message" in data

    def test_api_chat(self, test_server):
        base_url, _ = test_server
        payload = json.dumps({"message": "Which backups are failing?"}).encode("utf-8")
        req = Request(f"{base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "reply" in data
            assert data["status"] == "ok"


class TestBasicAuth:
    """Verify HTTP Basic Authentication enforcement (no DB)."""

    def test_basic_auth_enforcement(self, tmp_path):
        import base64
        import socket
        from urllib.error import HTTPError
        from app.config import AuthConfig

        state_dir = tmp_path / "auth_state"
        state_dir.mkdir(parents=True)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        auth_cfg = AuthConfig(enabled=True, username="admin", password="super-secret-password")
        srv = start_integrated_server(
            host="127.0.0.1",
            port=port,
            state_dir=str(state_dir),
            auth_config=auth_cfg,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            # 1. Unauthenticated request to / -> 401
            with pytest.raises(HTTPError) as exc:
                urlopen(f"{base_url}/")
            assert exc.value.code == 401
            assert "Basic" in exc.value.headers.get("WWW-Authenticate", "")

            # 2. Wrong password -> 401
            bad_tok = base64.b64encode(b"admin:wrong").decode("ascii")
            bad_req = Request(f"{base_url}/api/report", headers={"Authorization": f"Basic {bad_tok}"})
            with pytest.raises(HTTPError) as exc2:
                urlopen(bad_req)
            assert exc2.value.code == 401

            # 3. Healthcheck exempt -> 200 without auth
            with urlopen(f"{base_url}/healthz") as resp:
                assert resp.status == 200

            # 4. Valid credentials -> 200
            good_tok = base64.b64encode(b"admin:super-secret-password").decode("ascii")
            good_req = Request(f"{base_url}/", headers={"Authorization": f"Basic {good_tok}"})
            with urlopen(good_req) as resp:
                assert resp.status == 200
                assert "Backwatch" in resp.read().decode("utf-8")
        finally:
            srv.shutdown()
            srv.server_close()
