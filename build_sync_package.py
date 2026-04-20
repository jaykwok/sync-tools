"""本地脚本：读取云端清单，差异比对，生成增量同步压缩包"""

import argparse
import json
import lzma
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_common import (
    scan_directory, parse_mtime,
    compute_hash, human_readable_size,
)
from config import SEVEN_ZIP_EXTRA, TEMP_DIR, FILE_DIR, MANIFESTS_DIR, RM_DIR

# === 7-Zip 检测 ===

SEVEN_ZIP_CANDIDATES = ["7z"] + SEVEN_ZIP_EXTRA


def find_7z() -> str | None:
    for candidate in SEVEN_ZIP_CANDIDATES:
        try:
            result = subprocess.run(
                [candidate, "--help"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


# === 云端清单读取（支持 .xz 压缩版）===

def load_cloud_manifest(manifest_path: str) -> dict:
    if manifest_path.endswith(".xz"):
        with lzma.open(manifest_path, "rb") as f:
            raw = f.read()
        # strip UTF-8 BOM if present
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


# === 差异比对 ===

MTIME_TOLERANCE_SECONDS = 2.0


def compare_files(local_files: list, cloud_manifest: dict,
                  hash_check: bool = False, local_dir: str = "") -> dict:
    cloud_hash_algo = cloud_manifest.get("hash_algo") or "sha256"
    cloud_map = {entry["path"]: entry for entry in cloud_manifest["files"]}
    local_paths = {f["path"] for f in local_files}

    new_files = []
    updated_files = []
    skipped_files = []

    for local_file in local_files:
        path = local_file["path"]
        cloud_entry = cloud_map.get(path)

        if cloud_entry is None:
            new_files.append({"path": path, "size": local_file["size"]})
            continue

        if local_file["size"] != cloud_entry["size"]:
            updated_files.append({"path": path, "size": local_file["size"], "reason": "size_changed"})
            continue

        local_ts = parse_mtime(local_file["mtime"])
        cloud_ts = parse_mtime(cloud_entry["mtime"])
        diff = local_ts - cloud_ts

        if diff > MTIME_TOLERANCE_SECONDS:
            if hash_check and cloud_entry.get("hash"):
                abs_path = os.path.join(local_dir, path.replace("/", os.sep))
                if compute_hash(abs_path, cloud_hash_algo) == cloud_entry["hash"]:
                    skipped_files.append(path)
                    continue
            updated_files.append({"path": path, "size": local_file["size"], "reason": "mtime_newer"})
        else:
            skipped_files.append(path)

    # 云端有、本机没有的文件 → 待删除
    deleted_files = [
        {"path": path}
        for path in cloud_map
        if path not in local_paths
    ]
    deleted_files.sort(key=lambda x: x["path"])

    # 推断孤儿目录：云端文件所在目录集合 - 本机文件所在目录集合
    # 只保留"删完文件后本机已不存在的目录"（祖先目录也纳入）
    def ancestors(p: str) -> set[str]:
        parts = p.split("/")
        return {"/".join(parts[:i]) for i in range(1, len(parts))}

    cloud_dirs: set[str] = set()
    for entry in cloud_manifest["files"]:
        cloud_dirs |= ancestors(entry["path"])

    local_dirs: set[str] = set()
    for f in local_files:
        local_dirs |= ancestors(f["path"])

    deleted_dirs = sorted(cloud_dirs - local_dirs, reverse=True)  # 深度优先，先删子目录

    return {
        "new_files": new_files,
        "updated_files": updated_files,
        "skipped_files": skipped_files,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
    }


# === 报告生成 ===

def generate_reports(diff_result: dict, local_dir: str, manifest_path: str,
                     total_local: int, total_cloud: int, errors: list,
                     volume_size_bytes: int, report_dir: str, timestamp: str):
    all_diff = diff_result["new_files"] + diff_result["updated_files"]
    diff_total_size = sum(f["size"] for f in all_diff)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "local_dir": local_dir,
        "cloud_manifest": manifest_path,
        "summary": {
            "total_local_files": total_local,
            "total_cloud_files": total_cloud,
            "new_files": len(diff_result["new_files"]),
            "updated_files": len(diff_result["updated_files"]),
            "deleted_files": len(diff_result["deleted_files"]),
            "skipped_files": len(diff_result["skipped_files"]),
            "error_files": len(errors),
            "diff_total_size": diff_total_size,
            "diff_total_size_human": human_readable_size(diff_total_size),
            "will_split_volumes": diff_total_size > volume_size_bytes,
        },
        "new_files": diff_result["new_files"],
        "updated_files": diff_result["updated_files"],
        "deleted_files": diff_result["deleted_files"],
        "errors": errors,
    }

    os.makedirs(report_dir, exist_ok=True)

    json_path = os.path.join(report_dir, f"diff_report_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8-sig") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report, json_path


# === 文件复制 ===

def copy_diff_files(diff_result: dict, local_dir: str, temp_dir: str) -> list:
    copy_errors = []
    for entry in diff_result["new_files"] + diff_result["updated_files"]:
        rel_path = entry["path"]
        src = os.path.join(local_dir, rel_path.replace("/", os.sep))
        dst = os.path.join(temp_dir, rel_path.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except (PermissionError, OSError) as e:
            copy_errors.append({"path": rel_path, "error": str(e)})
    return copy_errors


# === 包内元数据 ===

def write_sync_manifest(temp_dir: str, local_dir: str, diff_result: dict, hash_check: bool):
    from sync_common import init_ignore_rules
    from config import RM_DIR
    ignore_dirs, ignore_files = init_ignore_rules(local_dir)
    all_diff = diff_result["new_files"] + diff_result["updated_files"]
    total_size = sum(f["size"] for f in all_diff)
    manifest = {
        "pack_time": datetime.now().astimezone().isoformat(),
        "source_dir": local_dir,
        "file_count": len(all_diff),
        "total_size": total_size,
        "total_size_human": human_readable_size(total_size),
        "hash_check_enabled": hash_check,
        "ignore_dirs": ignore_dirs,
        "ignore_files": ignore_files,
        "deleted_files": [e["path"] for e in diff_result["deleted_files"]],
        "rm_dir": str(RM_DIR),
    }
    with open(os.path.join(temp_dir, "sync_manifest.json"), "w", encoding="utf-8-sig") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# === 删除列表 ===

def write_delete_list(temp_dir: str, deleted_files: list, deleted_dirs: list):
    if not deleted_files and not deleted_dirs:
        return
    delete_list_path = os.path.join(temp_dir, "delete_list.txt")
    with open(delete_list_path, "w", encoding="utf-8") as f:
        if deleted_files:
            f.write("[files]\n")
            for entry in deleted_files:
                f.write(entry["path"] + "\n")
        if deleted_dirs:
            f.write("[dirs]\n")
            for d in deleted_dirs:
                f.write(d + "\n")


# === apply_sync.py 脚本写入 ===

APPLY_SYNC_SCRIPT = r'''
"""
云端增量同步清理脚本
将 delete_list.txt 中的文件/目录移动到 sync-tools/rm/，完成后自删本脚本及配套文件。
由 apply_sync.bat 自动调用，无需手动传参。
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def parse_delete_list(path: str) -> tuple[list[str], list[str]]:
    files, dirs = [], []
    section = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line == "[files]":
                section = "files"
            elif line == "[dirs]":
                section = "dirs"
            elif section == "files":
                files.append(line)
            elif section == "dirs":
                dirs.append(line)
            else:
                files.append(line)   # 兼容旧格式（无 section 头）
    return files, dirs


def main():
    script_dir = Path(__file__).resolve().parent
    # bat 将 project_root 作为第一个参数传入
    if len(sys.argv) >= 2:
        project_root = Path(sys.argv[1]).resolve()
    else:
        project_root = script_dir   # 解压到根目录时脚本就在根目录

    delete_list_path = script_dir / "delete_list.txt"

    try:
        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn, TimeElapsedColumn
        from rich.panel import Panel
        USE_RICH = True
    except ImportError:
        USE_RICH = False

    if USE_RICH:
        console = Console()
        console.print(f"\n[bold cyan]云端同步清理[/bold cyan]  项目: {project_root}\n")
    else:
        print(f"\n云端同步清理  项目: {project_root}\n")

    if not delete_list_path.exists():
        msg = "无 delete_list.txt，无需清理。"
        (console.print(f"[green]{msg}[/green]") if USE_RICH else print(msg))
        _self_clean(script_dir, USE_RICH)
        return

    del_files, del_dirs = parse_delete_list(str(delete_list_path))

    rm_base_path: Path | None = None
    sync_manifest_path = script_dir / "sync_manifest.json"
    if sync_manifest_path.exists():
        try:
            import json as _json
            meta = _json.loads(sync_manifest_path.read_text(encoding="utf-8-sig"))
            rm_dir_str = meta.get("rm_dir")
            if rm_dir_str:
                rm_base_path = Path(rm_dir_str)
        except Exception:
            pass
    if rm_base_path is None:
        rm_base_path = project_root / "sync-tools" / "rm"
    rm_base = rm_base_path / ("sync_delete_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    rm_base.mkdir(parents=True, exist_ok=True)

    file_moved = file_skipped = dir_moved = dir_skipped = 0

    # ── 移动文件 ──────────────────────────────────────────────────
    if del_files:
        if USE_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=35),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console, transient=True,
            ) as prog:
                task = prog.add_task("移动文件...", total=len(del_files))
                for rel in del_files:
                    src = project_root / Path(rel.replace("/", os.sep))
                    prog.update(task, description=rel[-50:] if len(rel) > 50 else rel)
                    if src.exists():
                        dst = rm_base / Path(rel.replace("/", os.sep))
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                        file_moved += 1
                    else:
                        file_skipped += 1
                    prog.update(task, advance=1)
        else:
            for rel in del_files:
                src = project_root / Path(rel.replace("/", os.sep))
                if src.exists():
                    dst = rm_base / Path(rel.replace("/", os.sep))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    file_moved += 1
                    print(f"  [文件] 已移动: {rel}")
                else:
                    file_skipped += 1

    # ── 移动目录（深度优先，rev=True 已保证子目录在前）────────────
    if del_dirs:
        if USE_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=35),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console, transient=True,
            ) as prog:
                task = prog.add_task("移动目录...", total=len(del_dirs))
                for rel in del_dirs:
                    src = project_root / Path(rel.replace("/", os.sep))
                    prog.update(task, description=rel[-50:] if len(rel) > 50 else rel)
                    if src.exists() and src.is_dir():
                        dst = rm_base / Path(rel.replace("/", os.sep))
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                        dir_moved += 1
                    else:
                        dir_skipped += 1
                    prog.update(task, advance=1)
        else:
            for rel in del_dirs:
                src = project_root / Path(rel.replace("/", os.sep))
                if src.exists() and src.is_dir():
                    dst = rm_base / Path(rel.replace("/", os.sep))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    dir_moved += 1
                    print(f"  [目录] 已移动: {rel}")
                else:
                    dir_skipped += 1

    summary = (
        f"文件: 移动 {file_moved} / 跳过 {file_skipped}  "
        f"目录: 移动 {dir_moved} / 跳过 {dir_skipped}\n"
        f"移动目标: {rm_base}"
    )
    if USE_RICH:
        console.print(Panel(f"[green]清理完成[/green]\n\n{summary}", border_style="green"))
    else:
        print(f"\n清理完成\n{summary}")

    _self_clean(script_dir, USE_RICH)


def _self_clean(script_dir: Path, use_rich: bool):
    """删除 delete_list.txt、apply_sync.py、apply_sync.bat 自身"""
    targets = ["delete_list.txt", "apply_sync.py", "apply_sync.bat", "sync_manifest.json"]
    cleaned = []
    for name in targets:
        p = script_dir / name
        try:
            if p.exists():
                p.unlink()
                cleaned.append(name)
        except OSError:
            pass
    if cleaned:
        msg = "已自动清理: " + ", ".join(cleaned)
        if use_rich:
            from rich.console import Console
            Console().print(f"[dim]{msg}[/dim]")
        else:
            print(msg)


if __name__ == "__main__":
    main()
'''

APPLY_SYNC_BAT = """\
@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PYTHON=%ROOT%\\.venv\\Scripts\\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%ROOT%\\apply_sync.py" "%ROOT%"
pause
endlocal
"""


def embed_apply_sync(temp_dir: str):
    py_path = os.path.join(temp_dir, "apply_sync.py")
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(APPLY_SYNC_SCRIPT)
    bat_path = os.path.join(temp_dir, "apply_sync.bat")
    with open(bat_path, "w", encoding="ascii") as f:
        f.write(APPLY_SYNC_BAT)


# === 7z 打包（内容平铺，无多余根目录）===

def run_7z_pack(seven_zip: str, temp_dir: str, output_path: str, volume_size: str) -> bool:
    cmd = [
        seven_zip, "a", "-t7z", "-r",
        "-y",               # 自动确认所有提示，不阻塞终端
        f"-v{volume_size}",
        output_path,
        ".",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=temp_dir)
    if result.returncode != 0:
        print(f"7z 打包失败:\n{result.stderr}", file=sys.stderr)
        return False
    return True


# === 云端清单存档（用完后移到 sync-tools/rm）===

def archive_cloud_manifest(manifest_path: str, archive_dir: str, timestamp: str):
    """将 manifest.json.xz 移入存档目录，同时从原位置删除（已不需要）。"""
    os.makedirs(archive_dir, exist_ok=True)
    dst = os.path.join(archive_dir, f"cloud_manifest_{timestamp}.json.xz")
    if manifest_path.endswith(".xz") and os.path.exists(manifest_path):
        shutil.move(manifest_path, dst)
    elif os.path.exists(manifest_path):
        shutil.copy2(manifest_path, dst)


# === 分卷阈值解析 ===

def parse_volume_size(size_str: str) -> int:
    size_str = size_str.strip().lower()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if size_str[-1] in multipliers:
        return int(float(size_str[:-1]) * multipliers[size_str[-1]])
    return int(size_str)


# === 主流程 ===

def main():
    parser = argparse.ArgumentParser(description="读取云端清单，差异比对，生成增量同步压缩包")
    parser.add_argument("local_dir", help="本地项目目录路径")
    parser.add_argument("manifest", help="云端 manifest.json 或 manifest.json.xz 文件路径")
    parser.add_argument("--hash-check", action="store_true",
                        help="对疑似差异文件做哈希验证（使用云端清单中记录的算法）")
    parser.add_argument("--volume-size", default="1g",
                        help="分卷阈值（默认 1g），如 500m, 1g, 2g")
    parser.add_argument("--dry-run", action="store_true",
                        help="只生成差异报告，不复制文件、不打包")
    parser.add_argument("--keep-temp", action="store_true",
                        help="打包后保留临时目录")
    args = parser.parse_args()

    local_dir = os.path.abspath(args.local_dir)
    manifest_path = os.path.abspath(args.manifest)
    volume_size_bytes = parse_volume_size(args.volume_size)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    temp_dir           = str(TEMP_DIR / f"sync_{timestamp}")
    file_dir           = str(FILE_DIR)
    report_dir         = str(FILE_DIR / "reports")
    manifest_archive_dir = str(MANIFESTS_DIR)

    # Step 1: 检测 7z
    seven_zip = None
    if not args.dry_run:
        seven_zip = find_7z()
        if seven_zip is None:
            print("错误：未找到 7-Zip 命令行工具。", file=sys.stderr)
            print("请安装 7-Zip: https://www.7-zip.org/", file=sys.stderr)
            print("常见安装路径: C:\\Program Files\\7-Zip\\7z.exe", file=sys.stderr)
            sys.exit(1)
        print(f"7-Zip 路径: {seven_zip}")

    # Step 2: 读取云端清单
    print(f"读取云端清单: {manifest_path}")
    try:
        cloud_manifest = load_cloud_manifest(manifest_path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"错误：云端清单格式无效 - {e}", file=sys.stderr)
        sys.exit(1)
    total_cloud = len(cloud_manifest["files"])
    hash_algo_info = cloud_manifest.get("hash_algo") or "（未启用）"
    print(f"  云端文件数: {total_cloud}  hash算法: {hash_algo_info}")

    # Step 3: 扫描本地目录
    print(f"扫描本地目录: {local_dir}")
    local_files, scan_errors = scan_directory(local_dir)
    total_local = len(local_files)
    print(f"  本地文件数: {total_local}")
    if scan_errors:
        print(f"  扫描错误: {len(scan_errors)} 个文件跳过")

    # Step 4: 差异比对
    print("执行差异比对...")
    diff_result = compare_files(local_files, cloud_manifest,
                                hash_check=args.hash_check, local_dir=local_dir)
    diff_count = len(diff_result["new_files"]) + len(diff_result["updated_files"])
    all_diff = diff_result["new_files"] + diff_result["updated_files"]
    diff_size = sum(f["size"] for f in all_diff)

    print(f"\n比对结果:")
    print(f"  新增文件: {len(diff_result['new_files'])}")
    print(f"  更新文件: {len(diff_result['updated_files'])}")
    print(f"  待删除文件（云端有/本机无）: {len(diff_result['deleted_files'])}")
    print(f"  待删除目录: {len(diff_result.get('deleted_dirs', []))}")
    print(f"  跳过文件: {len(diff_result['skipped_files'])}")
    print(f"  差异总大小: {human_readable_size(diff_size)}")

    # Step 5: 生成差异报告
    report, report_json = generate_reports(
        diff_result, local_dir, manifest_path,
        total_local, total_cloud, scan_errors,
        volume_size_bytes, report_dir, timestamp,
    )
    print(f"\n差异报告: {report_json}")

    # Step 6: 差异为零（含删除）
    has_deletes = len(diff_result["deleted_files"]) > 0 or len(diff_result.get("deleted_dirs", [])) > 0
    if diff_count == 0 and not has_deletes:
        print("\n没有需要同步的文件，退出。")
        return

    # Step 7: dry-run
    if args.dry_run:
        print("\n[dry-run] 仅生成报告，不复制文件、不打包。")
        return

    # Step 8: 复制差异文件
    print(f"\n复制差异文件到: {temp_dir}")
    os.makedirs(temp_dir, exist_ok=True)
    copy_errors = []
    if diff_count > 0:
        copy_errors = copy_diff_files(diff_result, local_dir, temp_dir)
        if copy_errors:
            print(f"  复制错误: {len(copy_errors)} 个文件跳过")

    # Step 9: 包内元数据
    write_sync_manifest(temp_dir, local_dir, diff_result, args.hash_check)

    # Step 10: 写入 delete_list.txt 和 apply_sync 套件
    if has_deletes:
        del_dirs = diff_result.get("deleted_dirs", [])
        write_delete_list(temp_dir, diff_result["deleted_files"], del_dirs)
        print(f"  delete_list.txt: {len(diff_result['deleted_files'])} 个文件，{len(del_dirs)} 个目录")
    embed_apply_sync(temp_dir)

    # Step 11: 7z 打包
    output_7z = os.path.join(file_dir, f"sync_{timestamp}.7z")
    print(f"\n打包中...")
    if diff_size > volume_size_bytes:
        print(f"  差异大小 ({human_readable_size(diff_size)}) 超过分卷阈值 ({args.volume_size})，将自动分卷")
    os.makedirs(file_dir, exist_ok=True)
    if not run_7z_pack(seven_zip, temp_dir, output_7z, args.volume_size):
        print("打包失败，临时目录已保留供排查。", file=sys.stderr)
        sys.exit(1)

    # Step 12: 存档云端清单
    archive_cloud_manifest(manifest_path, manifest_archive_dir, timestamp)
    print(f"  云端清单已存档到: {manifest_archive_dir}")

    # Step 13: 清理临时目录
    if args.keep_temp:
        print(f"  临时目录已保留: {temp_dir}")
    else:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"  临时目录已清理")

    print(f"\n同步包准备完成！")
    print(f"请将以下文件上传到云端并解压到项目根目录:")
    output_base = f"sync_{timestamp}.7z"
    for fname in sorted(os.listdir(file_dir)):
        if fname.startswith(f"sync_{timestamp}") and ".7z" in fname:
            fpath = os.path.join(file_dir, fname)
            print(f"  {fpath}  ({human_readable_size(os.path.getsize(fpath))})")

    if has_deletes:
        print(f"\n解压后，在云端项目根目录运行以下命令处理删除:")
        print(f"  python apply_sync.py . <云端项目根目录>")
        print(f"  (apply_sync.py 已打包在压缩包根目录内)")


if __name__ == "__main__":
    main()
