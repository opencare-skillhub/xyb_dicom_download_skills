---
name: xyb-dicom-download-skill
description: DICOM 影像批量下载技能包，基于 hengqujushi/dicom_download 封装。支持天肿(zlyy.tjmucih.cn)、复肿(ylyyx.shdc.org.cn)、宁夏总医院(zhyl.nyfy.com.cn)等多站点，通过 Playwright 自动化下载并提供多 URL 批处理与逐 URL 打包能力。
---

# xyb-dicom-download-skill

Use this skill when working on the `dicom_download` project or when drafting guidance for the Xyb DICOM download workflow.

## What this skill should do

- Help maintain the downloader repo and its docs.
- Write or update beginner-friendly quick-start instructions.
- Keep uv, Python virtual environment, and Playwright setup clear for Windows, macOS, and Linux.
- Preserve the project's safety and ethics language, especially around patient data and charging.
- Keep examples aligned with the current repo commands and behavior.

## Core workflow

1. Inspect the target files first.
   - Read `README.md`, `pyproject.toml`, and any relevant script before editing.
   - Prefer the existing repo patterns over introducing a new style.

2. Keep the quick start explicit.
   - Show the order: install Python, install `uv`, run `uv sync`, install Playwright browsers, then run the downloader.
   - Prefer `uv run python ...` in examples so the same instructions work across Windows, macOS, and Linux.

3. Treat browser installation as a separate step.
   - Python dependencies belong in `pyproject.toml`.
   - Playwright browser binaries should be installed with an explicit command in the docs.
   - If the user asks for automation, prefer a separate helper command or script, not a hidden install hook.

4. Keep the README beginner-friendly.
   - Start with a quick start section near the top.
   - Add a troubleshooting section for missing Python packages and missing Playwright browsers.
   - Keep command examples short and copy-pasteable.

5. Preserve the project’s ethics notes.
   - Always include acknowledgement of the upstream project.
   - Always include thanks to the 小胰宝 volunteer open-source contributors.
   - Always state that the tool must not be used to charge patients or to disguise charging as service, support, training, or any other indirect form.

## Style guidance

- Prefer short sections with clear headings.
- Use concrete commands, not vague descriptions.
- Keep wording consistent with the surrounding repository language.
- When updating docs, keep the recommended path easy for beginners.

## Good default content for a quick start

- `uv sync`
- `uv run python -m playwright install chromium`
- `uv run python multi_download.py --urls-file urls.txt --out-parent ./downloads`

## If you need more detail

Read the files in `references/` for the current quick-start wording, acknowledgement text, and command conventions.
