# Acknowledgements and policy

## 感谢上游项目

本项目基于 [hengqujushi/dicom_download](https://github.com/hengqujushi/dicom_download) 封装为 ZCode 技能包，感谢原作者的出色工作。

本项目中的 **cloud provider** 部分融合/适配了上游开源项目 [Kaciras/cloud-dicom-downloader](https://github.com/Kaciras/cloud-dicom-downloader)。该项目的许可为 **Apache 2.0 + Commons Clause**，其中 Commons Clause 明确不授予 "Sell（以软件功能为核心收费提供产品/服务）" 的权利。如你计划基于 cloud provider 能力开展商业收费/托管服务，请务必先阅读并遵守其许可条款，必要时联系原作者获得授权。

## 感谢小胰宝志愿者开源贡献

特别感谢小胰宝志愿者团队在开源、整理、验证和传播使用经验上的贡献。正是这些分享，让更多人能更顺利地完成环境配置和使用。

## 使用限制

**禁止使用本工具向患者收费或变相收费。**

这包括但不限于：
- 直接收费；
- 以"服务费""支持费""代操作费""培训费"等名义变相收费；
- 将该工具包装成面向患者的付费下载、托管、代取或代导出服务；
- 以软件功能本身作为主要收费价值。

如果要做公共服务、公益协助或院内支持，请保持免费、透明、合规。

## 注意事项

- 不要提交任何包含 PHI/敏感信息的数据样本。
- 不同站点 UI 有差异，若遇到选择器变更或策略不适配，可反馈或调整对应脚本的选择器/策略参数。
- cloud provider 依赖上游已停止维护的实现，若与本项目已有实现（天肿/复肿/宁夏总医院）重叠，则优先以本项目实现为准。
