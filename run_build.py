"""本机增量打包交互脚本（由 本机打包.bat 调用）"""
import json
import lzma
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_common import (
    scan_directory, parse_mtime, compute_hash,
    human_readable_size, quick_scan,
)
from build_sync_package import (
    find_7z, load_cloud_manifest, compare_files,
    generate_reports, write_sync_manifest,
    write_delete_list, embed_apply_sync,
    run_7z_pack, archive_cloud_manifest, parse_volume_size,
)
from config import ROOT, TEMP_DIR, FILE_DIR, MANIFESTS_DIR, SYNC_TOOLS_DIR

from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn,
    TransferSpeedColumn, FileSizeColumn, TotalFileSizeColumn,
    MofNCompleteColumn,
)
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

def ask(prompt: str, choices: list[str], default: str) -> str:
    opts = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        ans = input(f"{prompt} [{opts}]: ").strip().lower()
        if ans == "":
            return default
        if ans in choices:
            return ans
        console.print(f"  [yellow]请输入 {' 或 '.join(choices)}[/yellow]")


def scan_with_progress(root: Path) -> tuple:
    """快速扫描本地文件（不含 hash），带 rich 进度条。"""
    # 先快速数文件数
    from sync_common import normalize_path, should_ignore_dir, should_ignore_file
    total = sum(
        1
        for dp, dns, fns in os.walk(root)
        for fn in fns
        if not should_ignore_file(fn)
        and not should_ignore_dir(
            normalize_path(
                os.path.join(os.path.relpath(dp, root), fn)
            ).rsplit("/", 1)[0] or ""
        )
    )

    files_done = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("扫描本地文件...", total=total)

        def on_file(rel_path: str, size: int):
            nonlocal files_done
            files_done += 1
            short = rel_path if len(rel_path) <= 50 else "..." + rel_path[-47:]
            prog.update(task, advance=1, description=short)

        file_list, errors = scan_directory(str(root), on_file=on_file)

    return file_list, errors


def copy_with_progress(diff_result: dict, local_dir: str, temp_dir: str) -> list:
    all_diff = diff_result["new_files"] + diff_result["updated_files"]
    total_bytes = sum(f["size"] for f in all_diff)
    copy_errors = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        FileSizeColumn(),
        TextColumn("/"),
        TotalFileSizeColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,   # 完成后整条进度条消失，不残留在屏幕上
    ) as prog:
        task = prog.add_task("复制差异文件...", total=total_bytes)

        for entry in all_diff:
            rel_path = entry["path"]
            src = os.path.join(local_dir, rel_path.replace("/", os.sep))
            dst = os.path.join(temp_dir, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            short = rel_path if len(rel_path) <= 45 else "..." + rel_path[-42:]
            prog.update(task, description=short)
            try:
                shutil.copy2(src, dst)
                prog.update(task, advance=entry["size"])
            except (PermissionError, OSError) as e:
                copy_errors.append({"path": rel_path, "error": str(e)})
                prog.update(task, advance=entry["size"])

    return copy_errors


def show_diff_table(diff_result: dict):
    def make_table(title: str, color: str, rows: list, show_reason: bool = False):
        if not rows:
            return
        t = Table(title=title, box=box.SIMPLE_HEAVY, title_style=f"bold {color}",
                  show_header=True, header_style="bold dim")
        t.add_column("路径", style="dim", max_width=70)
        t.add_column("大小", justify="right", style=color)
        if show_reason:
            t.add_column("原因", style="yellow")
        for r in rows[:30]:
            row = [r["path"], human_readable_size(r.get("size", 0))]
            if show_reason:
                row.append(r.get("reason", ""))
            t.add_row(*row)
        if len(rows) > 30:
            t.add_row(f"... 共 {len(rows)} 个", "", *([""] if show_reason else []))
        console.print(t)

    make_table("新增文件", "green", diff_result["new_files"])
    make_table("更新文件", "yellow", diff_result["updated_files"], show_reason=True)

    del_files = diff_result.get("deleted_files", [])
    del_dirs  = diff_result.get("deleted_dirs", [])
    if del_files or del_dirs:
        t = Table(title="待删除（云端有 / 本机无）", box=box.SIMPLE_HEAVY,
                  title_style="bold red", show_header=True, header_style="bold dim")
        t.add_column("类型", style="dim", width=4)
        t.add_column("路径", style="dim", max_width=78)
        for r in del_files[:25]:
            t.add_row("文件", r["path"])
        if len(del_files) > 25:
            t.add_row("", f"... 共 {len(del_files)} 个文件")
        for d in del_dirs[:15]:
            t.add_row("[yellow]目录[/yellow]", d)
        if len(del_dirs) > 15:
            t.add_row("", f"... 共 {len(del_dirs)} 个目录")
        console.print(t)


def main():
    manifest_arg = sys.argv[1] if len(sys.argv) > 1 else None

    os.system("cls")
    console.print(Panel.fit(
        f"[bold cyan]本机增量打包工具[/bold cyan]\n[dim]项目: {ROOT}[/dim]",
        border_style="cyan"
    ))
    console.print()

    # 检测 7z
    seven_zip = find_7z()
    if seven_zip is None:
        console.print("[red][错误] 未找到 7-Zip，请安装: https://www.7-zip.org/[/red]")
        return

    # 获取 manifest
    if manifest_arg:
        manifest_path = Path(manifest_arg.strip('"'))
    else:
        console.print("[dim]提示：下次可将 manifest.json.xz 直接拖到 bat 图标上[/dim]\n")
        raw = input("请输入云端清单路径 (.json 或 .json.xz): ").strip().strip('"')
        manifest_path = Path(raw)

    if not manifest_path.exists():
        console.print(f"[red][错误] 文件不存在: {manifest_path}[/red]")
        return

    # 读取云端清单
    console.print(f"\n读取云端清单: [cyan]{manifest_path.name}[/cyan]")
    try:
        cloud_manifest = load_cloud_manifest(str(manifest_path))
    except Exception as e:
        console.print(f"[red][错误] {e}[/red]")
        return
    total_cloud = len(cloud_manifest["files"])
    hash_info = cloud_manifest.get("hash_algo") or "未启用"
    console.print(f"  云端: [cyan]{total_cloud}[/cyan] 个文件  hash: {hash_info}\n")

    # 扫描本机
    local_files, scan_errors = scan_with_progress(ROOT)
    if scan_errors:
        console.print(f"  [yellow]扫描错误: {len(scan_errors)} 个文件跳过[/yellow]")

    # 差异比对
    console.print("\n比对差异...")
    diff = compare_files(local_files, cloud_manifest, local_dir=str(ROOT))
    new_n  = len(diff["new_files"])
    upd_n  = len(diff["updated_files"])
    del_n  = len(diff["deleted_files"])
    del_dir_n = len(diff.get("deleted_dirs", []))
    skip_n = len(diff["skipped_files"])
    diff_size = sum(f["size"] for f in diff["new_files"] + diff["updated_files"])

    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column("项目", style="bold")
    summary.add_column("数量", justify="right")
    summary.add_row("[green]新增[/green]", str(new_n))
    summary.add_row("[yellow]更新[/yellow]", str(upd_n))
    summary.add_row("[red]待删除文件[/red]", str(del_n))
    if del_dir_n:
        summary.add_row("[red]待删除目录[/red]", str(del_dir_n))
    summary.add_row("[dim]跳过[/dim]", str(skip_n))
    summary.add_row("[cyan]差异大小[/cyan]", human_readable_size(diff_size))
    console.print(summary)

    if new_n == 0 and upd_n == 0 and del_n == 0 and del_dir_n == 0:
        console.print("[green]两端完全一致，无需同步。[/green]")
        return

    # 自动分卷：超过 1 GB 才分卷，否则单文件
    ONE_GB = 1024 ** 3
    vol_str    = "1g" if diff_size > ONE_GB else str(diff_size + 64 * 1024 * 1024)
    do_split   = diff_size > ONE_GB
    volume_size_bytes = parse_volume_size(vol_str)

    # 预览
    do_preview = ask("\n预览详细差异列表", ["y", "n"], "y") == "y"
    if do_preview:
        console.print()
        show_diff_table(diff)

    go = ask("确认打包", ["y", "n"], "y")
    if go != "y":
        console.print("已取消。")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_dir   = TEMP_DIR / f"sync_{timestamp}"
    report_dir = FILE_DIR / "reports"
    manifest_archive_dir = MANIFESTS_DIR

    # 差异报告（内部留档，完成后清理）
    generate_reports(
        diff, str(ROOT), str(manifest_path),
        len(local_files), total_cloud, scan_errors,
        volume_size_bytes, str(report_dir), timestamp,
    )

    # 复制差异文件
    console.print()
    temp_dir.mkdir(parents=True, exist_ok=True)
    copy_errors = copy_with_progress(diff, str(ROOT), str(temp_dir))
    if copy_errors:
        console.print(f"  [yellow]复制错误: {len(copy_errors)} 个[/yellow]")

    # 元数据
    write_sync_manifest(str(temp_dir), str(ROOT), diff, False)
    has_deletes = del_n > 0 or del_dir_n > 0
    if has_deletes:
        write_delete_list(str(temp_dir), diff["deleted_files"], diff.get("deleted_dirs", []))
    embed_apply_sync(str(temp_dir))

    # 7z 打包 —— 输出到 sync-tools 目录，和 bat 同级方便拷走
    output_7z = str(SYNC_TOOLS_DIR / f"sync_{timestamp}.7z")
    if do_split:
        console.print(f"\n[bold]7z 打包[/bold]（差异 {human_readable_size(diff_size)} > 1 GB，自动 1g 分卷）...")
    else:
        console.print(f"\n[bold]7z 打包[/bold]（单文件，{human_readable_size(diff_size)}）...")
    ok = run_7z_pack(seven_zip, str(temp_dir), output_7z, vol_str)
    if not ok:
        console.print("[red][错误] 打包失败，临时目录已保留。[/red]")
        return

    # 清理：存档 manifest、删除临时目录、清理 reports
    archive_cloud_manifest(str(manifest_path), str(manifest_archive_dir), timestamp)
    shutil.rmtree(str(temp_dir), ignore_errors=True)
    if report_dir.exists():
        shutil.rmtree(str(report_dir), ignore_errors=True)

    # 列出输出文件（在 sync-tools 目录）
    out_files = sorted(
        f for f in os.listdir(str(SYNC_TOOLS_DIR))
        if f.startswith(f"sync_{timestamp}") and ".7z" in f
    )
    result_lines = "\n".join(
        f"  [cyan]{SYNC_TOOLS_DIR / f}[/cyan]  ({human_readable_size((SYNC_TOOLS_DIR / f).stat().st_size)})"
        for f in out_files
    )
    extra = ""
    if has_deletes:
        extra = (
            f"\n\n[dim]包内含待删除清单（文件 {del_n} 个，目录 {del_dir_n} 个）[/dim]\n"
            "[dim]解压后进入[/dim] [cyan]_apply_sync/[/cyan] [dim]文件夹，双击[/dim] [cyan]apply_sync.bat[/cyan] [dim]即可自动处理，完成后自删该文件夹[/dim]"
        )

    console.print(Panel(
        f"[green]打包完成！[/green]\n\n"
        f"请上传到云端并解压到项目根目录:\n{result_lines}{extra}",
        title="完成",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
