# Quick Start

下面这套步骤适用于 Windows、macOS 和 Linux。

## 1. 安装 Python

建议使用 Python 3.10 或更高版本。

先检查版本：

```bash
python --version
```

如果你的系统里 `python` 不是 Python 3，也可以按系统习惯使用 `python3`。

## 2. 安装 uv

`uv` 是这个项目推荐的 Python 环境管理工具。

### Windows

可以用以下任一方式安装：

- `winget install astral-sh.uv`
- `scoop install uv`
- `pipx install uv`

### macOS

可以用以下任一方式安装：

- `brew install uv`
- `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Linux

可以用以下任一方式安装：

- `curl -LsSf https://astral.sh/uv/install.sh | sh`
- 通过发行版包管理器安装（如果仓库里有可用包）

安装完成后确认：

```bash
uv --version
```

## 3. 进入项目目录

切换到 `dicom_download` 仓库根目录。

## 4. 安装 Python 依赖

```bash
uv sync
```

这会创建或复用虚拟环境，并安装 `pyproject.toml` 中声明的 Python 包。

## 5. 安装 Playwright 浏览器

第一次使用时，还要单独安装浏览器。推荐先装 Chromium：

```bash
uv run python -m playwright install chromium
```

如果你想安装全部浏览器：

```bash
uv run python -m playwright install
```

## 6. 准备输入文件

新建 `urls.txt`，每行一个分享链接：

```text
https://example.com/viewer?share_id=AAAA
https://example.com/viewer?share_id=BBBB
```

## 7. 开始下载

推荐使用统一入口：

```bash
uv run python multi_download.py --urls-file urls.txt --out-parent ./downloads
```

如果你想先试单个脚本，也可以：

```bash
uv run python shdc_download_dicom.py --urls-file urls.txt --out-parent ./downloads
```

## 8. 常见报错

### 报 `BrowserType.launch: Executable doesn't exist`

说明浏览器还没有安装。重新执行：

```bash
uv run python -m playwright install chromium
```

### 报 Python 依赖缺失

重新安装依赖：

```bash
uv sync
```

### Windows 上提示命令找不到

优先使用 `uv run python ...`，不要依赖系统全局 Python 路径。
