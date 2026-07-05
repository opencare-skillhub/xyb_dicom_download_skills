# xyb-dicom-download-skill

一个独立、可复用的 ZCode 技能包，用于维护和扩展 `dicom_download` 项目的文档与使用指引。

## 这份技能包做什么

- 帮助完善 `dicom_download` 项目的 Quick Start / 快速开始。
- 统一 uv + Python 虚拟环境 + Playwright 的安装说明。
- 生成适合 Windows / macOS / Linux 的命令示例。
- 维护 README、CONTRIBUTING 等文档中的伦理与合规表述。

## 目录结构

```text
xyb-dicom-download-skill/
├── SKILL.md
├── README.md
└── references/
    ├── quick-start.md
    └── acknowledgements.md
```

## 重要约定

1. 先读项目里的现有文件，再改文档。
2. Python 依赖写进 `pyproject.toml`，Playwright 浏览器安装保持显式命令。
3. 命令示例优先使用 `uv run python ...`，减少跨平台差异。
4. 文档里必须保留以下三项内容：
   - 感谢引用上游项目；
   - 感谢小胰宝志愿者开源贡献；
   - 明确禁止使用本工具向患者收费或变相收费。

## 推荐使用场景

- 想把当前的 DICOM 下载流程整理成新手可读的 Quick Start。
- 想把 README 里分散的安装命令、启动命令和排错说明统一起来。
- 想把仓库的使用说明整理成可以直接复制给新手的版本。
