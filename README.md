# xyb-dicom-download-skill

使用 Playwright 自动化下载 DICOM 影像文件的工具。支持批量下载、跨平台（Windows / macOS / Linux），适合用于影像数据备份、科研分析、公益协助等场景。

> ⚠️ **使用限制**：本工具**禁止**用于向患者收费或变相收费。详见 [使用限制](#使用限制)。

## 快速开始

### 前置条件

- Python 3.10 或更高版本
- [uv](https://docs.astral.sh/uv/)（Python 环境管理工具）

### 安装步骤

```bash
# 1. 安装 Python 依赖
uv sync

# 2. 安装 Playwright 浏览器（推荐仅安装 Chromium）
uv run python -m playwright install chromium
```

### 准备输入文件

新建 `urls.txt`，每行一个分享链接：

```text
https://example.com/viewer?share_id=AAAA
https://example.com/viewer?share_id=BBBB
```

### 开始下载

```bash
# 统一入口（推荐）
uv run python multi_download.py --urls-file urls.txt --out-parent ./downloads

# 或直接使用下载脚本
uv run python shdc_download_dicom.py --urls-file urls.txt --out-parent ./downloads
```

## 目录结构

```text
xyb-dicom-download-skill/
├── SKILL.md                      # ZCode 技能定义
├── README.md                     # 本文件
├── references/
│   ├── quick-start.md            # 详细快速开始指南
│   └── acknowledgements.md       # 致谢与使用限制
└── scripts/                      # 辅助脚本
```

## 常见问题

### 浏览器未安装

```bash
# 报错: BrowserType.launch: Executable doesn't exist
uv run python -m playwright install chromium
```

### Python 依赖缺失

```bash
uv sync
```

### Windows 上命令找不到

优先使用 `uv run python ...`，不要依赖系统全局 Python 路径。

## 致谢

感谢上游开源项目的实现思路与使用方式，为 DICOM 下载工作流打下基础。

特别感谢**小胰宝志愿者团队**在开源、整理、验证和传播使用经验上的贡献。正是这些分享，让更多人能更顺利地完成环境配置和使用。

## 使用限制

**禁止使用本工具向患者收费或变相收费。**

这包括但不限于：

- 直接收费；
- 以"服务费""支持费""代操作费""培训费"等名义变相收费；
- 将该工具包装成面向患者的付费下载、托管、代取或代导出服务；
- 以软件功能本身作为主要收费价值。

如果要做公共服务、公益协助或院内支持，请保持免费、透明、合规。

## License

详见 [LICENSE](LICENSE) 文件。
