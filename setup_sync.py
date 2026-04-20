"""
部署脚本：检测环境、安装依赖，打印可用命令。
用法（在项目根目录运行）：
  .venv\\Scripts\\python.exe sync-tools\\setup_sync.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ROOT, VENV_PYTHON, SEVEN_ZIP_EXTRA, TEMP_DIR, FILE_DIR, RM_DIR

SYNC_TOOLS = Path(__file__).resolve().parent


def check(label: str, ok: bool, detail: str = ""):
    status = "OK  " if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def main():
    print("=" * 60)
    print("sync-tools 部署检测")
    print("=" * 60)

    all_ok = True

    # 1. Python 版本
    major, minor = sys.version_info[:2]
    py_ok = major == 3 and minor >= 13
    all_ok &= check("Python 版本", py_ok, f"{major}.{minor}（需要 3.13+）")

    # 2. .venv 存在
    venv_ok = VENV_PYTHON.exists()
    all_ok &= check(".venv Python", venv_ok, str(VENV_PYTHON))

    def try_install(pkg: str) -> tuple[bool, str]:
        """尝试 import，失败则安装，返回 (ok, version_str)。"""
        import importlib
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "VERSION", None) or getattr(mod, "__version__", None)
            if ver is None:
                import importlib.metadata
                ver = importlib.metadata.version(pkg)
            return True, str(ver)
        except ImportError:
            pass
        if not venv_ok:
            return False, "未安装"
        print(f"  → 正在安装 {pkg} ...")
        r = subprocess.run(
            [str(VENV_PYTHON), "-m", "pip", "install", pkg, "-q"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  安装失败: {r.stderr.strip()}")
            return False, "安装失败"
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "VERSION", None) or getattr(mod, "__version__", None)
            if ver is None:
                import importlib.metadata
                ver = importlib.metadata.version(pkg)
            return True, str(ver)
        except ImportError:
            return False, "安装后仍无法导入"

    # 3. xxhash
    xxhash_ok, xxhash_ver = try_install("xxhash")
    all_ok &= check("xxhash", xxhash_ok, f"版本 {xxhash_ver}")

    # 4. rich
    rich_ok, rich_ver = try_install("rich")
    all_ok &= check("rich", rich_ok, f"版本 {rich_ver}")

    # 5. 7-Zip
    SEVEN_ZIP_CANDIDATES = ["7z"] + SEVEN_ZIP_EXTRA
    seven_zip_path = None
    for candidate in SEVEN_ZIP_CANDIDATES:
        try:
            r = subprocess.run([candidate, "--help"], capture_output=True, timeout=5)
            if r.returncode == 0:
                seven_zip_path = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    sz_ok = seven_zip_path is not None
    all_ok &= check("7-Zip", sz_ok,
                    seven_zip_path if sz_ok else "未找到，请安装: https://www.7-zip.org/")

    # 5. sync_tools 脚本完整性
    for script in ("sync_common.py", "generate_manifest.py", "build_sync_package.py"):
        exists = (SYNC_TOOLS / script).exists()
        all_ok &= check(f"脚本: {script}", exists)

    # 6. Agent 目录结构
    for path in (FILE_DIR, TEMP_DIR, RM_DIR):
        path.mkdir(parents=True, exist_ok=True)
        rel = path.relative_to(ROOT)
        all_ok &= check(f"目录: {rel}", path.exists())

    print()
    if all_ok:
        print("所有检测通过！\n")
    else:
        print("部分检测失败，请根据上方提示修复后重试。\n")

    # === 打印使用命令 ===
    python = str(VENV_PYTHON)
    gen = str(SYNC_TOOLS / "generate_manifest.py")
    build = str(SYNC_TOOLS / "build_sync_package.py")
    root_str = str(ROOT)

    print("=" * 60)
    print("快速命令参考")
    print("=" * 60)
    print()
    print("【云端】生成清单（在云端项目根目录运行）:")
    print(f'  python "{gen}" .')
    print(f'  python "{gen}" . --hash   # 含 xxhash')
    print()
    print("【本机】dry-run 查看差异（下载 manifest.json.xz 后运行）:")
    print(f'  "{python}" "{build}" "{root_str}" "<manifest路径>" --dry-run')
    print()
    print("【本机】生成增量包（1g 分卷）:")
    print(f'  "{python}" "{build}" "{root_str}" "<manifest路径>"')
    print()
    print("【本机】生成增量包（500m 分卷）:")
    print(f'  "{python}" "{build}" "{root_str}" "<manifest路径>" --volume-size 500m')
    print()
    print("【云端】解压增量包（在云端项目根目录运行）:")
    print(f'  7z x sync_<时间戳>.7z -o"{root_str}" -y')
    print()
    print("【云端】处理删除列表（解压后，如包内含 delete_list.txt）:")
    print(f'  python apply_sync.py . "{root_str}"')
    print()


if __name__ == "__main__":
    main()
