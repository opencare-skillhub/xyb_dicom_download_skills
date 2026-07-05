# xyb-dicom-download-skill

使用 Playwright 自动化下载 DICOM 影像文件的工具。支持多站点、批量下载、跨平台（Windows / macOS / Linux），适合用于影像数据备份、科研分析、公益协助等场景。

> ⚠️ **使用限制**：本工具**禁止**用于向患者收费或变相收费。详见[使用限制](#使用限制)。

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
├── common_utils.py               # 通用工具函数
├── main.py                       # 文本菜单版入口
├── multi_download.py             # 多站点统一下载路由器
├── shdc_download_dicom.py        # 复肿下载脚本
├── tjmucih_download_dicom.py     # 天肿下载脚本
├── nyfy_download_dicom.py        # 宁夏总医院下载脚本
├── pyproject.toml                # Python 依赖声明
├── uv.lock                       # uv 锁定文件
├── dicom_download.toml.example   # 配置示例
├── urls.txt.example              # URL 列表示例
├── LICENSE                       # Apache 2.0
├── CONTRIBUTING.md               # 贡献指南
├── references/
│   ├── quick-start.md            # 详细快速开始指南
│   └── acknowledgements.md       # 致谢与使用限制
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

## 站点说明

| 域名 | 医院 | 文件名 |
|------|------|--------|
| `zlyy.tjmucih.cn` | 天肿（天津市肿瘤医院） | `tjmucih_download_dicom.py` |
| `ylyyx.shdc.org.cn` | 复肿（复旦大学附属肿瘤医院） | `shdc_download_dicom.py` |
| `zhyl.nyfy.com.cn` | 宁夏总医院（宁夏医科大学总医院） | `nyfy_download_dicom.py` |

`multi_download.py` 会自动根据域名选择对应的下载脚本。如果自动识别不准，也可以通过 `--provider` 手动指定（`tz` / `fz` / `nyfy`）。

## 致谢

本项目基于 [hengqujushi/dicom_download](https://github.com/hengqujushi/dicom_download) 封装为 ZCode 技能包，感谢原作者的出色工作。

本项目的 **cloud provider** 部分融合/适配了上游开源项目 [Kaciras/cloud-dicom-downloader](https://github.com/Kaciras/cloud-dicom-downloader)。该项目的许可为 **Apache 2.0 + Commons Clause**，其中 Commons Clause 明确不授予 "Sell（以软件功能为核心收费提供产品/服务）" 的权利。

特别感谢**小胰宝志愿者团队**在开源、整理、验证和传播使用经验上的贡献。

## 使用限制

**禁止使用本工具向患者收费或变相收费。**

这包括但不限于：

- 直接收费；
- 以"服务费""支持费""代操作费""培训费"等名义变相收费；
- 将该工具包装成面向患者的付费下载、托管、代取或代导出服务；
- 以软件功能本身作为主要收费价值。

如果要做公共服务、公益协助或院内支持，请保持免费、透明、合规。

### 注意事项

- 不要提交任何包含 PHI/敏感信息的数据样本。
- 不同站点 UI 有差异，若遇到选择器变更或策略不适配，可反馈或调整对应脚本的选择器/策略参数。
- cloud provider 依赖上游已停止维护的实现，若与本项目已有实现（天肿/复肿/宁夏总医院）重叠，则优先以本项目实现为准。

## License

详见 [LICENSE](LICENSE) 文件。
