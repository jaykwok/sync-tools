"""
云端增量同步清理脚本
将 delete_list.txt 中的文件/目录移动到 sync-tools/rm/，完成后自删 _apply_sync 文件夹。
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
                files.append(line)
    return files, dirs


def main():
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    if len(sys.argv) >= 2:
        project_root = Path(sys.argv[1]).resolve()

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
    try:
        shutil.rmtree(str(script_dir))
        msg = f"已自动清理: {script_dir.name}/"
    except OSError as e:
        msg = f"清理失败: {e}"
    if use_rich:
        from rich.console import Console
        Console().print(f"[dim]{msg}[/dim]")
    else:
        print(msg)


if __name__ == "__main__":
    main()
