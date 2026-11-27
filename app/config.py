"""Configuration loading and validation."""

import logging
import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_PATTERNS = [
    "*.sql",
    "*.sql.gz",
    "*.dump",
    "*.backup",
    "*.tar.gz",
    "*.tgz",
    "*.zip",
]


@dataclass
class HealthConfig:
    expected_interval_hours: float = 26.0
    minimum_file_size_bytes: int = 1
    size_anomaly_threshold_percent: float = 60.0


@dataclass
class RetentionConfig:
    enabled: bool = True
    days: int = 7
    dry_run: bool = True
    minimum_good_backups: int = 2


@dataclass
class RestoreTestConfig:
    enabled: bool = False
    frequency: str = "weekly"  # daily, weekly
    sample_count: int = 1


@dataclass
class SpecialDirConfig:
    path: str = ""
    expected_interval_hours: float = 26.0
    patterns: List[str] = field(default_factory=lambda: list(DEFAULT_BACKUP_PATTERNS))


@dataclass
class NotificationsConfig:
    webhook: str = ""
    alertmanager_url: str = ""


@dataclass
class AIConfig:
    enabled: bool = False
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    api_key: str = ""
    provider: str = "auto"  # "auto", "openai", "claude", "openwebui", "ollama"
    api_type: str = "auto"  # alias for provider


@dataclass
class AuthConfig:
    enabled: bool = False
    username: str = "admin"
    password: str = ""


@dataclass
class AppConfig:
    backup_root: str = "/backups"
    scan_interval_seconds: int = 3600
    state_dir: str = "/state"
    db_path: str = "/state/history.db"
    backup_file_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_BACKUP_PATTERNS))
    health: HealthConfig = field(default_factory=HealthConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    restore_test: RestoreTestConfig = field(default_factory=RestoreTestConfig)
    special: Dict[str, SpecialDirConfig] = field(default_factory=dict)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)

    def matches_backup_pattern(self, filename: str) -> bool:
        """Check if filename matches any configured backup file pattern."""
        for pattern in self.backup_file_patterns:
            if fnmatch(filename.lower(), pattern.lower()):
                return True
        return False


def _parse_special(raw: Dict[str, Any]) -> Dict[str, SpecialDirConfig]:
    """Parse special directory configurations."""
    result = {}
    if not raw:
        return result
    for name, cfg in raw.items():
        if isinstance(cfg, dict):
            result[name] = SpecialDirConfig(
                path=cfg.get("path", ""),
                expected_interval_hours=float(cfg.get("expected_interval_hours", 26.0)),
                patterns=cfg.get("patterns", list(DEFAULT_BACKUP_PATTERNS)),
            )
        else:
            result[name] = SpecialDirConfig(path=str(cfg))
    return result


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file and environment variables."""
    config_path = config_path or os.environ.get("CONFIG_PATH", "/app/config/config.yml")

    raw: Dict[str, Any] = {}
    path = Path(config_path)
    if path.exists():
        logger.info("Loading config from %s", config_path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        logger.warning("Config file %s not found, using defaults", config_path)

    # Build config from YAML with environment variable overrides
    health_raw = raw.get("health", {}) or {}
    retention_raw = raw.get("retention", {}) or {}
    restore_raw = raw.get("restore_test", {}) or {}
    notifications_raw = raw.get("notifications", {}) or {}
    ai_raw = raw.get("ai", {}) or {}
    auth_raw = raw.get("auth", {}) or {}

    auth_user = os.environ.get("AUTH_USERNAME", os.environ.get("ADMIN_USER", auth_raw.get("username", "admin")))
    auth_pass = os.environ.get("AUTH_PASSWORD", os.environ.get("ADMIN_PASSWORD", auth_raw.get("password", "")))
    auth_enabled_env = os.environ.get("AUTH_ENABLED")
    if auth_enabled_env is not None:
        auth_enabled = str(auth_enabled_env).lower() in ("true", "1", "yes")
    elif "enabled" in auth_raw:
        auth_enabled = bool(auth_raw.get("enabled", False))
    else:
        auth_enabled = bool(auth_pass)

    config = AppConfig(
        backup_root=os.environ.get("BACKUP_ROOT", raw.get("backup_root", "/backups")),
        scan_interval_seconds=int(os.environ.get(
            "SCAN_INTERVAL_SECONDS",
            raw.get("scan_interval_seconds", 3600),
        )),
        state_dir=os.environ.get("STATE_DIR", raw.get("state_dir", "/state")),
        db_path=os.environ.get("DB_PATH", raw.get("db_path", "/state/history.db")),
        backup_file_patterns=raw.get("backup_file_patterns", list(DEFAULT_BACKUP_PATTERNS)),
        health=HealthConfig(
            expected_interval_hours=float(health_raw.get("expected_interval_hours", 26.0)),
            minimum_file_size_bytes=int(health_raw.get("minimum_file_size_bytes", 1)),
            size_anomaly_threshold_percent=float(
                health_raw.get("size_anomaly_threshold_percent", 60.0)
            ),
        ),
        retention=RetentionConfig(
            enabled=bool(retention_raw.get("enabled", True)),
            days=int(retention_raw.get("days", 7)),
            dry_run=bool(retention_raw.get("dry_run", True)),
            minimum_good_backups=int(retention_raw.get("minimum_good_backups", 2)),
        ),
        restore_test=RestoreTestConfig(
            enabled=bool(restore_raw.get("enabled", False)),
            frequency=str(restore_raw.get("frequency", "weekly")),
            sample_count=int(restore_raw.get("sample_count", 1)),
        ),
        special=_parse_special(raw.get("special", {})),
        notifications=NotificationsConfig(
            webhook=os.environ.get("WEBHOOK_URL", notifications_raw.get("webhook", "")),
            alertmanager_url=os.environ.get(
                "ALERTMANAGER_URL",
                notifications_raw.get("alertmanager_url", ""),
            ),
        ),
        ai=AIConfig(
            enabled=bool(
                os.environ.get("AI_ENABLED", os.environ.get("OLLAMA_ENABLED", ai_raw.get("enabled", False)))
            ) if isinstance(os.environ.get("AI_ENABLED", os.environ.get("OLLAMA_ENABLED", ai_raw.get("enabled", False))), bool)
            else str(os.environ.get("AI_ENABLED", os.environ.get("OLLAMA_ENABLED", ai_raw.get("enabled", False)))).lower() in ("true", "1", "yes"),
            url=os.environ.get("AI_URL", os.environ.get("OLLAMA_URL", ai_raw.get("url", "http://localhost:11434"))),
            model=os.environ.get("AI_MODEL", os.environ.get("OLLAMA_MODEL", ai_raw.get("model", "qwen2.5:7b"))),
            api_key=os.environ.get("AI_API_KEY", os.environ.get("OLLAMA_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ai_raw.get("api_key", ""))))),
            provider=os.environ.get("AI_PROVIDER", os.environ.get("AI_API_TYPE", ai_raw.get("provider", ai_raw.get("api_type", "auto")))).lower(),
            api_type=os.environ.get("AI_PROVIDER", os.environ.get("AI_API_TYPE", ai_raw.get("provider", ai_raw.get("api_type", "auto")))).lower(),
        ),
        auth=AuthConfig(
            enabled=auth_enabled,
            username=auth_user,
            password=auth_pass,
        ),
    )

    _validate_config(config)
    return config


def _validate_config(config: AppConfig) -> None:
    """Validate configuration at startup."""
    backup_root = Path(config.backup_root)
    if not backup_root.exists():
        logger.warning("Backup root %s does not exist yet", config.backup_root)

    if config.health.expected_interval_hours <= 0:
        raise ValueError("expected_interval_hours must be positive")

    if config.retention.days < 1:
        raise ValueError("retention.days must be at least 1")

    if config.retention.minimum_good_backups < 1:
        raise ValueError("retention.minimum_good_backups must be at least 1")

    if not config.backup_file_patterns:
        raise ValueError("backup_file_patterns must not be empty")

    if config.health.size_anomaly_threshold_percent < 0 or \
       config.health.size_anomaly_threshold_percent > 100:
        raise ValueError("size_anomaly_threshold_percent must be 0-100")

    state_dir = Path(config.state_dir)
    if not state_dir.exists():
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created state directory %s", config.state_dir)
        except OSError as e:
            logger.error("Cannot create state directory %s: %s", config.state_dir, e)

    logger.info("Configuration validated successfully")
    logger.info("  Backup root: %s", config.backup_root)
    logger.info("  Scan interval: %ds", config.scan_interval_seconds)
    logger.info("  Backup patterns: %s", config.backup_file_patterns)
    logger.info("  Expected interval: %.1fh", config.health.expected_interval_hours)
    logger.info("  Retention: enabled=%s, days=%d, dry_run=%s",
                config.retention.enabled, config.retention.days, config.retention.dry_run)
    logger.info("  Restore test: enabled=%s", config.restore_test.enabled)
    logger.info("  Special dirs: %s", list(config.special.keys()) if config.special else "none")
    logger.info("  AI: enabled=%s", config.ai.enabled)
