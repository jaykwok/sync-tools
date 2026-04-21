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
    raw = _env.get(key, default)
    p = Path(raw.replace("\\", "/"))
    return p if p.is_absolute() else ROOT / p


# ── 项目根目录（可在 .env 中用 ROOT= 显式指定，否则自动推断）──────
if "ROOT" in _env:
    ROOT: Path = Path(_env["ROOT"].replace("\\", "/"))
else:
    ROOT = SYNC_TOOLS_DIR.parent   # sync-tools -> 项目根

# ── 7-Zip ─────────────────────────────────────────────────────────
_default_7z = r"C:\Program Files\7-Zip\7z.exe,C:\Program Files (x86)\7-Zip\7z.exe"
SEVEN_ZIP_EXTRA: list[str] = [
    s.strip().replace("/", "\\")
    for s in _env.get("SEVEN_ZIP_EXTRA", _default_7z).split(",")
    if s.strip()
]

# ── 项目目录 ──────────────────────────────────────────────────────
RM_DIR:        Path = _path("RM_DIR",        "sync-tools/rm")
FILE_DIR:      Path = _path("FILE_DIR",      "sync-tools/file_history")

_manifests_sub = _env.get("MANIFESTS_SUBDIR", "manifests")
MANIFESTS_DIR: Path = FILE_DIR / _manifests_sub

# ── 核心脚本清单 ───────────────────────────────────────────────────
SCRIPTS: list[str] = [
    "config.py", "setup_sync.py",
    "core/sync/sync_common.py",
    "core/pack/build_sync_package.py",
    "core/build/run_build.py",
    "core/generate/generate_manifest.py",
    "core/generate/run_generate.py",
]

# ── 运行时常量 ─────────────────────────────────────────────────────
APPLY_SYNC_SCRIPT: Path = SYNC_TOOLS_DIR / "core" / "apply" / "apply_sync.py"
APPLY_SYNC_BAT:    Path = SYNC_TOOLS_DIR / "core" / "apply" / "apply_sync.bat"
MTIME_TOLERANCE_SECONDS: float = 2.0
DEFAULT_VOLUME_SIZE: str = "1g"
VOLUME_PADDING_MB: int = 64
