"""云端清单生成脚本（命令行版本，适合脚本调用）。
扫描目录并输出 manifest.json.xz 到该目录，供本机打包时使用。
交互版本参见 run_generate.py（由 云端生成清单.bat 调用）。
"""

import argparse
import json
import lzma
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.sync.sync_common import scan_directory, default_hash_algo


def main():
    parser = argparse.ArgumentParser(
        description="扫描项目目录并生成 manifest.json.xz"
    )
    parser.add_argument("target_dir", help="要扫描的目录")
    parser.add_argument(
        "--hash", dest="enable_hash", action="store_true",
        help="Enable hash (default: xxh3_64 if xxhash installed, else sha256)"
    )
    parser.add_argument(
        "--hash-algo", dest="hash_algo", default=None,
        help="Hash algorithm: xxh3_64 (default), xxh128, sha256"
    )

    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f"Error: directory does not exist: {args.target_dir}", file=sys.stderr)
        sys.exit(1)

    target_dir_abs = os.path.abspath(args.target_dir)
    hash_algo = args.hash_algo or default_hash_algo()

    files, errors = scan_directory(target_dir_abs, enable_hash=args.enable_hash,
                                   hash_algo=hash_algo)

    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "root_dir": target_dir_abs,
        "hash_enabled": args.enable_hash,
        "hash_algo": hash_algo if args.enable_hash else None,
        "file_count": len(files),
        "files": files,
    }
    if errors:
        manifest["scan_errors"] = errors

    json_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    xz_path = os.path.join(target_dir_abs, "manifest.json.xz")
    with lzma.open(xz_path, "wb", preset=6) as f:
        f.write(json_bytes)

    print(f"扫描完成: {len(files)} 个文件", flush=True)
    if errors:
        print(f"警告: {len(errors)} 个文件跳过", flush=True)
    orig_kb = len(json_bytes) / 1024
    xz_kb = os.path.getsize(xz_path) / 1024
    print(f"manifest.json.xz: {xz_path}  ({orig_kb:.0f} KB -> {xz_kb:.0f} KB)", flush=True)


if __name__ == "__main__":
    main()
