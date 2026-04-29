"""
云端同步清理脚本
按 delete_list.txt 处理移动项，并将删除项移动到当前项目的 sync-tools/rm/。
由 apply_sync.bat 自动调用，无需手动传参。
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def parse_delete_list(path: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    files, dirs, moves = [], [], []
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
            elif line == "[moves]":
                section = "moves"
            elif section == "files":
                files.append(line)
            elif section == "dirs":
                dirs.append(line)
            elif section == "moves":
                if " -> " in line:
                    old, new = line.split(" -> ", 1)
                    moves.append((old.strip(), new.strip()))
            else:
                files.append(line)
    return files, dirs, moves


def resolve_rm_dir(rm_dir: str | None, project_root: Path) -> Path:
    candidate = Path((rm_dir or "sync-tools/rm").replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"sync_manifest.json 中的 rm_dir 必须是项目内相对路径: {rm_dir}")
    return project_root / candidate


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

    del_files, del_dirs, moves = parse_delete_list(str(delete_list_path))

    rm_base_path = project_root / "sync-tools" / "rm"
    sync_manifest_path = script_dir / "sync_manifest.json"
    if sync_manifest_path.exists():
        try:
            import json as _json
            meta = _json.loads(sync_manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
        else:
            rm_base_path = resolve_rm_dir(meta.get("rm_dir"), project_root)
    rm_base = rm_base_path / ("sync_delete_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    rm_base.mkdir(parents=True, exist_ok=True)

    file_moved = file_skipped = dir_moved = dir_skipped = 0
    move_done = move_skipped = move_conflict = 0

    # ── 先执行移动（避免父目录被删除后源文件丢失）─────────────────
    if moves:
        if USE_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=35),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console, transient=True,
            ) as prog:
                task = prog.add_task("执行移动...", total=len(moves))
                for old_rel, new_rel in moves:
                    src = project_root / Path(old_rel.replace("/", os.sep))
                    dst = project_root / Path(new_rel.replace("/", os.sep))
                    prog.update(task, description=new_rel[-50:] if len(new_rel) > 50 else new_rel)
                    if not src.exists():
                        move_skipped += 1
                        prog.update(task, advance=1)
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        conflict_dst = rm_base / Path(new_rel.replace("/", os.sep))
                        conflict_dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dst), str(conflict_dst))
                        move_conflict += 1
                    shutil.move(str(src), str(dst))
                    move_done += 1
                    prog.update(task, advance=1)
        else:
            for old_rel, new_rel in moves:
                src = project_root / Path(old_rel.replace("/", os.sep))
                dst = project_root / Path(new_rel.replace("/", os.sep))
                if not src.exists():
                    move_skipped += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    conflict_dst = rm_base / Path(new_rel.replace("/", os.sep))
                    conflict_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(conflict_dst))
                    move_conflict += 1
                    print(f"  [移动冲突] 原目标已软删: {new_rel}")
                shutil.move(str(src), str(dst))
                move_done += 1
                print(f"  [移动] {old_rel} -> {new_rel}")

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
        f"移动: 成功 {move_done} / 冲突软删 {move_conflict} / 跳过 {move_skipped}\n"
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
