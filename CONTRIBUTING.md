## 贡献指南：如何新增一家医院/厂商的适配

本项目目前有两类下载策略，你需要先判断目标站点的阅片器实现属于哪一类，然后选最近似的脚本作为模板：
- UI 响应抓取策略（类似 `tjmucih_download_dicom.py` / `shdc_download_dicom.py`）
  - 通过 Playwright 操作 UI（展开序列面板、点击序列、滑块逐帧）来触发网络请求；
  - 从响应体中识别 DICOM（快速头判断），直接落盘为 `.dcm` 文件；
  - 常见差异点：DOM 选择器、高清开关、分页/滑块行为、同源/跨域路径过滤等。
- WS + h5Cache 策略（类似 `nyfy_download_dicom.py`）
  - 监听 WebSocket 帧，解析影像元数据（Study/Series/SOP 等），补齐 `furl`；
  - 通过 HTTP 拉取像素数据，组装为 Part 10 DICOM（写入 UID/像素/空间信息等）。

### 步骤 1：确定站点特征
- 访问单个检查 URL，并用开发者工具观察：
  - 是否存在 WebSocket，帧内是否有 OnCached/OnThreeLevelChanged 等元数据；
  - 底部/右侧是否有“序列面板”，卡片或按钮结构、张数/序列号展示；
  - 网络请求里是否有明显的 DICOM/像素数据响应（通过文件头快速判断）。

### 步骤 2：选择模板并创建适配器
- UI 响应抓取型：以 `shdc_download_dicom.py` 为模板（支持滑块逐帧与高清切换），或 `tjmucih_download_dicom.py`（右侧按钮列表）。
- UI 响应抓取型：以 `shdc_download_dicom.py` 为模板（支持滑块逐帧与高清切换），或 `tjmucih_download_dicom.py`（右侧按钮列表）。
- WS+h5Cache 型：以 `nyfy_download_dicom.py` 为模板。
- 复制出新的脚本文件（或将逻辑并入统一入口 `multi_download.py` 的某 provider）。

### 步骤 3：填写选择器/规则与站点参数
- UI 响应抓取型（常改动点）：
  - `open_series_panel`：展开序列面板的按钮/容器选择器；
  - `read_series_list`：卡片/按钮列表选择器、张数/描述的解析正则；
  - `goto_image_index`/滑块读取：滑块 input 的选择器与事件派发；
  - 高清切换：右下角菜单文案（如“流畅/原图(清晰度高)”）；
  - 响应过滤：仅处理该域名/路径的响应（避免误判 CSS/JS）。
- WS+h5Cache 型（常改动点）：
  - WebSocket 帧结构：字段名（study/series/sop uid、spacing、window、像素等）；
  - `furl` 与 `h5Cache` 规则：如何拼接 base 与 uid；
  - DICOM 组装：必要时填写 Modality、Rescale、Spacing、IOP 等。

### 步骤 4：分类与命名
- 统一使用 `common_utils.safe_name` 处理目录/文件名（避免非法字符）。
- 目录命名建议包含 modality/series number/描述/UID 尾部，便于人眼识别。
- UI 策略可按“诊断/非诊断”关键字做粗分（避免 Localizer/Dose 等干扰）。

### 步骤 5：支持多 URL 批处理与 zip
- 所有脚本都应支持：
  - `--url` 或 `--urls-file`；
  - `--out-parent`：每个 URL 落库到独立子目录；
  - 默认为每个 URL 生成 `<share_id>.zip`（可用 `--no-zip` 关闭）。
- 使用 `common_utils.extract_share_id/read_urls_file/make_zip_dir` 统一实现。

### 步骤 6：接入统一路由（可选但推荐）
- 在 `multi_download.py` 的 `detect_provider` 中添加域名判断；
- 或通过 `--provider` 参数让用户手动指定；
- 确保参数透传：UI 策略（高清、轮次、并发等）、NYFY 策略（并发/重试/回填/校验）。

### 步骤 7：文档与示例
- 在 `README.md` 中增加站点使用说明和参数提示；
- 提供 `urls.txt` 示例（或补充到 `urls.txt.example`）；
- 如果是新手环境准备，先看 `README.md` 顶部的 Quick Start / 快速开始。

### 步骤 8：合规与注意事项
- 不要提交任何 PHI/敏感数据样本；
+- 遵守本仓库的许可证与上游项目的许可证（特别注意 Commons Clause 的限制：禁止出售软件或以其功能为主要价值的有偿服务/托管/支持）。

### 最小自检清单
- [ ] 单 URL 能成功拉取并输出 DICOM 文件
- [ ] 多 URL（urls.txt）能分别落库且生成独立 zip
- [ ] UI 策略能完整“走片”，NYFY 能正确回填 `furl`
- [ ] 目录命名与 zip 命名符合预期
- [ ] README 添加了站点说明与示例命令
