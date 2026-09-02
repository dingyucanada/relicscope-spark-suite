# RelicScope V2：单台 DGX Spark 正式部署

> 适用版本：V2 Scout + Spark
> 推荐运行时：NVIDIA NIM for VLM + Qwen3.6-35B-A3B
> 当前状态：`DEPLOYMENT_READY`。代码、配置与交接材料已具备部署条件；客户 Spark、目标 Android 和真实业务数据仍需现场验收。

## 1. 部署结果

一台 DGX Spark 必须独立完成整条主链路：

```text
Android Scout
  拍摄 · 端侧质检 · 离线队列
          │ HTTPS / 设备凭据
          ▼
DGX Spark
  Caddy → Scout Gateway → 持久任务 → 私有 NVIDIA NIM
                    │
                    └→ 结构化可见观察 · 运行证明 · 补拍建议
```

第二台 Spark 可以运行同一主模型作为人工切换备用，也可以承载 Qwen3.8、Nemotron、评测、LoRA 或批处理。它不是主系统启动条件，两台机器也不被强制划分为永久固定角色。

Codex Desktop 适合工程师检出代码、检查配置和协助排障。RelicScope 正式运行不依赖 Codex Desktop；服务由冻结的 Git commit、容器、NIM profile、环境配置和系统服务构成。

## 2. 首选与备选运行时

| 路径 | 用途 | 入口 |
|---|---|---|
| **NVIDIA NIM / Qwen3.6** | 首台 Spark 生产试运行；NVIDIA 提供 DGX Spark 专用 ARM64 profile | `compose.v2.nim.yml`、`.env.v2.nim`、`make v2-nim-*` |
| NVIDIA vLLM / 自选模型 | 兼容回退和模型研发；需自行证明具体模型、镜像与 GB10 组合 | `compose.v2.yml`、`.env.v2`、`make v2-*` |
| 候选实验节点 | Qwen3.8 / Nemotron 等冻结输入 A/B | `compose.v2.lab.yml`、`.env.v2.lab`、`make v2-lab-*` |

模型决策和晋级规则见 [`MODEL_SELECTION_AND_SPARK_RUNTIME_2026-09.md`](MODEL_SELECTION_AND_SPARK_RUNTIME_2026-09.md)。

## 3. Spark 准备

### 3.1 管理员先完成

- 更新并记录 DGX OS、GPU driver、CUDA、Docker 和 NVIDIA Container Toolkit；
- 为 RelicScope 建立一个非 root 运维账号，并允许其使用 Docker；
- 确定 Spark 的私有 LAN IPv4、主机名和现场 DNS；
- 预留独立持久磁盘目录和加密备份位置；
- 准备机构 CA，或仅在受控试运行使用 Caddy local CA；
- 取得短期、最小权限的 NGC Personal API Key，并保存在 Spark 上权限为 `600` 的独立文件中；准备完成后撤销或轮换；
- 将主机 `root`、Docker daemon 以及具有 Docker/docker-group 权限的管理员纳入下载阶段受信任边界；
- 明确联网下载窗口，准备结束后关闭公网出站。

NVIDIA 当前 NIM VLM 前置条件包括 ARM64、Docker 24+、Container Toolkit 1.14+、CUDA 12.9+ 和 driver 580+。实际版本仍应与目标 NIM release 和 DGX Spark release notes 一起复核。

### 3.2 检出冻结源码

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git switch main
git rev-parse HEAD
git status --short
```

只有干净工作树进入部署。记录 commit；不得从未提交的临时改动启动正式服务。

## 4. 推荐路径：NVIDIA NIM + Qwen3.6

### 4.1 初始化，不联网、不启动

```bash
make v2-nim-install
```

该命令创建 `.env.v2.nim`、私有数据与 NIM cache、Caddy 目录，以及随机服务密钥。配置和凭据均不进入 Git。

编辑 `.env.v2.nim`：

```dotenv
SCOUT_BIND_IP=192.168.50.20
SCOUT_HOSTNAME=scout.spark.local
```

`SCOUT_BIND_IP` 必须是本机私有地址。不要映射 NIM 的 8000 端口，也不要把 8443 转发到公网。

### 4.2 在目标 GPU 上发现 profile

NIM profile 与镜像、精度和 GPU 绑定。不要从文档或另一台机器抄 profile ID。Qwen3.6
的这个 NIM release 仍需要 NGC Registry 授权。把 key 保存在权限为 `600` 或 `400`
的独立文件中；脚本会使用隔离的临时 Docker 配置登录，且无论成功或失败都会自动
退出并清理，不覆盖操作员原有的 Docker 凭据：

```bash
make v2-nim-list-profiles \
  NIM_PROFILE_ARGS="--allow-network --ngc-key-file /secure/ngc_api_key"
```

从输出选择一个与 DGX Spark 匹配的 64 字符 profile ID，填入：

```dotenv
NIM_MODEL_PROFILE=<64-character-profile-id>
```

运维人员只填写 `NIM_MODEL_PROFILE`。`v2-nim-prepare-online` 会将该值自动同步至
`VISION_MODEL_REVISION`；后者仅为旧证据结构保留的兼容别名，不需要人工重复维护。

建议先比较 NVFP4 Fast MTP 和 FP8 DFlash。选择依据是本项目的 1/3/5 图延迟、峰值统一内存和结构化输出质量，不根据精度名称推测结果。

### 4.3 下载、固定并封存

```bash
make v2-nim-prepare-online \
  NIM_PREPARE_ARGS="--ngc-key-file /secure/ngc_api_key"
```

该步骤会在目标 GB10 上确认 profile，固定容器 digest，把指定模型 profile 下载到私有 cache，构建绑定当前 Git commit 的网关，并生成权限为 `600` 的准备清单。它同样使用隔离的临时 Registry 登录并在退出时清理。NGC key 只进入瞬时登录/下载进程，不会被复制到仓库、配置、长期 Docker 凭据或运行容器。下载期间，`root`、Docker daemon 和具有 Docker/docker-group 权限的管理员能够管理或检查容器进程，属于受信任边界；完成准备后应撤销或轮换短期 key。

默认在下载前要求 NIM cache 所在卷至少保留 64 GiB 可用空间；可通过
`NIM_PREPARE_MIN_FREE_BYTES` 按现场磁盘规划提高门槛，不建议在没有核实镜像、cache、
业务数据和备份共同容量时调低。

同一步骤还会建立一个小型 `.venv-v2`，只用于设备配发、私有资料审计和端到端
验收；它不承载模型服务。这样 NIM 路径无需再运行 vLLM 的下载流程，也能直接使用
`v2-nim-enroll`、`private-artwork-*` 和 `v2-nim-smoke`。

随后关闭批准的下载网络。运行配置必须保持：

```dotenv
RELICSCOPE_OFFLINE_MODE=true
NIM_DISABLE_MODEL_DOWNLOAD=1
NIM_MAX_VIDEOS_PER_PROMPT=0
NIM_TELEMETRY_MODE=0
```

第一阶段只处理图片。Qwen3.6 NIM 的视频路径需要额外的 ARM64 FFmpeg 8 共享库和独立资源验收，因此不与图片发布绑定。

### 4.4 离线预检并启动

```bash
make v2-nim-preflight
make v2-nim-start
make v2-nim-health
```

预检拒绝非 ARM64/DGX Spark/GB10、未固定源码或容器、错误 profile、缺失 cache、运行期凭据、不安全目录、公开模型端口，以及图片阶段意外启用视频。

`v2-nim-health` 证明 HTTPS 网关、持久队列和模型端点已就绪。它不能替代一次真实的多图 completion。

Qwen3.6 的 DGX Spark 专用 `1.7.1-variant` 不支持 Docker 的自定义 `--user`
参数，因此 NIM 容器保留其发行版规定的运行用户；网关与 ingress 仍保持非 root、
移除 Linux capabilities 并启用 `no-new-privileges`。NIM cache 必须对该容器可写，
但只挂载到模型容器，并由内部 Docker 网络、`NIM_DISABLE_MODEL_DOWNLOAD=1` 和不注入
运行期 NGC 凭据共同限制。不要为了表面上的统一加固重新加入 `user:` 或只读 cache，
否则该特定容器可能无法启动。

## 5. Scout 配对

Caddy 首次启动后导出受控试运行 CA：

```bash
make v2-nim-export-ca
```

结果位于 `runtime/provisioning/scout-local-ca.crt`。Android debug/reference build 可以信任用户安装 CA；正式 Android 包应使用机构信任链和正式配发策略。

创建一台设备的单独凭据：

```bash
make v2-nim-enroll \
  SCOUT_NAME="Scout 01" \
  SCOUT_SERVER_URL="https://scout.spark.local:8443" \
  SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"
```

配发文件包含只显示一次的设备 token。导入目标 Scout 后，把文件移入受控密码库或按批准流程销毁。设备丢失时立即 disable 该 device ID。

## 6. 授权测试资料

真实艺术品图片、清单和客户信息不能进入公开 Git 仓库。先做只读审计：

```bash
make private-artwork-audit \
  PRIVATE_ARTWORK_ARCHIVE=/absolute/path/artworks.zip
```

确认审计结果后，导入本机已忽略目录：

```bash
make private-artwork-import \
  PRIVATE_ARTWORK_ARCHIVE=/absolute/path/artworks.zip \
  PRIVATE_ARTWORK_BATCH=customer-pilot-001
```

导入器只接收经过解码、magic、大小、像素、压缩比和路径安全检查的 PNG/JPEG/PDF。媒体按内容哈希存储；原目录只记为 `candidate_group`，不会被系统擅自当成器物主键；视角在人工核对前保持 `unclassified`。PDF 只登记并标记人工复核，不自动把其文字当作系统指令。

## 7. 真实端到端验收

选择同一件器物的正面、背面和底足等授权图片：

```bash
make v2-nim-smoke SCOUT_SMOKE_ARGS="\
  --provisioning runtime/provisioning/scout-01.json \
  --ca-cert runtime/provisioning/scout-local-ca.crt \
  --timeout-seconds 900 \
  --capture FRONT=/private/test/front.jpg \
  --capture BACK=/private/test/back.jpg \
  --capture BASE=/private/test/base.jpg"
```

通过结果必须同时证明：

- 同一 job 从排队进入成功终态；
- 每张图通过服务器端质量和完整性检查；
- 每条观察绑定实际 capture ID 与视角；
- served model、NIM image digest、profile ID、请求 ID、请求/输出哈希和延迟已记录；
- 没有模型时保持排队/降级，不用规则文本冒充 GPU 推理；
- `reference_library_used=false`、`rag_used=false`、`agent_used=false`；
- `authenticity_state=NOT_ASSESSED`。

随后按 [`V2_SPARK_ACCEPTANCE.md`](V2_SPARK_ACCEPTANCE.md) 完成 1/3/5 图、冷/热、并发 1/2、连续任务、断网、重启、撤销设备和低磁盘测试。所有性能值均来自客户机器生成的 `runtime/` 证据，不能用开发电脑测试替代。

## 8. Qwen3.8 与 Nemotron A/B

主服务验收通过后，再使用第二台 Spark 或在维护窗口内时分复用：

1. 冻结同一批输入文件、SHA-256、视角顺序、prompt 和 schema；
2. Qwen3.8-27B-FP8 测中文可见观察与结构报告；
3. Nemotron 3 Nano Omni 测原生视频/音频/图像证据抽取；
4. 记录 p50/p95、峰值统一内存、JSON 通过率、违规率和专家盲评；
5. 只有结果和运维指标共同胜出才修改默认模型。

Qwen3.8 当前 NIM 文档给出通用单 GPU FP8 门槛，没有单列 DGX Spark 优化 profile。Nemotron 官方支持 Spark，但训练与评测主要为英文。两者都必须在本项目中文陶瓷输入上验证。

## 9. 停止、备份与恢复

```bash
make v2-nim-stop
```

停止容器不删除媒体、SQLite、NIM cache 或 CA。客户需在上线前批准原始图片、结果、日志和设备凭据的保留期限、加密备份介质、恢复负责人、删除审批和容量告警。

在正式加密保留策略完成前，只处理获得授权的测试资料。通用 V2 备份边界见 [`V2_BACKUP_RESTORE.md`](V2_BACKUP_RESTORE.md)；NIM cache 可由冻结 profile 重新准备，不应与客户媒体混装。

## 10. 交接清单

| 交付项 | 工程交接证据 |
|---|---|
| 源码 | Git URL、branch、commit、干净状态 |
| Spark 身份 | 型号、序列/资产号、DGX OS、driver、CUDA、磁盘 |
| 运行时 | NIM image digest、profile ID、served model、license review |
| 网络 | Scout hostname/IP/port、无公网转发、模型端口未暴露 |
| 安全 | 运维账号、密钥位置/权限、CA、设备撤销流程 |
| 数据 | 私有目录、授权记录、保留/销毁/备份责任人 |
| 验收 | 多图 smoke、连续任务、断网/重启、低磁盘、Android 真机 |
| 运维 | 启停、健康、日志、备份、恢复、人工故障切换 |
| 未完成项 | 真机数值、正式 Android 签名/MDM、机构 CA、领域准确性评测 |

完成目标 Spark 实机闭环并保留证据后，状态可从 `DEPLOYMENT_READY` 更新为 `HARDWARE_VERIFIED`；完成客户业务评审和签署后，状态更新为 `PILOT_ACCEPTED`。
