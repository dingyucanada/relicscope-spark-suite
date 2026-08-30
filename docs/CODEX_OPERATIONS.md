# Codex Desktop / CLI：开发与运维入口

## 1. 定位

Codex Desktop 或 Codex CLI 可以帮助获授权人员审阅差异、执行仓库提供的固定入口、分析脱敏日志和准备发布。它们不参与器物采集、推理、知识检索、证据图、报告或服务自启动，也不是 RelicScope 的生产依赖。

v1.2.0 默认部署是一台 DGX Spark：Qwen3-VL 作为中文陶瓷图像主基线并提供视频 A/B 对照；Nemotron 3 Nano Omni 只作为原生视频候选，使用冻结输入顺序 A/B。双 Spark 是另行选择和验收的次级扩展。

即使 Codex 完全不可用，默认产品路径仍可独立完成：

```bash
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
make accept-single-spark
```

运维状态必须使用两层口径：`endpoint_identity_ready=true` 只说明本地端点在线且 configured/served model 一致；实际完成证据必须来自验收包内对应图片、原生视频和报告角色的成功 `model_runs`。Codex 不得把健康探针、模型列表或端点身份改写成“推理已完成”。严格 DGX Spark 身份还需要 device-tree 主机型号含 `DGX Spark`、GPU 名称含 `GB10`、架构为 ARM64，三项同时通过。

## 2. 推荐安装位置

首选在受控开发或管理工作站安装 Codex，通过机构批准的 SSH 或跳板机操作 Spark。这样可以把 AI 工具登录态与生产服务身份分开，也不会给离线运行增加依赖。

若机构允许在 Spark Linux 管理账户使用 Codex CLI，应满足：

- 仅用普通交互式管理账户，不使用 `root` 或 RelicScope 服务用户；
- 不加入 systemd、cron、应用容器或健康检查；
- 不读取或上传 `secrets/`、`.env`、`runtime/`、原始器物、专家意见和未授权知识；
- 不把 Codex 可用性作为启动、故障恢复或离线演示条件；
- 任务结束后按机构要求退出并清理管理会话。

若 Spark 的操作系统或桌面环境不在 Codex Desktop 的当前官方支持范围，使用管理工作站上的 Desktop 或在 Spark 上使用受支持的 CLI；不要为安装 Codex 改动驱动、桌面栈或系统关键依赖。安装前以 [OpenAI Codex CLI 官方文档](https://learn.chatgpt.com/docs/codex/cli)为准。

## 3. 获取仓库与启动 Codex

在受控工作站安装和登录 Codex 后：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout main
git status --short
git rev-parse HEAD
codex
```

公开仓库的只读克隆不需要 GitHub 凭据。只有推送变更时才使用经批准的开发者身份；不得把个人访问令牌、Codex 登录令牌或 OpenAI API key 写入 clone URL、脚本、镜像或 RelicScope `.env`。

建议首次提示：

```text
请先完整阅读 AGENTS.md、README.md、docs/GITHUB_SPARK_QUICKSTART.md，
以及单台 Spark 部署指南。默认目标是一台 DGX Spark；只调用仓库已有 make 入口。
不要读取或输出 secrets、.env、runtime 和真实器物数据，不要修改驱动、网络、
防火墙、用户或 systemd。没有目标机器证据时，不要宣称 GPU、离线或模型验收通过。
```

## 4. 默认单 Spark 工作流

### 4.1 代码检查

Codex 可以在开发工作站执行：

```bash
make demo-check
make test
make check
```

测试数量随版本变化；只记录命令、commit、退出状态和实际测试报告，不在运维文档中固定数量。确定性 Demo 通过只能证明代码闭环，不能证明 DGX Spark、真实 GPU 模型或离线网络通过。

### 4.2 安装、预缓存与启动

在负责人已批准模型许可、存储与联网窗口后，固定入口为：

```bash
make install ROLE=single INSTALL_ARGS="--generate-key"
make prefetch ROLE=single
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
make accept-single-spark
```

Codex 可以解释失败和建议下一项只读检查，但不能替操作者自动接受许可、扩大联网范围、关闭防火墙或更改系统驱动。预缓存完成后必须恢复 `.env` 的离线闸门，并由主机或上游网络策略真正阻断公网出站。

原生视频验收使用服务端从文件字节解析确认的单视频轨 H.264 MP4。默认 `RELICSCOPE_MAX_NATIVE_VIDEO_DURATION_MS=15000` 与 32 MiB 上限是当前实测 Demo 闸门；Codex 不得通过改文件名、MIME 或客户端时长绕过它，也不得在没有 profile 专项复验时擅自调高。较长或其他容器的视频使用代表帧路径。

### 4.3 顺序模型 A/B

只有两套模型都已批准和预缓存时才运行：

```bash
make ab-single-spark
```

该入口冻结输入并依次运行：

1. Qwen3-VL 中文陶瓷图像主基线与同视频对照；
2. Nemotron 3 Nano Omni 原生视频候选；
3. 恢复 Qwen3-VL 并生成最终报告证据。

一台 Spark 不同时常驻两套大模型。Nemotron 是 English-first 候选；Codex 可以汇总 `runtime/model-ab/` 内的脱敏 scorecard，但不得润色原始候选输出后再评分，也不得读取原始受限媒体。专家 rubric 至少覆盖中文术语或忠实翻译、时序完整性、事实/推测分离、边界安全和建议可操作性。任何默认模型晋升都需要文物/材料专家与模型工程负责人复核、签字并保留理由；完成或中断 A/B 后必须确认 Qwen served model 已恢复。

## 5. 建议的远程运维方式

由操作者先在 Spark 上切换到受控发布目录，再从管理工作站执行固定入口。例如：

```bash
ssh <spark-admin>@<spark-private-ip> \
  'cd /srv/relicscope/current && make health ROLE=single'
```

变更发布前应人工审阅 diff；发布后保存实际 commit、预检结果、健康响应、容器 image ID、模型 revision、GPU telemetry、验收 JSON 和哈希。不要保存服务 key 或 HF token。

## 6. 次级扩展：双 Spark

只有操作者明确选择双节点扩展时，才读取 `docs/DUAL_SPARK_DEPLOYMENT.md` 并分别执行 `ROLE=spark-a` 与 `ROLE=spark-b` 的流程。单机原视频只在同机受控目录与内部容器网络中使用；双机由 Spark B 保存权威原件，代表帧路径只传有限派生帧，显式原生路径才把服务端校验通过的受限 H.264 MP4 请求字节经私有平面送往 Spark A。单机成功不能证明跨节点网络、认证或故障恢复；双机成功也不能替代模型科学质量与专家复核。Codex 不得根据检测到第二台设备就自动切换拓扑。

## 7. 明确禁止

- 把 Codex Desktop 或 CLI 当作常驻推理服务、Agent 运行时或健康检查依赖；
- 把 Codex、GitHub、HF 或服务凭据打进 Docker 镜像、离线包、备份或 systemd 环境；
- 将完整 `.env`、原始媒体、受限知识、专家意见或密钥粘贴给代理；
- 让 AI 工具未经审批修改防火墙、网卡、驱动、用户权限、保留策略或删除备份；
- 未经专家复核就根据 A/B scorecard 晋升 Nemotron 或其他候选；
- 以开发电脑结果、Codex 生成说明或单机结果替代对应目标 Spark 拓扑的现场证据。
