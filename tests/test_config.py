"""Tests for configuration loading.

Test 20: Config loading and validation.
"""

import os
import tempfile

import pytest
import yaml

from app.config import AppConfig, load_config, DEFAULT_BACKUP_PATTERNS


class TestConfigLoading:
    """Test 20: Configuration loading and validation."""

    def test_default_config(self):
        """Default config should have sensible values."""
        config = AppConfig()
        assert config.backup_root == "/backups"
        assert config.scan_interval_seconds == 3600
        assert config.health.expected_interval_hours == 26.0
        assert config.retention.dry_run is True
        assert config.retention.enabled is True
        assert config.retention.minimum_good_backups == 2
        assert config.restore_test.enabled is False
        assert config.ai.enabled is False

    def test_default_patterns(self):
        """Default backup patterns should include standard types."""
        config = AppConfig()
        assert "*.sql" in config.backup_file_patterns
        assert "*.sql.gz" in config.backup_file_patterns
        assert "*.dump" in config.backup_file_patterns
        assert "*.backup" in config.backup_file_patterns
        assert "*.tar.gz" in config.backup_file_patterns
        assert "*.tgz" in config.backup_file_patterns
        assert "*.zip" in config.backup_file_patterns

    def test_load_yaml_config(self, tmp_path):
        """Config should load from YAML file."""
        config_data = {
            "backup_root": "/custom/backups",
            "scan_interval_seconds": 1800,
            "health": {
                "expected_interval_hours": 48,
                "size_anomaly_threshold_percent": 50,
            },
            "retention": {
                "enabled": True,
                "days": 14,
                "dry_run": True,
                "minimum_good_backups": 3,
            },
        }

        config_file = tmp_path / "config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = load_config(str(config_file))
        assert config.backup_root == "/custom/backups"
        assert config.scan_interval_seconds == 1800
        assert config.health.expected_interval_hours == 48.0
        assert config.retention.days == 14
        assert config.retention.minimum_good_backups == 3

    def test_missing_config_uses_defaults(self, tmp_path):
        """Missing config file should use defaults."""
        config = load_config(str(tmp_path / "nonexistent.yml"))
        assert config.backup_root == "/backups"

    def test_env_var_override(self, tmp_path, monkeypatch):
        """Environment variables should override config file."""
        config_data = {"backup_root": "/yaml/path"}
        config_file = tmp_path / "config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        monkeypatch.setenv("BACKUP_ROOT", "/env/path")
        config = load_config(str(config_file))
        assert config.backup_root == "/env/path"

    def test_invalid_interval_raises(self):
        """Invalid expected_interval_hours should raise ValueError."""
        from app.config import HealthConfig, _validate_config
        config = AppConfig()
        config.health.expected_interval_hours = -1
        with pytest.raises(ValueError):
            _validate_config(config)

    def test_invalid_retention_days_raises(self):
        """Invalid retention.days should raise ValueError."""
        from app.config import _validate_config
        config = AppConfig()
        config.retention.days = 0
        with pytest.raises(ValueError):
            _validate_config(config)

    def test_empty_patterns_raises(self):
        """Empty backup_file_patterns should raise ValueError."""
        from app.config import _validate_config
        config = AppConfig()
        config.backup_file_patterns = []
        with pytest.raises(ValueError):
            _validate_config(config)
