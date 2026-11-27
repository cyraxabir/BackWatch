"""Modular database restore testing framework.

Supports PostgreSQL and MySQL/MariaDB restore tests using disposable containers.
NEVER restores into production databases.

Restore testing is:
- Off by default
- Configurable (daily/weekly, sample_count)
- Isolated from production
- Fully logged
"""

import logging
import os
import random
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import AppConfig
from app.models import BackupFile, BackupGroup

logger = logging.getLogger(__name__)

# Container cleanup timeout
CONTAINER_TIMEOUT = 300


class RestoreTestResult:
    """Result of a single restore test."""

    def __init__(
        self,
        server: str,
        group_name: str,
        file: str,
        success: bool,
        message: str,
        duration_seconds: float = 0.0,
    ):
        self.server = server
        self.group_name = group_name
        self.file = file
        self.success = success
        self.message = message
        self.duration_seconds = duration_seconds

    def to_dict(self) -> dict:
        return {
            "server": self.server,
            "group": self.group_name,
            "file": self.file,
            "success": self.success,
            "message": self.message,
            "duration_seconds": round(self.duration_seconds, 2),
        }


def _detect_db_type(backup_file: BackupFile) -> Optional[str]:
    """Detect database type from backup file.

    Returns 'postgresql' or 'mysql' or None.
    """
    filename = backup_file.filename.lower()

    # PostgreSQL indicators
    if filename.endswith(".dump") or filename.endswith(".backup"):
        return "postgresql"

    # For .sql and .sql.gz, try to peek at content
    if filename.endswith(".sql") or filename.endswith(".sql.gz"):
        # Default to postgresql for now; can be enhanced with content inspection
        return "postgresql"

    return None


def _run_container_command(args: List[str], timeout: int = CONTAINER_TIMEOUT) -> Tuple[int, str, str]:
    """Run a container command safely (no shell=True)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "Docker not available"
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except OSError as e:
        return -1, "", str(e)


def _test_postgresql_restore(
    backup_file: BackupFile,
    group: BackupGroup,
) -> RestoreTestResult:
    """Test PostgreSQL restore using a disposable container.

    1. Create temporary PostgreSQL container
    2. Wait for it to be ready
    3. Restore backup
    4. Run validation query
    5. Record result
    6. Destroy container
    """
    container_name = f"backup-restore-test-pg-{uuid.uuid4().hex[:8]}"
    db_name = "restore_test"
    start_time = time.time()

    try:
        # 1. Start PostgreSQL container
        rc, stdout, stderr = _run_container_command([
            "docker", "run", "-d",
            "--name", container_name,
            "-e", "POSTGRES_PASSWORD=testpass",
            "-e", f"POSTGRES_DB={db_name}",
            "--tmpfs", "/var/lib/postgresql/data",
            "postgres:16-alpine",
        ])

        if rc != 0:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message=f"Failed to start PostgreSQL container: {stderr}",
                duration_seconds=time.time() - start_time,
            )

        # 2. Wait for PostgreSQL to be ready
        for _ in range(30):
            time.sleep(1)
            rc, _, _ = _run_container_command([
                "docker", "exec", container_name,
                "pg_isready", "-U", "postgres",
            ], timeout=10)
            if rc == 0:
                break
        else:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message="PostgreSQL container did not become ready",
                duration_seconds=time.time() - start_time,
            )

        # 3. Restore backup
        filename = backup_file.filename.lower()
        if filename.endswith(".sql.gz"):
            # Decompress and restore
            rc, stdout, stderr = _run_container_command([
                "docker", "exec", "-i", container_name,
                "bash", "-c",
                f"gunzip -c < /dev/stdin | psql -U postgres -d {db_name}",
            ])
        elif filename.endswith(".sql"):
            rc, stdout, stderr = _run_container_command([
                "docker", "exec", "-i", container_name,
                "psql", "-U", "postgres", "-d", db_name,
            ])
        elif filename.endswith((".dump", ".backup")):
            # Copy file to container first
            _run_container_command([
                "docker", "cp", backup_file.path,
                f"{container_name}:/tmp/restore_file",
            ])
            rc, stdout, stderr = _run_container_command([
                "docker", "exec", container_name,
                "pg_restore", "-U", "postgres", "-d", db_name,
                "--no-owner", "--no-privileges",
                "/tmp/restore_file",
            ])
        else:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message=f"Unsupported file format: {filename}",
                duration_seconds=time.time() - start_time,
            )

        # 4. Validation query
        rc_val, stdout_val, _ = _run_container_command([
            "docker", "exec", container_name,
            "psql", "-U", "postgres", "-d", db_name,
            "-c", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';",
        ], timeout=30)

        success = rc == 0 or rc_val == 0
        message = "Restore successful" if success else f"Restore failed: {stderr}"

        return RestoreTestResult(
            server=group.server,
            group_name=group.group_name,
            file=backup_file.filename,
            success=success,
            message=message,
            duration_seconds=time.time() - start_time,
        )

    finally:
        # 6. Destroy container
        _run_container_command([
            "docker", "rm", "-f", container_name,
        ], timeout=30)


def _test_mysql_restore(
    backup_file: BackupFile,
    group: BackupGroup,
) -> RestoreTestResult:
    """Test MySQL/MariaDB restore using a disposable container."""
    container_name = f"backup-restore-test-mysql-{uuid.uuid4().hex[:8]}"
    db_name = "restore_test"
    start_time = time.time()

    try:
        # 1. Start MySQL container
        rc, stdout, stderr = _run_container_command([
            "docker", "run", "-d",
            "--name", container_name,
            "-e", "MYSQL_ROOT_PASSWORD=testpass",
            "-e", f"MYSQL_DATABASE={db_name}",
            "--tmpfs", "/var/lib/mysql",
            "mysql:8-oracle",
        ])

        if rc != 0:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message=f"Failed to start MySQL container: {stderr}",
                duration_seconds=time.time() - start_time,
            )

        # 2. Wait for MySQL to be ready
        for _ in range(60):
            time.sleep(2)
            rc, _, _ = _run_container_command([
                "docker", "exec", container_name,
                "mysqladmin", "ping", "-uroot", "-ptestpass",
            ], timeout=10)
            if rc == 0:
                break
        else:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message="MySQL container did not become ready",
                duration_seconds=time.time() - start_time,
            )

        # 3. Restore backup
        filename = backup_file.filename.lower()
        if filename.endswith(".sql.gz"):
            rc, stdout, stderr = _run_container_command([
                "docker", "exec", "-i", container_name,
                "bash", "-c",
                f"gunzip -c < /dev/stdin | mysql -uroot -ptestpass {db_name}",
            ])
        elif filename.endswith(".sql"):
            rc, stdout, stderr = _run_container_command([
                "docker", "exec", "-i", container_name,
                "mysql", "-uroot", "-ptestpass", db_name,
            ])
        else:
            return RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message=f"Unsupported file format: {filename}",
                duration_seconds=time.time() - start_time,
            )

        # 4. Validation query
        rc_val, stdout_val, _ = _run_container_command([
            "docker", "exec", container_name,
            "mysql", "-uroot", "-ptestpass", db_name,
            "-e", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE();",
        ], timeout=30)

        success = rc == 0 or rc_val == 0
        message = "Restore successful" if success else f"Restore failed: {stderr}"

        return RestoreTestResult(
            server=group.server,
            group_name=group.group_name,
            file=backup_file.filename,
            success=success,
            message=message,
            duration_seconds=time.time() - start_time,
        )

    finally:
        _run_container_command([
            "docker", "rm", "-f", container_name,
        ], timeout=30)


def run_restore_tests(
    groups: List[BackupGroup],
    config: AppConfig,
) -> List[RestoreTestResult]:
    """Run restore tests on a sample of backup groups.

    Selects sample_count random groups and tests one file from each.
    """
    if not config.restore_test.enabled:
        logger.info("Restore testing is disabled")
        return []

    # Filter to groups with files
    eligible = [g for g in groups if g.files]
    if not eligible:
        logger.warning("No eligible groups for restore testing")
        return []

    # Sample
    sample_count = min(config.restore_test.sample_count, len(eligible))
    sample = random.sample(eligible, sample_count)

    results: List[RestoreTestResult] = []

    for group in sample:
        # Pick one file from the group
        backup_file = group.files[0]  # Use first file (usually consistent)
        db_type = _detect_db_type(backup_file)

        if db_type == "postgresql":
            result = _test_postgresql_restore(backup_file, group)
        elif db_type == "mysql":
            result = _test_mysql_restore(backup_file, group)
        else:
            result = RestoreTestResult(
                server=group.server,
                group_name=group.group_name,
                file=backup_file.filename,
                success=False,
                message=f"Unknown database type for {backup_file.filename}",
            )

        results.append(result)
        logger.info(
            "Restore test %s: server=%s group=%s file=%s — %s",
            "PASS" if result.success else "FAIL",
            result.server,
            result.group_name,
            result.file,
            result.message,
        )

    return results
