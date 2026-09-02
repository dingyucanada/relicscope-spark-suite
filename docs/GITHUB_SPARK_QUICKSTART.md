# GitHub / Codex / DGX Spark 快速复现

本指南对应 RelicScope V2 的当前推荐基线：Android Scout 把授权的多视角图片送到一台
DGX Spark，由本机 HTTPS 网关、持久任务队列和 Qwen3.6-35B-A3B NVIDIA NIM 完成结构化
可见观察。第二台 Spark 可以用于备用、容量或候选模型评测，不是主链路启动条件。

当前证明状态是 `DEPLOYMENT_READY`：代码与配置可以审查和复现，客户 Spark、Android
真机、现场网络及真实业务数据仍需按验收清单留证。开发电脑上的确定性 Demo 不代表
GB10、NIM、GPU、断网运行或科学仪器已经验证。

## 1. 获取并冻结源码

获授权的部署人员可从公开仓库只读克隆，无需在 Spark 上保存个人 GitHub 凭据：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git fetch --all --tags
git switch main
git status --short
git rev-parse HEAD
```

正式部署应使用负责人批准的 release tag 或 commit。记录完整 commit，且只从干净工作树准备运行环境。不要把访问令牌写入 clone URL、脚本、
`.env` 或聊天记录。

## 2. Codex 是部署助手，不是运行依赖

Codex 可以协助检出、配置复核和排障。建议先让它阅读 `AGENTS.md`、本指南、
[`V2_SCOUT_SPARK_DEPLOYMENT.md`](V2_SCOUT_SPARK_DEPLOYMENT.md) 和
[`V2_SPARK_ACCEPTANCE.md`](V2_SPARK_ACCEPTANCE.md)，再执行固定的 Make 入口。

优先在受控管理工作站使用 Codex，通过批准的 SSH 通道操作 Spark。即使 Codex 已退出，
RelicScope 也必须依靠冻结的源码、容器、配置和系统服务独立运行。不要把 Codex 放入应用
容器、systemd 或离线运行依赖；不要向它粘贴 NGC key、设备 token、客户图片或 `.env`。

## 3. 开发电脑：确定性代码冒烟

前置条件为 Python 3.11+、可用的 `venv`，以及首次安装时可访问批准的 Python 包源。

```bash
./scripts/reproduce-demo.sh --check-only
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
make check
```

浏览器打开 `http://127.0.0.1:8088`。该路径使用 `DEMO/SYNTHETIC` fixture，只能报告为
确定性降级复现；不得写成真实本地 VLM、原生视频、NIM 或 DGX Spark GPU 验收。

## 4. 目标 Spark 前置条件

管理员先确认并留档：

- 目标机是 ARM64 DGX Spark，GPU 可被 NVIDIA Container Toolkit 访问；
- Docker、Docker Compose v2 和 NVIDIA Container Toolkit 满足所选 NIM release 要求；
- NGC NIM 权限、模型许可与机构使用条款已经批准；
- Spark 私有 LAN IP、Scout 主机名、现场 DNS、持久存储和加密备份位置已经确定；
- 只在批准的联网准备窗口下载，运行阶段阻断公网出站；
- 使用短期、最小权限的 NGC Personal API Key，并保存在 Spark 上权限为 `600` 的独立文件；准备完成后撤销或轮换；
- 下载窗口内，主机 `root`、Docker daemon 以及具有 Docker/docker-group 权限的管理员属于受信任边界。

本仓库安装脚本另外要求 Docker Compose 2.30+。不要在演示当天首次升级 DGX OS、驱动、
容器工具包或模型镜像。以所选 NIM 版本的 NVIDIA 官方
[前置条件](https://docs.nvidia.com/nim/vision-language-models/latest/get-started/prerequisites.html)、
[安装说明](https://docs.nvidia.com/nim/vision-language-models/latest/get-started/installation.html)
和 [DGX Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) 为准。

## 5. 单台 Spark：NIM 正式路径

### 5.1 初始化，不下载、不启动

```bash
make v2-nim-install
```

检查权限为 `600` 的 `.env.v2.nim`，至少确认：

```dotenv
SCOUT_BIND_IP=192.168.50.20
SCOUT_HOSTNAME=scout.spark.local
VISION_MODEL=qwen/qwen3.6-35b-a3b
VISION_MODEL_SOURCE=qwen/qwen3.6-35b-a3b
NIM_SERVED_MODEL_NAME=qwen/qwen3.6-35b-a3b
RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB=8
NIM_MAX_IMAGES_PER_PROMPT=8
NIM_MAX_VIDEOS_PER_PROMPT=0
```

三个模型名称必须一致；Scout 单任务图片上限不得大于 NIM 图片上限。`SCOUT_BIND_IP` 只能
使用这台 Spark 的私有地址。不要发布 NIM 8000 端口，也不要把 Scout 8443 转发到公网。
这个 DGX-Spark 专用 `1.7.1-variant` 不支持 Docker 自定义 `-u`；保持镜像规定的运行用户和
仅供 NIM 使用的可写私有 cache，不要自行加入 `user:` 或把该 cache 改成只读。

### 5.2 在批准的联网窗口认证并发现 profile

Qwen3.6 的 `1.7.1-variant` 早于 NIM VLM 2.1.1 的 keyless 门槛。按 NVIDIA 官方要求先
完成 NGC 条款与权限，再让仓库脚本在本次批准窗口使用隔离的临时 Docker 配置认证
`nvcr.io`：

```bash
make v2-nim-list-profiles \
  NIM_PROFILE_ARGS="--allow-network --ngc-key-file /secure/ngc_api_key"
```

脚本从权限为 `600` 或 `400` 的文件读取 key，成功或失败都会自动退出 Registry 并删除
临时凭据目录，不会覆盖现有 Docker 登录。下载期间，key 会进入瞬时登录/下载进程；
`root`、Docker daemon 和具有 Docker/docker-group 权限的管理员能够管理或检查这些进程，
因此必须纳入受信任边界。不能把 key 写入命令参数、环境文件、Compose、Git 或终端记录，
准备完成后应撤销或轮换该短期 key。

profile 与 NIM 镜像、精度和目标 GPU 绑定。只从本机 `list-model-profiles` 输出选择一个
64 字符 ID，不要从另一台机器或文档复制。填入 `.env.v2.nim`：

```dotenv
NIM_MODEL_PROFILE=<64-character-profile-id>
```

只需人工填写 `NIM_MODEL_PROFILE`。`v2-nim-prepare-online` 会把选定 profile 自动同步到
`VISION_MODEL_REVISION`；后者是兼容旧证据字段的别名，不应重复手工填写。

可用 profile 的显存、磁盘与推理特征见 NVIDIA 的
[Qwen3.6 NIM 支持矩阵](https://docs.nvidia.com/nim/vision-language-models/1.7.0/support-matrix.html#qwen3-6-35b-a3b)。
最终选择以本项目 1/3/5 图的延迟、峰值统一内存、JSON 合规率、安全边界和专家评审为准。

### 5.3 准备缓存并关闭联网窗口

```bash
make v2-nim-prepare-online \
  NIM_PREPARE_ARGS="--ngc-key-file /secure/ngc_api_key"
```

准备步骤应固定容器 digest、下载所选 NIM profile、构建绑定当前 Git commit 的 Scout
网关，并写入私有 preparation manifest。脚本默认要求 cache 卷在下载前至少有 64 GiB
可用空间；现场可以提高 `NIM_PREPARE_MIN_FREE_BYTES`，不要在未核算容量时降低。确认结果后，关闭批准的公网出站并保持：

```dotenv
RELICSCOPE_OFFLINE_MODE=true
NIM_DISABLE_MODEL_DOWNLOAD=1
NIM_MAX_VIDEOS_PER_PROMPT=0
NIM_TELEMETRY_MODE=0
```

环境变量不能代替网络控制。可证明的离线运行需要在主机或上游网关阻断公网出站后重启
并验收。第一阶段只处理图片；Qwen3.6 NIM 的视频路径另需 ARM64 FFmpeg 8 依赖和独立
资源测试，不与图片基线一起启用。

### 5.4 离线启动与 readiness

```bash
make v2-nim-preflight
make v2-nim-start
make v2-nim-health
```

预检通过只说明目标硬件身份、冻结输入、私有缓存、容器和网络边界符合配置；健康检查
只说明 HTTPS 网关、持久队列和模型端点 ready。两者都不能代替一次真实多图 completion。

## 6. 配对 Android Scout

Caddy 首次启动后导出受控试运行 CA：

```bash
make v2-nim-export-ca
```

结果位于 `runtime/provisioning/scout-local-ca.crt`。Android debug/reference build 可以信任
用户安装的本地 CA；正式 Android 包应使用机构信任链与正式配发策略。

为每台 Scout 创建独立凭据：

```bash
make v2-nim-enroll \
  SCOUT_NAME="Scout 01" \
  SCOUT_SERVER_URL="https://scout.spark.local:8443" \
  SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"
```

配发文件权限必须为 `600`，并包含只显示一次的设备 token。导入目标 Scout 后，把它移入
受控密码库或按批准流程销毁；设备遗失时立即停用对应 device ID。

## 7. 真实多图闭环

使用同一件器物的授权图片，并显式标注视角：

```bash
make v2-nim-smoke SCOUT_SMOKE_ARGS="\
  --provisioning runtime/provisioning/scout-01.json \
  --ca-cert runtime/provisioning/scout-local-ca.crt \
  --timeout-seconds 900 \
  --capture FRONT=/private/test/front.jpg \
  --capture BACK=/private/test/back.jpg \
  --capture BASE=/private/test/base.jpg"
```

成功结果至少要证明：

- 同一 job 从排队进入成功终态，每张图通过服务器端质量与完整性检查；
- 每条观察绑定实际 capture ID、视角和输入哈希；
- served model、NIM image digest、profile ID、请求 ID、输出哈希和延迟已记录；
- `reference_library_used=false`、`rag_used=false`、`agent_used=false`；
- `authenticity_state=NOT_ASSESSED`，且输出没有确定真伪、年代、窑口、作者或价值。

随后按 [`V2_SPARK_ACCEPTANCE.md`](V2_SPARK_ACCEPTANCE.md) 完成 1/3/5 图、冷/热、连续任务、
并发、断网、重启、低磁盘、设备撤销与 Android 真机测试。只有目标机生成的 `runtime/`
证据通过，状态才能从 `DEPLOYMENT_READY` 更新为 `HARDWARE_VERIFIED`；现场签字后才是
`PILOT_ACCEPTED`。

## 8. 第二台 Spark 与候选模型

一台 Spark 必须独立运行主闭环。第二台可以运行同一基线作为人工切换备用，也可以在隔离
环境中评测 Qwen3.8、Nemotron、LoRA 或批处理任务。两台设备的 128 GB 统一内存不会自动
合并；没有多机运行证据时，不能声称 256 GB、故障切换或分布式推理已经实现。

主服务验收通过后，按
[`MODEL_SELECTION_AND_SPARK_RUNTIME_2026-09.md`](MODEL_SELECTION_AND_SPARK_RUNTIME_2026-09.md)
冻结输入、prompt、schema 和评分规则，再做顺序 A/B。Qwen3.8 是中文多模态候选；
Nemotron 3 Nano Omni 是原生视频候选，其官方模型卡存在 English-only 语言边界。任何晋级
都需要目标 Spark 运维指标、机器门槛和领域专家盲评共同通过。

## 9. 数据与科学边界

- `.env*`、`secrets/`、`runtime/`、客户媒体、数据库与模型缓存都不能进入公开 Git。
- 真实艺术品资料先按 [`PRIVATE_ARTWORK_TEST_DATA.md`](PRIVATE_ARTWORK_TEST_DATA.md) 的授权、
  审计、导入与销毁流程处理。
- RGB 图片只能形成可见观察、拍摄质量判断、比较与补拍建议；不能单独形成真伪、确定年代、
  窑口、作者、价格、等级、来源或法律结论。
- 内置仪器数值属于回放资料；真实 Raman、XRF、HSI、X-ray、CT 或 TL 必须另行接入、
  校准和验收。
- 当前仓库未授予开源许可证；公开可读不等于允许复制、修改、再分发或商业使用。

## 10. 交接时必须留下的证据

| 类别 | 必需记录 |
|---|---|
| 源码 | Git URL、branch/tag、完整 commit、干净状态 |
| Spark | 资产号、DGX OS、driver、CUDA、磁盘与 GB10 证据 |
| NIM | image digest、profile ID、served model、许可审批 |
| 网络 | Scout hostname/IP/port、无公网转发、模型端口未发布 |
| 安全 | 运维账号、密钥位置/权限、CA、设备撤销与出站策略 |
| 数据 | 授权、私有目录、保留/销毁、加密备份与恢复负责人 |
| 验收 | 多图 smoke、断网/重启/失败场景、Android 真机与签字人 |

停止服务使用 `make v2-nim-stop`。停止容器不会删除媒体、SQLite、NIM cache 或 CA；任何
删除、恢复或保留期变更都必须由客户明确授权。
