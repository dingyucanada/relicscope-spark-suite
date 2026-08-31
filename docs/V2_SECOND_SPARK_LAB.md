# 第二台 DGX Spark：隔离的模型实验节点

第二台 Spark 的 V2 职责只有三项：运行候选多模态模型、执行冻结输入基准、在主节点
维护或故障时保留可重建的模型服务能力。它不保存唯一的 Scout 任务、客户媒体或业务
数据库，也不与第一台 Spark 拼成一台“256 GB 机器”。

当前部署闭环由 `compose.v2.lab.yml` 和四个独立脚本构成：

1. `v2-lab-install.sh` 核实 Linux ARM64、DGX Spark/GB10、非 root 操作员和干净提交，
   创建专用目录、600 权限密钥及 `.venv-v2`；
2. `v2-lab-prepare-online.sh --allow-network` 在明确批准的联网窗口内下载模型和镜像；
3. `v2-lab-preflight.sh` 断网后核实精确模型 revision、镜像摘要、GPU、目录、密钥和
   网络隔离；
4. `v2-lab-health.sh` 通过真实 TLS 和鉴权访问 `/v1/models`，确认配置的模型已就绪。

这套设计让第二台节点可以独立重建和回滚，也避免候选模型影响 Scout 主链路。

## 网络与权限边界

```text
本机 127.0.0.1:8444
        │ HTTPS + API key
        ▼
  Caddy（非 root）
        │ lab-private（internal）
        ▼
  vLLM + GB10（非 root，无宿主端口）
```

- 默认只发布 `127.0.0.1:8444`；模型的 8000 端口没有宿主映射。
- vLLM 只加入 `lab-private` 内部网络；只有 Caddy 同时连接入口网络和内部网络。
- 两个容器均使用安装时记录的 `LAB_UID:LAB_GID`、只读根文件系统、`cap_drop: ALL`
  和 `pull_policy: never`。
- API key 只通过 600 权限文件挂载，禁止写入 Git、命令示例或镜像。
- Caddy 只转发基准所需的 `/v1/models` 与 `/v1/chat/completions`；其他 vLLM
  路径统一返回 404，避免把未受 API key 保护的兼容端点暴露到实验网。
- 默认 `nemotron3-nano-omni` profile 固定采用 NVIDIA 当前 Spark 配方要求的
  `vllm/vllm-openai:v0.20.0`，并启用 `trust-remote-code`、`nemotron_v3` reasoning
  parser、视频 token 剪枝和有界帧采样。模型源码与运行镜像均须固定到不可变
  revision/digest 后才允许离线启动。
- 当前 profile 只处理视频视觉轨，不安装音频依赖；需要视频声音时，应在批准窗口
  构建并固定含 `vllm[audio]` 的派生镜像，完成额外许可与攻击面审查后再启用。
- Caddy 的本地 CA 适用于受控实验。跨设备或机构网络使用前，应换成机构证书并配置
  防火墙；不要把 8444 暴露到互联网。

如确实需要从第一台 Spark 访问，可先建立 SSH 隧道或独立受控私网。只有在该私网地址
已经配置到第二台主机并完成防火墙限制后，才把 `LAB_BIND_IP` 从 loopback 改为对应的
私有 IPv4 地址。

## 首次部署

以下命令必须在第二台 DGX Spark 的干净仓库提交上，以普通操作员身份执行。

### 1. 离线安装骨架

```bash
./deploy/v2-lab-install.sh
```

随后编辑 `.env.v2.lab`：

- 确认 `LAB_MODEL` 是已批准且许可证允许本地部署的候选模型；
- 保持 `LAB_MODEL_PROFILE=nemotron3-nano-omni` 时，`LAB_MODEL` 必须属于 NVIDIA
  Nemotron 3 Nano Omni 模型族；其他经过验证的图像 VLM 使用 `generic-vlm` profile；
- 把 `LAB_MODEL_REVISION` 设为模型仓库真实的 40 或 64 位十六进制 commit；
- 保持 `LAB_BIND_IP=127.0.0.1`、`LAB_HTTPS_PORT=8444` 和
  `LAB_OFFLINE_MODE=true`。

脚本会保留已存在的密钥，不会覆盖它；若源码提交发生变化，应先完成代码审查和提交，
再重新运行安装脚本以刷新 `RELICSCOPE_LAB_GIT_COMMIT`。

### 2. 打开一次明确的联网窗口

公开模型：

```bash
./deploy/v2-lab-prepare-online.sh --allow-network
```

需要 Hugging Face 凭据的模型：

```bash
./deploy/v2-lab-prepare-online.sh \
  --allow-network \
  --hf-token-file /secure/path/hf-token
```

该步骤会：

- 按模型 profile 拉取 ARM64 vLLM 和 Caddy 镜像，并把配置改写为 registry digest；
- 对 Nemotron profile 额外验证容器中的 vLLM 精确版本为 `0.20.0`；
- 只缓存 `LAB_MODEL_REVISION` 指定的快照；
- 在 `.venv-v2` 中安装基准脚本固定版本的 `httpx==0.28.1` 与
  `Pillow==11.3.0`；
- 写入私有运行清单，但不启动任何服务。

准备完成后关闭下载通道。运行时不会自动拉取镜像或模型。

### 3. 断网预检并启动

```bash
./deploy/v2-lab-preflight.sh

docker compose \
  --env-file .env.v2.lab \
  -f compose.v2.lab.yml \
  up -d --no-build --pull never

./deploy/v2-lab-health.sh
```

预检本身不访问网络；它还会在 `--network none` 的临时容器中验证 GB10 CUDA 可见性，
并以 `local_files_only` 重新解析精确模型快照。健康检查只有在 Caddy 本地 CA、TLS、
API key 以及 `/v1/models` 返回的模型 ID 全部一致时才通过。首次装载大模型可能耗时，
默认等待上限为 1800 秒，可在 5–3600 秒范围内调整。

停止节点：

```bash
docker compose \
  --env-file .env.v2.lab \
  -f compose.v2.lab.yml \
  down
```

## 冻结输入基准

健康检查通过后，在第二台 Spark 本机运行同一组 3–5 张固定图片：

```bash
.venv-v2/bin/python scripts/benchmark-scout-vlm.py \
  --base-url https://spark-lab.local:8444 \
  --runtime-manifest runtime/lab-preparation/runtime-manifest.txt \
  --api-key-file secrets/lab_api_key \
  --ca-cert runtime/lab-caddy/data/caddy/pki/authorities/local/root.crt \
  --repeats 10 \
  front.jpg back.jpg base.jpg
```

保存输出 JSON，并对 Qwen 基线与候选模型使用完全相同的图片、视角和重复次数。脚本
直接复用生产链路的提示词、请求构造、Nemotron 参数、输出边界校验与哈希算法；同时
记录输入哈希、请求 ID、准备清单哈希、模型 revision、镜像摘要与端到端非流式时长。
其中 revision 与镜像来自本机 0600 权限的准备清单，属于操作员声明；远端只证明实际
响应的模型 ID，不能远程证明容器或 revision。因此基准必须在完成预检的节点本机执行
并连同 Compose 运行证据保存。它不测量 TTFT、峰值统一内存、并发能力或领域准确率；
切换主模型仍需预先冻结并由专家评分的评测集与验收阈值。

Nemotron 参数依据 NVIDIA 当前模型卡的 DGX Spark 专章：
[Nemotron 3 Nano Omni model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4#vllm)。
模型卡明确列出 DGX Spark 支持、vLLM 0.20.0、`trust-remote-code` 与专用 parser；
这些属于可部署前提，并不代表已经在客户副机通过中文陶瓷质量评测。

## 何时考虑两机分布式

只有单机在已定义的容量或时延门槛上失败，并且目标模型存在可复现的 GB10/ARM64
多节点配方时，才进入 ConnectX-7、NCCL/Ray 和 tensor parallel 验证。此前两台机器
保持“主节点稳定服务、实验节点独立评测”更容易运维，也能提供清晰的故障边界。

## 当前验证状态

仓库内可以完成脚本语法、Compose 拓扑和配置的静态检查。DGX Spark 硬件身份、GB10
容器访问、模型许可证、真实 revision 下载、首次加载时延、HTTPS 健康检查和基准结果，
仍必须在第二台实机上按上述顺序留存验收证据；本文档不把静态检查表述为真机通过。
