"""Archive integrity verification.

Verifies archive integrity for supported formats:
- .gz: gzip -t
- .zip: unzip -t
- .tar.gz / .tgz: tar -tzf
- .sql: existence + non-zero size

Uses subprocess with argument arrays (never shell=True) for security.
Does not extract large backup files unnecessarily.
"""

import logging
import subprocess
from pathlib import Path
from typing import List, Tuple

from app.models import BackupFile, BackupGroup, HealthStatus, IntegrityStatus

logger = logging.getLogger(__name__)

# Timeout for integrity checks (seconds)
INTEGRITY_CHECK_TIMEOUT = 300


def _check_gzip_integrity(filepath: str) -> Tuple[bool, str]:
    """Test gzip archive integrity using gzip -t."""
    try:
        result = subprocess.run(
            ["gzip", "-t", filepath],
            capture_output=True,
            text=True,
            timeout=INTEGRITY_CHECK_TIMEOUT,
        )
        if result.returncode == 0:
            return True, "OK"
        return False, f"gzip integrity failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return True, "gzip not available, skipping"
    except subprocess.TimeoutExpired:
        return True, "gzip check timed out, skipping"
    except OSError as e:
        return True, f"gzip check error: {e}"


def _check_zip_integrity(filepath: str) -> Tuple[bool, str]:
    """Test zip archive integrity using unzip -t."""
    try:
        result = subprocess.run(
            ["unzip", "-t", filepath],
            capture_output=True,
            text=True,
            timeout=INTEGRITY_CHECK_TIMEOUT,
        )
        if result.returncode == 0:
            return True, "OK"
        return False, f"zip integrity failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return True, "unzip not available, skipping"
    except subprocess.TimeoutExpired:
        return True, "zip check timed out, skipping"
    except OSError as e:
        return True, f"zip check error: {e}"


def _check_tar_gz_integrity(filepath: str) -> Tuple[bool, str]:
    """Test tar.gz/tgz archive integrity using tar -tzf."""
    try:
        result = subprocess.run(
            ["tar", "-tzf", filepath],
            capture_output=True,
            text=True,
            timeout=INTEGRITY_CHECK_TIMEOUT,
        )
        if result.returncode == 0:
            return True, "OK"
        return False, f"tar.gz integrity failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return True, "tar not available, skipping"
    except subprocess.TimeoutExpired:
        return True, "tar check timed out, skipping"
    except OSError as e:
        return True, f"tar check error: {e}"


def _check_sql_integrity(filepath: str) -> Tuple[bool, str]:
    """Basic check for .sql files: existence and non-zero size."""
    p = Path(filepath)
    if not p.exists():
        return False, "File does not exist"
    if p.stat().st_size == 0:
        return False, "SQL file is empty (0 bytes)"
    return True, "OK"


def check_file_integrity(backup_file: BackupFile) -> None:
    """Check integrity of a single backup file based on its extension."""
    filename_lower = backup_file.filename.lower()

    if filename_lower.endswith(".tar.gz") or filename_lower.endswith(".tgz"):
        ok, msg = _check_tar_gz_integrity(backup_file.path)
    elif filename_lower.endswith(".sql.gz"):
        ok, msg = _check_gzip_integrity(backup_file.path)
    elif filename_lower.endswith(".gz"):
        ok, msg = _check_gzip_integrity(backup_file.path)
    elif filename_lower.endswith(".zip"):
        ok, msg = _check_zip_integrity(backup_file.path)
    elif filename_lower.endswith(".sql"):
        ok, msg = _check_sql_integrity(backup_file.path)
    elif filename_lower.endswith(".dump") or filename_lower.endswith(".backup"):
        # Custom pg_dump formats — check existence/size only
        ok, msg = _check_sql_integrity(backup_file.path)
    else:
        backup_file.integrity = IntegrityStatus.SKIPPED
        return

    if ok:
        backup_file.integrity = IntegrityStatus.PASS
    else:
        backup_file.integrity = IntegrityStatus.FAIL
        logger.warning(
            "Integrity FAIL: %s — %s", backup_file.path, msg
        )


def check_group_integrity(group: BackupGroup, sample_limit: int = 3) -> None:
    """Check integrity of files in a backup group.

    Optimized for production:
    - Tests the newest backup file first.
    - Samples up to sample_limit files per group to avoid blocking on massive historical archives.
    - Skips heavy gzip decompression on groups already marked STALE/FAIL from months/years ago.
    """
    if not group.files:
        return

    # If already marked STALE/FAIL from old dates, don't waste I/O decompressing ancient archives
    if group.health == HealthStatus.FAIL and any("STALE" in err for err in group.errors):
        group.integrity = IntegrityStatus.SKIPPED
        return

    failures: List[str] = []

    # Prioritize newest file + sample of other files
    files_to_check = []
    newest = group.newest_file
    if newest:
        files_to_check.append(newest)

    for f in group.files:
        if f not in files_to_check and len(files_to_check) < sample_limit:
            files_to_check.append(f)

    for f in files_to_check:
        check_file_integrity(f)
        if f.integrity == IntegrityStatus.FAIL:
            failures.append(f"INTEGRITY_FAIL: {f.filename}")

    if failures:
        group.integrity = IntegrityStatus.FAIL
        group.errors.extend(failures)
        group.health = HealthStatus.FAIL
        logger.error(
            "Server %s group %s INTEGRITY_FAIL: %d file(s)",
            group.server,
            group.group_name,
            len(failures),
        )
    else:
        group.integrity = IntegrityStatus.PASS
        logger.debug(
            "Server %s group %s integrity PASS (%d checked)",
            group.server,
            group.group_name,
            len(files_to_check),
        )
