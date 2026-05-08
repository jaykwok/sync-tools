"""公共工具库：忽略规则、路径归一化、文件 hash、目录扫描、7z 检测、云端清单加载、交互询问。"""

import fnmatch
import json
import lzma
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
        ignore_dirs = [
            ".venv", ".git", ".claude", "node_modules", "__pycache__",
            "sync-tools/rm", "sync-tools/file_history",
        ]
        ignore_files = [
            "*.log", "*.pyc", "*.tmp", "Thumbs.db", "desktop.ini",
            "*manifest*.json.xz", "*.7z*",
        ]
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


# ── 全局缓存 ─────────────────────────────────────────────────────

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


# ── 路径归一化 ───────────────────────────────────────────────────

def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


# ── 忽略判断（内部用，接收已加载的规则列表）─────────────────────

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
        if len(ignored_parts) > 1 and parts[: len(ignored_parts)] == ignored_parts:
            return True
    return False


def _should_ignore_file(filename: str, ignore_files: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename.lower(), p.lower()) for p in ignore_files)


def should_ignore_dir(rel_dir: str) -> bool:
    if not _cached_ignore_dirs:
        return False
    return _should_ignore_dir(rel_dir, _cached_ignore_dirs)


def should_ignore_file(filename: str) -> bool:
    if not _cached_ignore_files:
        return False
    return _should_ignore_file(filename, _cached_ignore_files)


# ── Hash ──────────────────────────────────────────────────────────

def format_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


def parse_mtime(iso_str: str) -> float:
    return datetime.fromisoformat(iso_str).timestamp()


def compute_hash(
    filepath: str, algo: str = "xxh3_64", on_bytes: Callable[[int], None] | None = None
) -> str:
    if algo != "xxh3_64":
        raise ValueError(f"不支持的 hash 算法: {algo}；仅支持 XXH3")
    if not _XXHASH_AVAILABLE:
        raise RuntimeError("缺少 xxhash 依赖，请在项目 .venv 中安装 xxhash")
    h = _xxhash.xxh3_64()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    return h.hexdigest()


def default_hash_algo() -> str:
    if not _XXHASH_AVAILABLE:
        raise RuntimeError("缺少 xxhash 依赖，请在项目 .venv 中安装 xxhash")
    return "xxh3_64"


def hash_algo_display_name(algo: str | None = None) -> str:
    algo = algo or default_hash_algo()
    if algo == "xxh3_64":
        return "XXH3"
    return algo


def get_hash_worker_count() -> int:
    """返回当前进程可用的 hash 线程数。"""
    cpu_count_fn = getattr(os, "process_cpu_count", None)
    count = cpu_count_fn() if cpu_count_fn else os.cpu_count()
    return max(1, count or 1)


def human_readable_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


# ── 目录扫描 ─────────────────────────────────────────────────────


def scan_directory(
    root_dir: str,
    hash_algo: str | None = None,
    on_file: Callable[[str, int], None] | None = None,
    on_bytes: Callable[[int], None] | None = None,
    progress=None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if hash_algo is None:
        hash_algo = default_hash_algo()

    root_dir = os.path.abspath(root_dir)
    ignore_dirs, ignore_files = init_ignore_rules(root_dir)

    files: list[dict[str, Any]] = []
    hash_targets: list[tuple[dict[str, Any], str]] = []
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
                files.append(file_info)
                hash_targets.append((file_info, filepath))
            except OSError as exc:
                errors.append({"path": rel_path, "error": str(exc)})

    callback_lock = threading.Lock()

    def on_hash_bytes(n: int) -> None:
        if on_bytes is None:
            return
        with callback_lock:
            on_bytes(n)

    def finish_file(file_info: dict[str, Any]) -> None:
        if on_file:
            on_file(file_info["path"], file_info["size"])
        if progress is not None:
            try:
                progress.update(1)
            except Exception:
                pass

    def hash_one(file_info: dict[str, Any], filepath: str) -> tuple[dict[str, Any], str | None]:
        try:
            file_info["hash"] = compute_hash(filepath, hash_algo, on_bytes=on_hash_bytes)
            return file_info, None
        except OSError as exc:
            return file_info, str(exc)

    failed_paths: set[str] = set()
    worker_count = get_hash_worker_count()
    if worker_count == 1 or len(hash_targets) <= 1:
        for file_info, filepath in hash_targets:
            hashed_info, error = hash_one(file_info, filepath)
            if error:
                failed_paths.add(hashed_info["path"])
                errors.append({"path": hashed_info["path"], "error": error})
            else:
                finish_file(hashed_info)
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(hash_one, file_info, filepath) for file_info, filepath in hash_targets]
            for future in as_completed(futures):
                hashed_info, error = future.result()
                if error:
                    failed_paths.add(hashed_info["path"])
                    errors.append({"path": hashed_info["path"], "error": error})
                else:
                    finish_file(hashed_info)

    if failed_paths:
        files = [f for f in files if f["path"] not in failed_paths]

    files.sort(key=lambda x: x["path"])
    return files, errors


# ── 交互询问 ─────────────────────────────────────────────────────

def ask(prompt: str, choices: list[str], default: str) -> str:
    """显示 prompt 并等待用户输入，支持回车默认选项。"""
    opts = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        sys.stdout.write(f"{prompt} [{opts}]: ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans == "":
            return default
        if ans in choices:
            return ans
        print(f"  请输入 {' 或 '.join(choices)}", flush=True)


# ── 7-Zip 检测 ───────────────────────────────────────────────────

def find_7z(extra_paths: list[str] | None = None) -> str | None:
    """查找 7z 可执行文件路径，优先用 extra_paths，再沿 PATH。"""
    candidates = (extra_paths or []) + ["7z"]
    for candidate in candidates:
        try:
            r = subprocess.run([candidate, "--help"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


# ── 云端清单读取（支持 .xz 压缩版）───────────────────────────────

def load_cloud_manifest(manifest_path: str) -> dict:
    """读取 manifest.json 或 manifest.json.xz，返回 dict。"""
    if manifest_path.endswith(".xz"):
        with lzma.open(manifest_path, "rb") as f:
            raw = f.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
    else:
        with open(manifest_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

    if "files" not in data:
        raise ValueError("manifest 缺少 'files' 字段")
    for i, entry in enumerate(data["files"]):
        for key in ("path", "size", "mtime"):
            if key not in entry:
                raise ValueError(f"manifest files[{i}] 缺少 '{key}' 字段")
    return data
