"""云端清单生成交互脚本（由 云端生成清单.bat 调用）"""
import json
import lzma
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_common import (
    scan_directory, default_hash_algo,
    human_readable_size,
    normalize_path, should_ignore_dir, should_ignore_file,
)
from config import ROOT

from rich.console import Console

from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn,
    TransferSpeedColumn, FileSizeColumn, TotalFileSizeColumn,
)
from rich.panel import Panel

console = Console()


def ask(prompt: str, choices: list[str], default: str) -> str:
    opts = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        ans = input(f"{prompt} [{opts}]: ").strip().lower()
        if ans == "" :
            return default
        if ans in choices:
            return ans
        console.print(f"  [yellow]请输入 {' 或 '.join(choices)}[/yellow]")


def count_files(root: Path) -> tuple[int, int]:
    """返回 (文件数, 总字节数)"""
    total_files = 0
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        dirnames[:] = [
            d for d in dirnames
            if not should_ignore_dir(
                normalize_path(os.path.join(rel, d)) if rel != "." else d
            )
        ]
        for fn in filenames:
            if should_ignore_file(fn):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                total_bytes += os.path.getsize(fp)
                total_files += 1
            except OSError:
                pass
    return total_files, total_bytes


def main():
    os.system("cls")
    console.print(Panel.fit(
        f"[bold cyan]云端清单生成工具[/bold cyan]\n[dim]项目: {ROOT}[/dim]",
        border_style="cyan"
    ))
    console.print()

    use_hash = ask("启用 xxhash（更精确，大文件会慢一些）", ["y", "n"], "n") == "y"
    hash_algo = default_hash_algo() if use_hash else None
    mode_str = f"size + mtime + [green]{hash_algo}[/green]" if use_hash else "size + mtime"
    console.print(f"  模式: {mode_str}\n")

    console.print()

    # ── 阶段一：快速统计 ─────────────────────────────────────────
    console.print("[bold]阶段 1/2  统计文件...[/bold]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as prog:
        t = prog.add_task("正在统计文件数...", total=None)
        total_files, total_bytes = count_files(ROOT)
        prog.update(t, completed=total_files, total=total_files,
                    description=f"统计完成: {total_files} 个文件 / {human_readable_size(total_bytes)}")

    console.print(f"  共 [cyan]{total_files}[/cyan] 个文件，"
                  f"合计 [cyan]{human_readable_size(total_bytes)}[/cyan]\n")

    # ── 阶段二：扫描（含 hash 时按字节更新）──────────────────────
    console.print("[bold]阶段 2/2  扫描目录...[/bold]")

    files_done = 0
    current_file = ""

    if use_hash:
        # 按字节进度（含速度、ETA）
        progress_cols = [
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30),
            FileSizeColumn(),
            TextColumn("/"),
            TotalFileSizeColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
        ]
    else:
        # 按文件数进度
        progress_cols = [
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ]

    with Progress(*progress_cols, console=console) as prog:
        if use_hash:
            task = prog.add_task("扫描中...", total=total_bytes, completed=0)
        else:
            task = prog.add_task("扫描中...", total=total_files, completed=0)

        def on_file(rel_path: str, size: int):
            nonlocal files_done, current_file
            files_done += 1
            current_file = rel_path
            short = rel_path if len(rel_path) <= 50 else "..." + rel_path[-47:]
            if not use_hash:
                prog.update(task, advance=1,
                            description=f"[{files_done}/{total_files}] {short}")
            else:
                prog.update(task,
                            description=f"[{files_done}/{total_files}] {short}")

        def on_bytes(n: int):
            prog.update(task, advance=n)

        file_list, errors = scan_directory(
            str(ROOT),
            enable_hash=use_hash,
            hash_algo=hash_algo,
            on_file=on_file,
            on_bytes=on_bytes if use_hash else None,
        )

    console.print()

    # ── 写出文件（xz 直接放项目根目录，方便拷贝）────────────────
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    manifest = {
        "generated_at": generated_at,
        "root_dir": str(ROOT),
        "hash_enabled": use_hash,
        "hash_algo": hash_algo if use_hash else None,
        "file_count": len(file_list),
        "files": file_list,
    }
    if errors:
        manifest["scan_errors"] = errors

    json_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    xz_path = ROOT / "manifest.json.xz"
    with lzma.open(xz_path, "wb", preset=6) as f:
        f.write(json_bytes)

    orig_kb = len(json_bytes) / 1024
    xz_kb   = xz_path.stat().st_size / 1024

    console.print(Panel(
        f"[green]扫描完成[/green]  {len(file_list)} 个文件"
        + (f"，[yellow]{len(errors)} 个错误[/yellow]" if errors else "") + "\n\n"
        f"[bold cyan]manifest.json.xz[/bold cyan]  {orig_kb:.0f} KB → {xz_kb:.0f} KB\n\n"
        f"[bold]文件位置（直接拷贝）:[/bold]\n  [cyan]{xz_path}[/cyan]",
        title="完成",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
