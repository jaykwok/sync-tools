import fnmatch
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import xxhash as _xxhash
    _XXHASH_AVAILABLE = True
except ImportError:
    _XXHASH_AVAILABLE = False


# ── 忽略规则加载 ──────────────────────────────────────────────────

def load_syncignore(root_dir: str) -> tuple[list[str], list[str]]:
    """读取 <root_dir>/.syncignore，返回 (ignore_dirs, ignore_files)。
    若文件不存在，回退到内置默认值。
    """
    ignore_dirs: list[str] = []
    ignore_files: list[str] = []

    syncignore_path = os.path.join(root_dir, ".syncignore")
    if not os.path.exists(syncignore_path):
        # 内置默认值（与 .syncignore 模板一致）
        ignore_dirs = [".venv", ".claude", "sync-tools/temp", "sync-tools/rm", "sync-tools/file_history", "__pycache__"]
        ignore_files = ["*.log", "*.pyc", "Thumbs.db", "desktop.ini", "*manifest*.json.xz"]
        return ignore_dirs, ignore_files

    with open(syncignore_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("dir:"):
                val = line[4:].strip().replace("\\", "/")
                if val:
                    ignore_dirs.append(val)
            elif line.startswith("file:"):
                val = line[5:].strip()
                if val:
                    ignore_files.append(val)

    return ignore_dirs, ignore_files


# ── 全局缓存（避免每次 should_ignore_* 调用都重新 I/O）──────────

_cached_root: str | None = None
_cached_ignore_dirs: list[str] = []
_cached_ignore_files: list[str] = []


def init_ignore_rules(root_dir: str) -> tuple[list[str], list[str]]:
    """加载并缓存忽略规则，同一 root_dir 多次调用不重复读文件。"""
    global _cached_root, _cached_ignore_dirs, _cached_ignore_files
    root_abs = os.path.abspath(root_dir)
    if _cached_root != root_abs:
        _cached_root = root_abs
        _cached_ignore_dirs, _cached_ignore_files = load_syncignore(root_abs)
    return _cached_ignore_dirs, _cached_ignore_files


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _should_ignore_dir(rel_dir: str, ignore_dirs: list[str]) -> bool:
    normalized = normalize_path(rel_dir).strip("/")
    if not normalized:
        return False
    parts = [p.lower() for p in normalized.split("/")]
    for ignored in ignore_dirs:
        ignored_parts = [p.lower() for p in normalize_path(ignored).strip("/").split("/")]
        if parts == ignored_parts:
            return True
        if len(ignored_parts) == 1 and ignored_parts[0] in parts:
            return True
        if len(ignored_parts) > 1 and parts[:len(ignored_parts)] == ignored_parts:
            return True
    return False


def _should_ignore_file(filename: str, ignore_files: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename.lower(), p.lower()) for p in ignore_files)


# 便捷包装（供外部代码直接调用，使用已缓存规则）
def should_ignore_dir(rel_dir: str) -> bool:
    return _should_ignore_dir(rel_dir, _cached_ignore_dirs)

def should_ignore_file(filename: str) -> bool:
    return _should_ignore_file(filename, _cached_ignore_files)


# ── Hash ──────────────────────────────────────────────────────────

def format_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


def parse_mtime(iso_str: str) -> float:
    return datetime.fromisoformat(iso_str).timestamp()


def compute_hash(filepath: str, algo: str = "xxh3_64",
                 on_bytes: Callable[[int], None] | None = None) -> str:
    if algo == "xxh3_64" and _XXHASH_AVAILABLE:
        h = _xxhash.xxh3_64()
    elif algo == "xxh128" and _XXHASH_AVAILABLE:
        h = _xxhash.xxh128()
    else:
        h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    return h.hexdigest()


def default_hash_algo() -> str:
    return "xxh3_64" if _XXHASH_AVAILABLE else "sha256"


def human_readable_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


# ── 扫描 ──────────────────────────────────────────────────────────

def quick_scan(root_dir: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """快速扫描：不计算 hash。"""
    return scan_directory(root_dir, enable_hash=False)


def scan_directory(root_dir: str, enable_hash: bool = False,
                   hash_algo: str | None = None,
                   on_file: Callable[[str, int], None] | None = None,
                   on_bytes: Callable[[int], None] | None = None,
                   progress=None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if hash_algo is None:
        hash_algo = default_hash_algo()

    root_dir = os.path.abspath(root_dir)
    ignore_dirs, ignore_files = init_ignore_rules(root_dir)

    files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for current_root, dirnames, filenames in os.walk(root_dir):
        rel_current = os.path.relpath(current_root, root_dir)
        if rel_current == ".":
            rel_current = ""
        dirnames[:] = [
            d for d in dirnames
            if not _should_ignore_dir(
                normalize_path(os.path.join(rel_current, d)) if rel_current else d,
                ignore_dirs,
            )
        ]
        for filename in filenames:
            if _should_ignore_file(filename, ignore_files):
                continue
            filepath = os.path.join(current_root, filename)
            rel_path = normalize_path(os.path.relpath(filepath, root_dir))
            try:
                st = os.stat(filepath)
                file_info: dict[str, Any] = {
                    "path": rel_path,
                    "size": st.st_size,
                    "mtime": format_mtime(st.st_mtime),
                    "hash": "",
                }
                if enable_hash:
                    file_info["hash"] = compute_hash(filepath, hash_algo, on_bytes=on_bytes)
                files.append(file_info)
            except OSError as exc:
                errors.append({"path": rel_path, "error": str(exc)})

            if on_file:
                size = file_info["size"] if "file_info" in locals() and isinstance(file_info, dict) else 0
                on_file(rel_path, size)
            if progress is not None:
                try:
                    progress.update(1)
                except Exception:
                    pass

    files.sort(key=lambda x: x["path"])
    return files, errors
