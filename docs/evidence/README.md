# v1.1.0 本机验收证据

本目录保存可直接复核的界面与结构化报告样本：

- `ui-desktop-initial.png`：1280 × 720 桌面首屏；
- `ui-desktop-workspace.png`：桌面端并列的器物建档与图像 / 视频入口；
- `ui-completed-evidence.png`：P01 完成后的桌面证据图与执行时间线；
- `ui-mobile-iab-390.png`：390 × 844 首屏；
- `ui-mobile-media-entry.png`、`ui-mobile-media-controls.png`：窄屏媒体入口与控制区；
- `ui-mobile-completed-evidence.png`：窄屏专用双列证据图，节点文字保持可读；
- `media-smoke-report.json`、`media-smoke-report.html`：真实 JPEG、MP4 与时间戳帧通过同一 API 生成的机器版和阅读版结构化报告。

报告中的用户媒体哈希、质量门、帧来源、证据边和审计链来自真实执行；内置检测回放仍明确标记为 `DEMO/SYNTHETIC`。测试视频仅包含轻微缩放，系统按设计拒绝把它解释为充分多视角覆盖，并输出重采建议。上述材料证明本机软件闭环，不替代两台 DGX Spark、真实 VLM 或科学仪器验收，也不构成文物鉴定结论。
