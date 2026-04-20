"""统一配置加载：读取 sync-tools/.env，提供全局路径常量。"""
import os
from pathlib import Path

# sync-tools 目录
SYNC_TOOLS_DIR = Path(__file__).resolve().parent


def _load_env() -> dict[str, str]:
    env_path = SYNC_TOOLS_DIR / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


_env = _load_env()


def _path(key: str, default: str) -> Path:
    """读取配置中的路径值，若是相对路径则以 ROOT 为基础解析。"""
    raw = _env.get(key, default)
    p = Path(raw.replace("\\", "/"))
    return p if p.is_absolute() else ROOT / p


# ── 项目根目录（可在 .env 中用 ROOT= 显式指定，否则自动推断）──────
if "ROOT" in _env:
    ROOT: Path = Path(_env["ROOT"].replace("\\", "/"))
else:
    ROOT = SYNC_TOOLS_DIR.parents[0]   # sync-tools -> 项目根

# ── Python 可执行文件 ─────────────────────────────────────────────
VENV_PYTHON: Path = _path("VENV_PYTHON", ".venv/Scripts/python.exe")

# ── 项目目录 ──────────────────────────────────────────────────────
TEMP_DIR:      Path = _path("TEMP_DIR",      "sync-tools/temp")
RM_DIR:        Path = _path("RM_DIR",        "sync-tools/rm")
FILE_DIR:      Path = _path("FILE_DIR",      "sync-tools/file_history")

_manifests_sub = _env.get("MANIFESTS_SUBDIR", "manifests")
MANIFESTS_DIR: Path = FILE_DIR / _manifests_sub

# ── 7-Zip ─────────────────────────────────────────────────────────
_default_7z = r"C:\Program Files\7-Zip\7z.exe,C:\Program Files (x86)\7-Zip\7z.exe"
SEVEN_ZIP_EXTRA: list[str] = [
    s.strip().replace("/", "\\")
    for s in _env.get("SEVEN_ZIP_EXTRA", _default_7z).split(",")
    if s.strip()
]
