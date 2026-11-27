"""Automatic discovery of backup servers, dates, and groups.

Discovers the canonical structure:
    <BACKUP_ROOT>/<IP>/YYYY/MM/DD/<BACKUP_GROUP>/

Also supports special (non-IP) directories configured explicitly.
"""

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import AppConfig
from app.models import BackupFile, BackupGroup

logger = logging.getLogger(__name__)

# Pattern to match IP-like directory names
IP_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# Pattern to match YYYY/MM/DD directory structure
YEAR_PATTERN = re.compile(r"^\d{4}$")
MONTH_PATTERN = re.compile(r"^\d{1,2}$")
DAY_PATTERN = re.compile(r"^\d{1,2}$")


def _is_ip_directory(name: str) -> bool:
    """Check if directory name looks like an IP address."""
    return bool(IP_PATTERN.match(name))


def _get_file_metadata(filepath: str) -> Optional[BackupFile]:
    """Collect filesystem metadata for a backup file.

    Uses actual filesystem timestamps. mtime is the primary freshness indicator.
    ctime is inode metadata-change time (NOT creation time on Linux).
    birth_time is true creation time if available.
    """
    try:
        stat_result = os.stat(filepath)
        mtime = datetime.fromtimestamp(stat_result.st_mtime)
        ctime = datetime.fromtimestamp(stat_result.st_ctime)

        # Try to get birth time (true creation time) if available
        birth_time = None
        if hasattr(stat_result, "st_birthtime"):
            try:
                birth_time = datetime.fromtimestamp(stat_result.st_birthtime)
            except (OSError, ValueError, OverflowError):
                pass

        return BackupFile(
            path=filepath,
            filename=os.path.basename(filepath),
            size_bytes=stat_result.st_size,
            mtime=mtime,
            ctime=ctime,
            birth_time=birth_time,
        )
    except OSError as e:
        logger.warning("Cannot stat file %s: %s", filepath, e)
        return None


def _discover_backup_files(
    directory: str, config: AppConfig
) -> List[BackupFile]:
    """Discover valid backup files in a directory using the allow-list."""
    files = []
    try:
        entries = os.listdir(directory)
    except OSError as e:
        logger.warning("Cannot list directory %s: %s", directory, e)
        return files

    for entry in sorted(entries):
        filepath = os.path.join(directory, entry)
        if not os.path.isfile(filepath):
            continue

        # Only include files matching the backup file allow-list
        if not config.matches_backup_pattern(entry):
            continue

        file_info = _get_file_metadata(filepath)
        if file_info is not None:
            files.append(file_info)

    return files


def _discover_date_dirs(ip_path: str) -> List[Tuple[str, str]]:
    """Discover YYYY/MM/DD directory structures under an IP directory.

    Returns list of (full_path, folder_date_string) tuples.
    """
    date_dirs = []
    try:
        years = os.listdir(ip_path)
    except OSError:
        return date_dirs

    for year in sorted(years):
        if not YEAR_PATTERN.match(year):
            continue
        year_path = os.path.join(ip_path, year)
        if not os.path.isdir(year_path):
            continue

        try:
            months = os.listdir(year_path)
        except OSError:
            continue

        for month in sorted(months):
            if not MONTH_PATTERN.match(month):
                continue
            month_path = os.path.join(year_path, month)
            if not os.path.isdir(month_path):
                continue

            try:
                days = os.listdir(month_path)
            except OSError:
                continue

            for day in sorted(days):
                if not DAY_PATTERN.match(day):
                    continue
                day_path = os.path.join(month_path, day)
                if not os.path.isdir(day_path):
                    continue

                # Construct folder date string
                try:
                    folder_date = f"{year}-{int(month):02d}-{int(day):02d}"
                    # Validate date
                    datetime.strptime(folder_date, "%Y-%m-%d")
                    date_dirs.append((day_path, folder_date))
                except ValueError:
                    logger.warning("Invalid date directory: %s/%s/%s", year, month, day)

    return date_dirs


def _discover_groups_in_date_dir(
    date_path: str, server: str, folder_date: str, config: AppConfig
) -> List[BackupGroup]:
    """Discover backup groups within a YYYY/MM/DD directory."""
    groups = []
    try:
        entries = os.listdir(date_path)
    except OSError as e:
        logger.warning("Cannot list date directory %s: %s", date_path, e)
        return groups

    for entry in sorted(entries):
        group_path = os.path.join(date_path, entry)
        if not os.path.isdir(group_path):
            continue

        files = _discover_backup_files(group_path, config)
        group = BackupGroup(
            server=server,
            group_name=entry,
            folder_date=folder_date,
            path=group_path,
            files=files,
        )
        groups.append(group)

    return groups


def discover_all(config: AppConfig) -> List[BackupGroup]:
    """Discover all backup groups from the backup root.

    Scans for:
    1. IP directories with YYYY/MM/DD/GROUP structure (automatic)
    2. Special directories configured explicitly
    """
    backup_root = config.backup_root
    all_groups: List[BackupGroup] = []

    if not os.path.isdir(backup_root):
        logger.error("Backup root does not exist: %s", backup_root)
        return all_groups

    try:
        top_entries = os.listdir(backup_root)
    except OSError as e:
        logger.error("Cannot list backup root %s: %s", backup_root, e)
        return all_groups

    # 1. Discover IP-based backup directories
    for entry in sorted(top_entries):
        entry_path = os.path.join(backup_root, entry)
        if not os.path.isdir(entry_path):
            continue

        if _is_ip_directory(entry):
            server = entry
            # Find latest date directory (scan most recent dates first for efficiency)
            date_dirs = _discover_date_dirs(entry_path)
            for date_path, folder_date in date_dirs:
                groups = _discover_groups_in_date_dir(
                    date_path, server, folder_date, config
                )
                all_groups.extend(groups)

    # 2. Discover special (non-IP) directories
    for name, special_cfg in config.special.items():
        special_path = special_cfg.path
        if not special_path:
            special_path = os.path.join(backup_root, name)

        if not os.path.isdir(special_path):
            logger.warning("Special directory not found: %s (%s)", name, special_path)
            continue

        # For special dirs, scan for backup files directly
        files = _discover_backup_files(special_path, config)

        # Also check subdirectories one level deep
        try:
            sub_entries = os.listdir(special_path)
        except OSError:
            sub_entries = []

        for sub in sorted(sub_entries):
            sub_path = os.path.join(special_path, sub)
            if os.path.isdir(sub_path):
                sub_files = _discover_backup_files(sub_path, config)
                if sub_files:
                    sub_group = BackupGroup(
                        server=name,
                        group_name=sub,
                        folder_date=None,
                        path=sub_path,
                        files=sub_files,
                        is_special=True,
                    )
                    all_groups.append(sub_group)

        # Create group for files directly in the special directory
        if files:
            group = BackupGroup(
                server=name,
                group_name=name,
                folder_date=None,
                path=special_path,
                files=files,
                is_special=True,
            )
            all_groups.append(group)

    logger.info("Discovered %d backup groups", len(all_groups))
    return all_groups


def discover_date_directories(config: AppConfig) -> List[Tuple[str, str, str]]:
    """Discover all IP/YYYY/MM/DD directories for retention processing.

    Returns list of (full_path, server, folder_date) tuples.
    """
    backup_root = config.backup_root
    results: List[Tuple[str, str, str]] = []

    if not os.path.isdir(backup_root):
        return results

    try:
        top_entries = os.listdir(backup_root)
    except OSError:
        return results

    for entry in sorted(top_entries):
        entry_path = os.path.join(backup_root, entry)
        if not os.path.isdir(entry_path):
            continue
        if not _is_ip_directory(entry):
            continue

        server = entry
        date_dirs = _discover_date_dirs(entry_path)
        for date_path, folder_date in date_dirs:
            results.append((date_path, server, folder_date))

    return results


def discover_system_storage(backup_root: str) -> dict:
    """Retrieve mount point storage statistics using shutil.disk_usage."""
    root_path = backup_root if os.path.exists(backup_root) else "."
    try:
        usage = shutil.disk_usage(root_path)
        total = usage.total
        used = usage.used
        free = usage.free
        pct = round((used / total * 100.0), 1) if total > 0 else 0.0
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent_used": pct,
            "mount_point": os.path.abspath(root_path),
        }
    except Exception as e:
        logger.warning("Could not read disk usage for %s: %s", root_path, e)
        return {
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "percent_used": 0.0,
            "mount_point": backup_root,
        }


def discover_system_files(backup_root: str, max_samples: int = 50) -> dict:
    """Discover non-backup helper files (scripts, logs, markdown, text files) in backup root."""
    system_files = {
        "total_count": 0,
        "total_bytes": 0,
        "scripts_count": 0,
        "logs_count": 0,
        "sample_files": [],
    }
    if not os.path.isdir(backup_root):
        return system_files

    helper_extensions = {".sh", ".bash", ".log", ".txt", ".md", ".json", ".py", ".conf"}
    collected_samples = []

    try:
        for root, dirs, files in os.walk(backup_root):
            try:
                rel_parts = Path(root).relative_to(Path(backup_root)).parts
                if len(rel_parts) > 5:
                    continue
            except ValueError:
                pass

            for fname in files:
                ext = Path(fname).suffix.lower()
                full_path = os.path.join(root, fname)
                if ext in helper_extensions:
                    try:
                        sz = os.path.getsize(full_path)
                        mtime_dt = datetime.fromtimestamp(os.path.getmtime(full_path)).isoformat()
                        system_files["total_count"] += 1
                        system_files["total_bytes"] += sz
                        if ext in (".sh", ".bash"):
                            system_files["scripts_count"] += 1
                        elif ext == ".log":
                            system_files["logs_count"] += 1

                        if len(collected_samples) < max_samples:
                            collected_samples.append({
                                "path": full_path,
                                "filename": fname,
                                "extension": ext,
                                "size_bytes": sz,
                                "mtime": mtime_dt,
                            })
                    except OSError:
                        pass
    except Exception as e:
        logger.warning("Error scanning system files in %s: %s", backup_root, e)

    system_files["sample_files"] = collected_samples
    return system_files


IGNORED_FS_TYPES = {
    "tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs",
    "udev", "proc", "sysfs", "cgroup", "cgroup2", "devpts", "autofs",
}


def _find_best_mount(target_path: str, mount_points: List[str]) -> Optional[str]:
    """Find the longest POSIX mount point prefix matching target_path."""
    if not target_path or not mount_points:
        return None
    import posixpath
    norm_target = posixpath.normpath(target_path.replace("\\", "/"))
    best: Optional[str] = None
    best_len = -1
    for mp in mount_points:
        norm_mp = posixpath.normpath(mp.replace("\\", "/"))
        if norm_mp == "/" or norm_target == norm_mp or norm_target.startswith(norm_mp.rstrip("/") + "/"):
            if len(norm_mp) > best_len:
                best = mp
                best_len = len(norm_mp)
    return best


def _parse_df_output(output: str, backup_root: str = "/backups", state_dir: str = "/state") -> List[Dict]:
    """Parse `df -B1 -PT` output into structured mount records."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return []

    raw_mounts: List[Dict] = []
    for line in lines[1:]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        fs, fs_type, size_s, used_s, avail_s, cap_s, mounted_on = parts
        try:
            size_b = int(size_s)
            used_b = int(used_s)
            avail_b = int(avail_s)
        except ValueError:
            continue

        pct = round((used_b / size_b * 100.0), 1) if size_b > 0 else 0.0
        if cap_s.endswith("%"):
            try:
                pct = float(cap_s[:-1])
            except ValueError:
                pass

        raw_mounts.append({
            "filesystem": fs,
            "fs_type": fs_type,
            "size_bytes": size_b,
            "used_bytes": used_b,
            "avail_bytes": avail_b,
            "percent_used": pct,
            "mounted_on": mounted_on,
        })

    mount_points = [m["mounted_on"] for m in raw_mounts]
    backup_mp = _find_best_mount(backup_root, mount_points)
    state_mp = _find_best_mount(state_dir, mount_points)

    filtered: List[Dict] = []
    seen_mounts = set()
    for m in raw_mounts:
        mp = m["mounted_on"]
        is_backup = (mp == backup_mp)
        is_state = (mp == state_mp)
        if m["fs_type"].lower() in IGNORED_FS_TYPES and not (is_backup or is_state):
            continue
        if (m["filesystem"], mp) in seen_mounts:
            continue
        seen_mounts.add((m["filesystem"], mp))
        m["is_backup_mount"] = is_backup
        m["is_state_mount"] = is_state
        filtered.append(m)

    # Sort backup mount first, then by mount point
    filtered.sort(key=lambda x: (not x["is_backup_mount"], not x["is_state_mount"], x["mounted_on"]))
    return filtered


def discover_df_mounts(backup_root: str, state_dir: str = "/state") -> List[Dict]:
    """Collect `df -hT` style mounted filesystem conditions.

    On POSIX systems, runs `df -B1 -PT` for exact byte sizes and filesystem types.
    Falls back to shutil.disk_usage on Windows or if df is unavailable.
    """
    if os.name == "posix":
        try:
            res = subprocess.run(
                ["df", "-B1", "-PT"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout:
                parsed = _parse_df_output(res.stdout, backup_root=backup_root, state_dir=state_dir)
                if parsed:
                    return parsed
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("df -B1 -PT failed, falling back to disk_usage: %s", e)

    # Cross-platform fallback (Windows / restricted sandbox)
    mounts: List[Dict] = []
    candidates = []
    if backup_root and os.path.exists(backup_root):
        candidates.append((os.path.abspath(backup_root), True, False))
    else:
        candidates.append((os.path.abspath("."), True, False))

    if state_dir and os.path.exists(state_dir):
        abs_state = os.path.abspath(state_dir)
        if abs_state != candidates[0][0]:
            candidates.append((abs_state, False, True))

    for path, is_bk, is_st in candidates:
        try:
            usage = shutil.disk_usage(path)
            pct = round((usage.used / usage.total * 100.0), 1) if usage.total > 0 else 0.0
            mounts.append({
                "filesystem": "local-disk",
                "fs_type": "ntfs" if os.name == "nt" else "local",
                "size_bytes": usage.total,
                "used_bytes": usage.used,
                "avail_bytes": usage.free,
                "percent_used": pct,
                "mounted_on": path,
                "is_backup_mount": is_bk,
                "is_state_mount": is_st,
            })
        except OSError:
            pass
    return mounts
