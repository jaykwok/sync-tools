# sync-tools — 手动增量同步工具

在**无法建立网络直连**的场景下（如本机内网、云桌面无公网 IP），通过"生成差异包 → 手动上传 → 解压覆盖"实现增量同步。

适用于：两端都是 Windows、文件量大（含 Office / 二进制文件）、上传带宽有限、不能安装 rsync/VPN 的情况。

---

## 工作原理

```
云端：扫描目录 → manifest.json.xz  ──(拷贝到本机)──►
本机：读清单 + 扫描本地 → 比对差异 → sync_<时间戳>.7z  ──(上传)──►
云端：解压覆盖 + apply_sync.bat 处理删除
```

比对逻辑：

| 情况 | 判定 |
|------|------|
| 本机有、云端无 | 新增 |
| size 或 mtime 不同 | 更新 |
| 完全一致 | 跳过 |
| 云端有、本机无 | 待删除（写入 `delete_list.txt`） |

---

## 快速开始

### 1. 部署（两端各做一次）

```bat
REM 在项目根目录运行，自动检测 Python / xxhash / rich / 7-Zip 并安装缺失依赖
.venv\Scripts\python.exe sync-tools\setup_sync.py
```

依赖项：

| 依赖 | 说明 |
|------|------|
| Python 3.11+ | 两端均需（推荐 3.13） |
| [xxhash](https://pypi.org/project/xxhash/) | 可选，启用后比对更精确且比 SHA-256 快 5-10 倍 |
| [rich](https://pypi.org/project/rich/) | 进度条显示 |
| [7-Zip](https://www.7-zip.org/) | 本机需安装；云端解压用系统自带 `7z` 即可 |

### 2. 云端：生成清单

双击 `sync-tools\云端生成清单.bat`

脚本会询问是否启用 xxhash（默认 `n`，快速模式）。  
完成后在**项目根目录**生成 `manifest.json.xz`，将其拷贝到本机。

### 3. 本机：生成增量包

双击 `sync-tools\本机打包.bat`，或将 `manifest.json.xz` 直接**拖到** bat 图标上。

脚本会：

1. 读取云端清单，扫描本机目录
2. 显示差异汇总（新增 / 更新 / 待删除）
3. 可选预览详细差异列表
4. 确认后打包（差异 > 1 GB 自动分卷）
5. 输出 `sync_<时间戳>.7z` 到**项目根目录**

将所有分卷上传到云端（如有分卷：`.7z.001` `.7z.002` ...）。

### 4. 云端：解压覆盖

```bat
7z x sync_<时间戳>.7z -o<项目根目录路径> -y
REM 例如：
7z x sync_20260420_103000.7z -oD:\MyProject -y
```

### 5. 云端：处理删除（如有）

如果本机有删除文件，压缩包内会含 `delete_list.txt` 和 `apply_sync.bat`。  
解压后在解压目录**双击 `apply_sync.bat`** 即可。

被删除的文件移入 `sync-tools\rm\` 软删除（不永久丢失）。  
脚本执行完自动清理自身。

---

## 配置

### `.env` 配置项

复制 `sync-tools\.env.example` 为 `sync-tools\.env` 并按需修改（`.env` 不提交 git）：

```ini
# 项目根目录（可选，默认自动推断：sync-tools 的上一级）
# ROOT=D:/MyProject

# Python 虚拟环境路径（相对于根目录，或绝对路径）
VENV_PYTHON=.venv/Scripts/python.exe

# 临时打包目录
TEMP_DIR=sync-tools/temp

# 软删除目录
RM_DIR=sync-tools/rm

# 输出目录（清单存档）
FILE_DIR=sync-tools/file_history

# 清单存档子目录（相对于 FILE_DIR）
MANIFESTS_SUBDIR=manifests

# 7-Zip 搜索路径（逗号分隔）
SEVEN_ZIP_EXTRA=C:/Program Files/7-Zip/7z.exe,C:/Program Files (x86)/7-Zip/7z.exe
```

> **不同机器适配**：如果 `sync-tools` 不在项目根目录下，取消注释 `ROOT=` 行并填绝对路径，其余相对路径仍以该 ROOT 为基准。

### 忽略规则（`.syncignore`）

在**项目根目录**创建 `.syncignore`（若不存在，使用内置默认值）：

```
# 井号开头为注释
dir:.venv           # 忽略名为 .venv 的目录（任意层级）
dir:sync-tools      # 忽略 sync-tools 目录（含 temp/rm/file_history）
file:*.log
file:Thumbs.db
```

---

## 命令行用法（高级）

```bat
REM 云端生成清单（带 xxhash）
python sync-tools\generate_manifest.py . --hash

REM 本机 dry-run（只看差异，不打包）
.venv\Scripts\python.exe sync-tools\build_sync_package.py . manifest.json.xz --dry-run

REM 本机打包（500m 分卷）
.venv\Scripts\python.exe sync-tools\build_sync_package.py . manifest.json.xz --volume-size 500m

REM 本机打包（带 hash 二次校验）
.venv\Scripts\python.exe sync-tools\build_sync_package.py . manifest.json.xz --hash-check
```

### generate_manifest.py 参数

| 参数 | 说明 |
|------|------|
| `target_dir` | 扫描目录（`.` 表示当前目录） |
| `--hash` | 启用文件 hash |
| `--hash-algo` | 算法：`xxh3_64`（默认）/ `xxh128` / `sha256` |
| `--output DIR` | 输出目录（默认当前目录） |
| `--no-xz` | 不生成 .xz 压缩版 |

### build_sync_package.py 参数

| 参数 | 说明 |
|------|------|
| `local_dir` | 本地目录（`.` 表示当前目录） |
| `manifest` | 云端清单路径（.json 或 .json.xz） |
| `--hash-check` | 对疑似差异文件做 hash 二次验证 |
| `--volume-size` | 分卷大小，如 `500m` `1g`（差异 > 1 GB 自动分卷） |
| `--dry-run` | 只看差异报告，不打包 |
| `--keep-temp` | 保留临时目录（排查用） |

---

## 文件结构

```
<项目根>/
├── .syncignore               ← 忽略规则（自行创建/修改）
├── .gitignore
├── manifest.json.xz          ← 云端生成后拷到这里（打包后自动存档）
├── sync_<时间戳>.7z          ← 本机生成的增量包（上传后可删除）
└── sync-tools/
    ├── .env                  ← 本地路径配置（不提交 git）
    ├── .env.example          ← 配置模板（提交 git）
    ├── temp/                 ← 打包临时目录（自动清理）
    ├── rm/                   ← 软删除目录
    ├── file_history/
    │   └── manifests/
    │       └── cloud_manifest_<时间戳>.json.xz
    ├── sync_common.py
    ├── config.py
    ├── generate_manifest.py
    ├── run_generate.py
    ├── build_sync_package.py
    ├── run_build.py
    ├── setup_sync.py
    ├── 云端生成清单.bat
    ├── 本机打包.bat
    └── README.md
```

---

## 常见问题

**Q: 云端 CPU 弱，生成清单很慢？**  
A: 默认模式（不启用 xxhash）只读 mtime + size，速度极快。xxhash 只在需要精确校验时使用。

**Q: 上传到一半断了怎么办？**  
A: 差异 > 1 GB 时自动分卷（每卷 1 GB），只需重传未完成的卷。

**Q: 想把 sync-tools 放到其他位置？**  
A: 在 `sync-tools\.env` 中添加 `ROOT=<项目根绝对路径>`，其余配置不变。

**Q: 两端 Python 版本不同？**  
A: 最低要求 Python 3.11。云端只需标准库 + xxhash + rich，无其他依赖。
