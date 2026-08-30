# GitHub / Codex / DGX Spark 快速复现

本指南用于开发电脑或 DGX Spark 从公开 GitHub 仓库复现 RelicScope v1.1.0。最先复现的是无需模型和仪器的本地确定性 Demo；双 Spark、真实 VLM 与真实传感器属于后续硬件验收。

## 1. 获取仓库

公开仓库只读克隆无需登录 GitHub，也无需在 Spark 上保存个人凭据：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout v1.1.0-r2
git status --short
git rev-parse HEAD
```

已安装 GitHub CLI 的管理工作站也可以运行：

```bash
gh repo clone dingyucanada/relicscope-spark-suite
cd relicscope-spark-suite
git checkout v1.1.0-r2
```

不要把个人访问令牌写入 clone URL、脚本、`.env` 或聊天记录。

标签固定源码，两个 `requirements*.lock` 固定本地 Demo 的 Python 依赖，`demo_media/SHA256SUMS` 固定合成输入。完整 GPU 路径还必须在每台 Spark 留存 prefetch manifest、容器 image ID 和模型 revision；完成镜像 digest 与模型 revision 的组织级冻结前，不把它表述为跨机器字节级一致。

## 2. 可选：安装 Codex CLI

Codex 不是 RelicScope 的运行依赖。需要它协助复现时，按照 [OpenAI 官方 Codex CLI 文档](https://learn.chatgpt.com/docs/codex/cli)在 macOS/Linux 安装：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
codex
```

首次启动按官方流程登录。在 DGX Spark 上只用普通管理账户运行 Codex，不使用 root、服务账户、systemd 或应用容器。若站点策略不允许安装，改在管理工作站使用 Codex，通过批准的 SSH 通道操作 Spark。

建议提示词：

```text
请先阅读 AGENTS.md 和 docs/GITHUB_SPARK_QUICKSTART.md。
只复现本地 deterministic demo，不访问 secrets、.env 或 runtime，
不修改驱动、网络、防火墙或 systemd。执行仓库检查和首次安装，
确认 /api/health 后告诉我浏览器地址与仍未验证的硬件边界。
```

## 3. 不依赖 Codex 的一键复现

前置条件：Python 3.11+、可用的 `venv` 模块；首次安装时能够访问批准的 Python 包源。Ubuntu / DGX OS 若提示 `ensurepip` 或 `venv` 不可用，请由管理员安装与所选 Python 匹配的 `python3-venv` 包后重试。

```bash
./scripts/reproduce-demo.sh --install
```

该命令先检查 JavaScript、部署脚本和 OpenSpec（若已安装），再创建 `.venv`、安装固定版本依赖并在 `127.0.0.1:8088` 启动服务。后续启动：

```bash
./scripts/reproduce-demo.sh
```

另一个终端验证：

```bash
curl --fail http://127.0.0.1:8088/api/health
```

远程 Spark 默认保持 loopback 绑定。从管理工作站访问：

```bash
ssh -L 8088:127.0.0.1:8088 <spark-admin>@<spark-private-ip>
```

浏览器打开 `http://127.0.0.1:8088`，运行“一键完整演示”。页面必须显示本地降级模式和 `DEMO/SYNTHETIC` 边界。

## 4. 仓库验证

```bash
make demo-check
make test
make demo-media-check
make demo-media-smoke
```

仓库内置一套完全程序生成的 `DEMO/SYNTHETIC` 输入，不含真实文物或用户媒体。浏览器手工复演可依次选择 `demo_media/reference.png`、`demo_media/comparison.png` 和 `demo_media/synthetic_orbit.mp4`；`make demo-media-smoke` 会在临时服务中自动完成图片、复拍比较、视频帧、报告和完整性闭环并自动清理。

CI 同样执行 103 项测试、合成媒体校验与闭环、JavaScript 语法、部署脚本和 OpenSpec strict。`make demo-check` 在尚未安装 `.venv` 时会明确跳过 Python 测试；`make demo-install` 会先安装测试依赖并完成校验，再启动服务。

## 5. 两台 DGX Spark

确定性 Demo 通过后，再严格按照 `docs/DUAL_SPARK_DEPLOYMENT.md` 完成：安装初始化 → 核对 `.env` 与网络 → 在批准的联网窗口预缓存 → 恢复离线标志 → 完整预检 → 启动与健康验证。下面只列启动阶段的固定入口；fresh Spark 不能从这里直接开始：

```bash
make preflight ROLE=spark-a
make start ROLE=spark-a
make health ROLE=spark-a

make preflight ROLE=spark-b
make start ROLE=spark-b
make health ROLE=spark-b
```

这些命令不会自动修改驱动、网卡或防火墙。只有两台目标设备的健康响应、模型身份、跨节点认证、断网流量和性能证据均留档后，才可以声明“双 Spark 实机复现通过”。

## 6. 数据与凭据边界

- `.env`、`secrets/`、`runtime/`、上传媒体、数据库和模型缓存均被 Git 排除。
- 不把服务 key、HF token、Codex/GitHub 凭据提交或粘贴给代理。
- 仓库公开可见，但当前未授予开源许可证；复制、修改、再分发或商业使用需取得权利人另行许可。
- RGB 图片/视频只能形成可见观察和采集建议，不能输出真伪、确定年代、窑口、作者、价值或法律结论。
