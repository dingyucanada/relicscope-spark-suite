# GitHub / Codex / DGX Spark 快速复现

本指南用于从公开 GitHub 仓库复现 RelicScope Spark Suite v1.2.0。交付分为两个层级：

- **代码冒烟层**：任何受支持的开发电脑都可运行确定性 Demo；不调用 GPU 模型，不代表 Spark 实机通过。
- **默认产品层**：一台 DGX Spark 运行一个共享多模态 GPU 端点；Qwen3-VL 是中文陶瓷图像主基线并提供视频 A/B 对照，Nemotron 3 Nano Omni 是原生视频候选。

双 Spark 是次级扩展，必须在单机主闭环稳定后单独部署和验收。

## 1. 获取仓库

公开仓库只读克隆无需 GitHub 登录，也无需在 Spark 上保存个人凭据：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout main
git status --short
git rev-parse HEAD
```

已安装 GitHub CLI 的管理工作站也可以运行：

```bash
gh repo clone dingyucanada/relicscope-spark-suite
cd relicscope-spark-suite
git checkout main
```

不要把个人访问令牌写入 clone URL、脚本、`.env` 或聊天记录。`requirements*.lock` 固定本地 Python 依赖，`demo_media/SHA256SUMS` 固定合成输入；完整 GPU 复现还必须保存预缓存 manifest、容器 image ID、模型 revision 和实际应用 commit。

## 2. 可选：使用 Codex

Codex 不是 RelicScope 的运行依赖。需要它协助时，按照 [OpenAI 官方 Codex CLI 文档](https://learn.chatgpt.com/docs/codex/cli)在受控工作站安装并登录，然后在仓库根目录运行 `codex`。

建议提示词：

```text
请先阅读 AGENTS.md、README.md 和 docs/GITHUB_SPARK_QUICKSTART.md。
默认目标是一台 DGX Spark；不要读取 secrets、.env、runtime 或真实器物数据，
不要修改驱动、网络、防火墙或 systemd。先运行仓库检查，再按固定 make 入口操作；
没有目标 Spark 的真实证据时，不要声称 GPU、离线或模型验收通过。
```

优先把 Codex 放在管理工作站，通过批准的 SSH 通道操作 Spark。若机构允许在 Spark 上使用 Codex CLI，也只能使用普通交互式管理账户；不能把 Codex 放进应用容器、systemd 或离线运行依赖。

## 3. 开发电脑：确定性代码冒烟

前置条件为 Python 3.11+、可用的 `venv` 模块，以及首次安装时可访问批准的 Python 包源：

```bash
./scripts/reproduce-demo.sh --install
```

以后启动：

```bash
./scripts/reproduce-demo.sh
```

另一个终端验证：

```bash
curl --fail http://127.0.0.1:8088/api/health
make demo-media-check
make demo-media-smoke
```

浏览器打开 `http://127.0.0.1:8088`。远程主机保持 loopback 绑定时，从管理工作站建立 SSH 隧道：

```bash
ssh -L 8088:127.0.0.1:8088 <spark-admin>@<spark-private-ip>
```

这条路径使用程序生成的 `DEMO/SYNTHETIC` 图片和视频，可复演图片、复拍比较、代表帧、证据图、报告和完整性。页面必须显示 degraded/local 边界；不能把它写成真实 VLM、原生视频模型或 DGX Spark GPU 验收。

仓库检查入口为：

```bash
make demo-check
make test
make check
```

测试数量会随功能变化；以命令退出状态和当前测试报告为准，不在文档中固定数量。

## 4. 默认产品路径：一台 DGX Spark

先完整阅读 [单台 DGX Spark 真实 GPU 部署与现场验收](SINGLE_SPARK_GPU_DEPLOYMENT.md)。不要在活动当天第一次下载模型或升级系统。

### 4.1 初始化与联网预缓存

```bash
make install ROLE=single INSTALL_ARGS="--generate-key"
```

检查 `.env` 的模型、许可、路径和容量设置。默认运行配置为：

```dotenv
MODEL_PROFILE=qwen3-vl
VISION_MODEL_SOURCE=Qwen/Qwen3-VL-30B-A3B-Instruct
VISION_MODEL=qwen3_vl_30b_a3b
SINGLE_VISION_BASE_URL=http://vision:8000/v1
VLLM_BASE_IMAGE=vllm/vllm-openai:v0.20.0
VLLM_IMAGE=relicscope-multimodal-vllm:0.20.0-arm64
RELICSCOPE_MAX_NATIVE_VIDEO_BYTES=33554432
RELICSCOPE_MAX_NATIVE_VIDEO_DURATION_MS=15000

AB_NEMOTRON_MODEL_SOURCE=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
AB_NEMOTRON_MODEL=nemotron_3_nano_omni
PREFETCH_AB_MODELS=1
```

Qwen3-VL 是 RelicScope 的中文陶瓷工程基线，不表述为 NVIDIA 官方 Spark 验证模型。Nemotron 是 English-first 原生视频候选；它有 NVIDIA 单机 Spark playbook，但官方模型卡将语言列为 English only，中文输出及忠实翻译必须进入专家评分，不能由机器 scorecard 自动晋升。

由负责人完成两套模型许可审核、准备权限为 `600` 的 Hugging Face token 后，只在获准联网窗口临时打开下载闸门：

```dotenv
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

然后执行：

```bash
make prefetch ROLE=single
```

预缓存完成后保存 `runtime/prefetch-manifest-single.txt`，并立即恢复：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
ACCEPT_MODEL_TERMS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

环境变量不是防火墙。可证明的离线运行还需要主机或上游网关阻断公网出站，并在断网状态下重新启动和验收。

### 4.2 启动与真实 GPU 验收

```bash
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
make accept-single-spark
```

单机只运行一份大模型权重。当前 Qwen 端点依次完成图片观察、原生视频观察和受约束报告摘要；应用、知识、证据和报告也在同一台 Spark。本机默认只暴露 `127.0.0.1:8088`，模型端点保留在私有容器网络。原生路径只接受服务端解析确认的单视频轨 H.264 MP4；默认 15 秒/32 MiB 是当前实测 Demo 闸门，其他输入走代表帧路径。

健康响应中的 `endpoint_identity_ready=true` 仅证明端点在线且 configured/served model 身份一致，不证明某次推理已经完成。严格 DGX 身份还要求 device-tree 主机型号含 `DGX Spark`、GPU 名称含 `GB10`、架构为 ARM64，三项同时通过。

单机实时验收结果写入 `runtime/acceptance/single-spark-live.json`。只有三类 `model_runs` 均为成功的 `local_vllm` completion，且 provider request ID、finish reason、模型 revision、应用 commit、输入/输出哈希、完成时间、容器与 GPU/DGX 证据均留档后，才能写“单台 DGX Spark 真实 GPU 推理已验证”。

### 4.3 冻结输入顺序 A/B

```bash
make ab-single-spark
```

该入口按 Qwen 基线 → Nemotron 同视频候选 → Qwen 最终报告顺序运行，两套大模型不会同时常驻。结果位于 `runtime/model-ab/`，包含同输入 hash、两模型运行记录、机器 scorecard 和 `SHA256SUMS`；成功路径最后恢复 Qwen，并需再次确认 served model 为 `qwen3_vl_30b_a3b`。

机器门槛通过只表示候选具备进入人工评审的资格。Nemotron 按 English-first 规则保留原始输出；专家至少评分中文术语或忠实翻译、时序完整性、事实与推测分离、边界安全及建议可操作性。任何默认模型晋升必须由文物/材料专家与模型工程负责人复核并记录，脚本不会自动宣布胜者。

## 5. 次级扩展：双 Spark

双 Spark 用于把多模态计算与应用/证据服务拆到两个独立节点；它不是 v1.2.0 默认安装，也不把两台设备的统一内存合并。单机原视频不跨 Spark；双机由 Spark B 保存权威原件，代表帧路径只传有限派生帧，显式原生路径才把服务端校验通过的受限 H.264 MP4 请求字节经私有模型平面送往 Spark A。只有用户明确选择该扩展时，才按 [双 Spark 部署指南](DUAL_SPARK_DEPLOYMENT.md)分别完成安装、网络预检、预缓存、离线锁定、启动和健康验证。

单机验收不能证明双机私网、跨节点认证、故障切换或性能；双机验收也不能替代模型科学质量和专家评审。

## 6. 数据、凭据与科学边界

- `.env`、`secrets/`、`runtime/`、上传媒体、数据库和模型缓存均被 Git 排除。
- 不把服务 key、HF token、Codex/GitHub 凭据提交或粘贴给代理。
- 仓库公开可见，但当前未授予开源许可证；复制、修改、再分发或商业使用需取得权利人另行许可。
- RGB 图片/视频只能形成可见观察、时序观察和采集建议，不能输出真伪、确定年代、窑口、作者、价值或法律结论。
- 内置仪器值属于回放数据；真实 Raman、XRF、HSI、X-ray、CT 或 TL 能力必须另行接入、校准和验收。
