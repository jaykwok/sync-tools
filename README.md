# sync-tools — 手动同步工具

在**无法建立网络直连**的场景下（如本机内网、云桌面无公网 IP），通过"生成差异包 → 手动上传 → 解压覆盖"实现手动同步。支持默认的**完全镜像**模式，也支持只新增/更新的**增量更新**模式。

适用于：两端都是 Windows、文件量大（含 Office / 二进制文件）、上传带宽有限、不能安装 rsync/VPN 的情况。

---

## 工作原理

```
云端：扫描目录 → manifest.json.xz  ──(拷贝到本机)──►
本机：读清单 + 扫描本地 → 比对差异 → sync_<时间戳>.7z  ──(上传)──►
云端：解压覆盖 + apply_sync.bat 处理删除/移动（镜像模式如有）
```

比对逻辑：

| 情况 | 判定 |
|------|------|
| 本机有、云端无 | 新增 |
| size 或 mtime 不同 | 更新 |
| 完全一致 | 跳过 |
| 云端有、本机无 | 镜像模式下待删除（写入 `delete_list.txt`）；增量更新模式下保留 |
| 路径变更、内容相同 | 镜像模式下记录为移动；增量更新模式下按新文件上传 |

---

## 环境准备

**两端均需完成以下步骤。**

### 系统要求

| 项目 | 要求 |
|------|------|
| Windows | 10 / 11 / Server 2019+ |
| Python | **3.11 或以上**（推荐 3.13） |
| 7-Zip | 本机打包端必须安装；云端解压用系统自带或 7-Zip 均可 |

Python 下载：https://www.python.org/downloads/  
7-Zip 下载：https://www.7-zip.org/

### 创建虚拟环境并安装依赖

在**项目根目录**（`sync-tools` 的上一级）执行：

```bat
REM 创建虚拟环境（仅首次）
python -m venv .venv

REM 安装依赖
.venv\Scripts\pip install xxhash rich
```

> **云端注意**：云端生成清单和处理同步时同样使用 `.venv`，需要安装 `xxhash rich`。
> 如果云端无法访问 PyPI，可在本机安装后将整个 `.venv` 目录打包传过去。

### 运行部署检测（可选）

安装完成后，运行检测脚本确认环境就绪：

```bat
.venv\Scripts\python.exe sync-tools\setup_sync.py
```

脚本会自动检测 Python 版本、`.venv`、xxhash、rich、7-Zip 是否正常，并创建必要的工作目录。

---

## 快速开始

### 步骤 1：初始化配置

将 `sync-tools\.env.example` 复制为 `sync-tools\.env`（两端各自操作，`.env` 不提交 git）：

```bat
copy sync-tools\.env.example sync-tools\.env
```

通常不需要修改，默认配置即可工作。如需自定义路径，参见下方[配置](#配置)章节。

将 `sync-tools\.syncignore.example` 复制到**项目根目录**并重命名：

```bat
copy sync-tools\.syncignore.example .syncignore
```

按需编辑 `.syncignore`，将不需要同步的目录和文件加入忽略规则。

### 步骤 2：云端生成清单

在云端项目根目录，双击 `sync-tools\云端生成清单.bat`。

脚本固定使用 `XXH3` 生成清单，用于准确识别文件移动和内容一致性。

完成后在 `sync-tools\` 目录（与 bat 同级）生成 `manifest.json.xz`，将其拷贝到本机。
脚本会询问是否保存到默认目录，选 `n` 可弹窗另存到其他位置。

### 步骤 3：本机生成同步包

双击 `sync-tools\本机打包.bat`，或将 `manifest.json.xz` 直接**拖到** bat 图标上。

脚本会：

1. 弹窗选择云端清单文件（拖入 bat 时跳过此步）
2. 读取云端清单，扫描本机目录
3. 选择打包模式：完全镜像（默认）或增量更新
4. 显示差异汇总（新增 / 更新 / 移动 / 待删除文件数及总大小）
5. 可选预览详细差异列表
6. 确认打包后询问输出路径（默认 `sync-tools\`，选 `n` 可弹窗另存）
7. 复制差异文件并打包（差异 > 1 GB 自动按 1 GB 分卷）
8. 输出 `sync_<时间戳>.7z` 到所选目录

将输出文件上传到云端（如有分卷：`.7z.001` `.7z.002` ...，需全部上传）。

### 步骤 4：云端解压覆盖

```bat
7z x sync_<时间戳>.7z -o<项目根目录路径> -y
REM 例如：
7z x sync_20260420_103000.7z -oD:\MyProject -y
```

### 步骤 5：云端处理删除/移动（镜像模式如有）

如果镜像模式下存在删除或移动项，压缩包内会含 `_apply_sync\` 子目录，里面包含 `apply_sync.bat` 等配套文件。
解压后进入 `_apply_sync\` 文件夹，**双击 `apply_sync.bat`** 即可。

- 被删除的文件移入 `sync-tools\rm\` 软删除，不会永久丢失
- 移动项会在云端就地重命名，不重新上传文件内容
- 脚本执行完毕后自动删除整个 `_apply_sync\` 文件夹
- 增量更新模式不会生成删除/移动指令，云端旧路径文件会保留

---

## 配置

### `.env` 配置项

```ini
# 项目根目录（可选，默认自动推断：sync-tools 的上一级）
# ROOT=D:/MyProject

# Python 虚拟环境路径（相对于根目录，或绝对路径）
VENV_PYTHON=.venv/Scripts/python.exe

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

放在**项目根目录**，若不存在则使用内置默认值。参见 `.syncignore.example` 了解完整格式说明。

```
dir:.venv           # 忽略名为 .venv 的目录（任意层级）
dir:sync-tools      # 忽略 sync-tools 目录（含 rm/ 及 file_history/）
file:*manifest*.json.xz
file:*.7z*
file:*.log
file:Thumbs.db
```

---

## 命令行用法（高级）

```bat
REM 云端生成清单（XXH3）
.venv\Scripts\python.exe sync-tools\core\generate\generate_manifest.py .

REM 本机 dry-run（只看差异，不打包）
.venv\Scripts\python.exe sync-tools\core\pack\build_sync_package.py . manifest.json.xz --dry-run

REM 本机完全镜像打包（500m 分卷）
.venv\Scripts\python.exe sync-tools\core\pack\build_sync_package.py . manifest.json.xz --volume-size 500m

REM 本机增量更新打包（只新增/更新，不删除/移动）
.venv\Scripts\python.exe sync-tools\core\pack\build_sync_package.py . manifest.json.xz --mode incremental
```

### generate_manifest.py 参数

| 参数 | 说明 |
|------|------|
| `target_dir` | 扫描目录（`.` 表示当前目录） |

清单固定使用 `XXH3`，需要在 `.venv` 中安装 `xxhash`。

### build_sync_package.py 参数

| 参数 | 说明 |
|------|------|
| `local_dir` | 本地目录（`.` 表示当前目录） |
| `manifest` | 云端清单路径（.json 或 .json.xz） |
| `--hash-check` | 对疑似差异文件做 hash 二次验证 |
| `--mode` | `mirror` 完全镜像（默认）/ `incremental` 增量更新 |
| `--volume-size` | 分卷大小，如 `500m` `1g`（差异 > 1 GB 自动分卷） |
| `--dry-run` | 只看差异报告，不打包 |
| `--keep-temp` | 保留临时目录（排查用） |

---

## 文件结构

```
<项目根>/
├── .syncignore               ← 忽略规则（从 .syncignore.example 复制到根目录）
└── sync-tools/               ← 本仓库
    ├── .env                  ← 本地路径配置（不提交 git，从 .env.example 复制）
    ├── .env.example          ← 配置模板
    ├── .syncignore.example   ← 忽略规则模板
    ├── .gitignore
    ├── config.py             ← 配置加载（读取 .env）
    ├── setup_sync.py         ← 部署检测
    ├── core/                 ← 核心模块
    │   ├── apply/            同步清理脚本
    │   │   ├── apply_sync.py
    │   │   └── apply_sync.bat
    │   ├── build/             本机打包交互脚本
    │   │   └── run_build.py
    │   ├── generate/          云端清单生成
    │   │   ├── generate_manifest.py   命令行版
    │   │   └── run_generate.py        交互版（bat 调用）
    │   ├── pack/              打包逻辑
    │   │   └── build_sync_package.py
    │   └── sync/              公共工具库（扫描/hash/忽略规则/7z检测/清单读写）
    │       └── sync_common.py
    ├── 本机打包.bat           ← 双击运行
    ├── 云端生成清单.bat
    └── README.md

<项目根>/                          ← 运行时目录（由脚本自动创建，不提交 git）
├── .venv/                    ← Python 虚拟环境
├── sync-tools/
│   ├── rm/                   ← 软删除目录
│   └── file_history/         ← 历史清单存档
│       └── manifests/        ← 云端 manifest 存档
└── ... 其他项目文件 ...
```

解压后的同步包结构（供参考）：

```
<解压目录>/
├── ... 差异文件（直接覆盖到项目根）...
└── _apply_sync/              ← 镜像模式有删除/移动项时存在
    ├── apply_sync.bat        ← 双击执行，完成后整个文件夹自动删除
    ├── apply_sync.py
    ├── delete_list.txt
    └── sync_manifest.json
```

---

## 常见问题

**Q: 云端 CPU 弱，生成清单很慢？**  
A: 现在固定使用 `XXH3`，会读取文件内容，速度较快，并能稳定识别移动文件。

**Q: 上传到一半断了怎么办？**  
A: 差异 > 1 GB 时自动分卷（每卷 1 GB），只需重传未完成的卷，其余卷不受影响。

**Q: 云端无法访问 PyPI，无法安装 xxhash / rich？**  
A: 可在本机安装好依赖后，将整个 `.venv` 目录打包上传到云端解压。项目脚本应使用项目根目录下的 `.venv`，不要使用全局 Python 或 conda 环境。

**Q: 想把 sync-tools 放到其他位置？**  
A: 在 `sync-tools\.env` 中添加 `ROOT=<项目根绝对路径>`，其余配置不变。

**Q: apply_sync.bat 执行报路径错误？**  
A: 检查解压路径是否含特殊字符（如末尾空格）。可改用命令行手动指定：  
```bat
.venv\Scripts\python.exe _apply_sync\apply_sync.py "D:\My Project"
```
