# RelicScope V2 备份与恢复

本流程覆盖单台 DGX Spark 上的现场业务状态，并同时支持推荐的 NVIDIA NIM
运行路径和原有 vLLM 运行路径。当前 NIM 格式 v2 的已支持范围，是把业务数据恢复到
**原 DGX Spark、同一套已冻结且仍保存在本机的 OCI 镜像 ID**。它不是整机镜像，也不是
跨机迁移工具；操作系统、GPU 驱动、源码、容器镜像、模型缓存和凭据均不随归档搬运。

> 状态说明：脚本级安全检查与 Compose 配置检查已纳入仓库；真实 DGX Spark 上的 NIM
> 备份—清空—恢复演练仍待目标硬件部署时完成，未通过该演练前不得把它标记为生产级灾备。

## 1. 备份边界

两种运行路径都会备份：

- `RELICSCOPE_DATA_HOST_DIR`：Scout SQLite、可能存在的 WAL/SHM、作业状态、结构化结果和
  `scout-media` 原始图像；
- `CADDY_CONFIG_DIR`：不含密钥的 Caddy 运行配置状态；NIM 路径还会检查此目录中是否
  出现 PEM 私钥或 NGC key 特征，发现即拒绝归档；
- `manifest.json`：源码提交、服务版本、运行时类型、模型来源/版本、实际容器镜像引用与
  本地 OCI image ID；
- 逐文件 `SHA256SUMS` 和整个压缩包的相邻 SHA-256 sidecar。

推荐的 NIM 路径明确排除：

- `.env.v2.nim`、service API key、NGC API key；
- `NIM_CACHE_DIR` 及其中的模型文件；
- Hugging Face/vLLM 缓存和所有容器镜像；
- `CADDY_DATA_DIR`，因为其中包含本地 CA/服务器私钥；
- 任何明文私钥值。

因此 NIM 归档只承诺在原 Spark 上、使用 manifest 记录的同一本地 OCI image ID，恢复
业务数据与 Caddy 无密钥配置。它不包含 NIM cache、任何容器镜像或 TLS 私钥，也不提供
容器镜像的跨机转移。原机恢复会保留该机器当前的 `CADDY_DATA_DIR`。

跨机 NIM 恢复目前不属于已支持能力。若未来确需迁移，必须在归档流程之外，通过合规的
镜像供应渠道准备并验证 manifest 指定的精确镜像，重新建立 NIM cache 与 Caddy TLS
identity、更新 Scout 受信 CA，并在目标 Spark 上重新完成全部预检、真实多图闭环和灾备
验收；完成前不得宣称跨机恢复可用。

原有 vLLM 路径保持兼容：格式 v1 归档仍包含 `CADDY_DATA_DIR`，以保留已经配发 Scout 的
TLS 信任。此类归档包含本地 CA 私钥，只能存放在加密介质并按高敏感凭据管理。新的 NIM
路径不会生成这种归档。

服务端 SQLite 只保存 Scout token 的加盐哈希；Android/配发文件中的明文设备凭据不在
此备份范围内，必须由客户另行安全保管。

## 2. 创建备份

主机需提供 Docker Compose、Python 3、GNU `tar`/`sha256sum`、`rsync` 和 `flock`。
请使用安装 V2 的同一非 root 运维账号。输出目录必须是仓库和所有受管目录之外的绝对
路径，建议使用加密移动盘或受控 NAS。

NVIDIA NIM（推荐）：

```bash
make v2-nim-backup \
  V2_BACKUP_ARGS="--output-dir /media/secure-backup/relicscope-v2-nim"
```

原有 vLLM：

```bash
make v2-backup \
  V2_BACKUP_ARGS="--output-dir /media/secure-backup/relicscope-v2"
```

脚本与设备注册、启用、停用和换钥共用独占维护锁。它先预同步媒体，随后短暂停止
`ingress` 和 `gateway`，做最终增量同步，再恢复两者此前的运行状态；`vision` 无需停止。
这样 SQLite/WAL、媒体索引和 Caddy 配置来自同一个无写入窗口。

归档前会把选择的环境文件、Compose 项目与实际容器交叉核验：

- gateway 的源码提交和服务版本；
- 模型名称、模型来源、版本/运行 profile 与实际 served model；
- vision runtime 的镜像引用和本地 OCI image ID；
- ingress 的 Caddy immutable image ref 和本地 OCI image ID；
- NIM 路径要求 `NIM_VLM_IMAGE` 已固定为 registry digest，并要求 gateway、
  `NIM_MODEL_PROFILE` 与 `NIM_SERVED_MODEL_NAME` 三方身份一致。

工作树不干净、运行容器与配置不一致、NIM 镜像未固定或缓存/秘密目录落入归档范围时，
脚本都会直接退出。

成功后得到必须一起保管的两个文件：

```text
relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz
relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

归档仍包含艺术品图像和设备身份数据，应采用全盘加密、最小权限、第二介质与异地副本，
不得上传到公共对象存储。

## 3. 恢复前准备

1. 检出 manifest 记录的 `source.git_commit`，保持工作树干净。
2. 选择与备份相同的运行路径，生成本机 `.env.v2.nim` 或 `.env.v2` 和新的 service key。
3. 对 NIM 格式 v2，确认操作发生在原 Spark，且 manifest 指定的 runtime image 与完全
   一致的本地 OCI image ID 仍可用；备份归档本身不能恢复或转移该镜像。
4. 确认原机所需的 `NIM_CACHE_DIR` 已存在且可用。NIM cache 不在备份中；如需重建，必须
   在受控联网窗口通过 `make v2-nim-prepare-online ...`，使用冻结的 `NIM_VLM_IMAGE` 与
   `NIM_MODEL_PROFILE` 重新准备。NGC key 只在该窗口通过受限文件输入，不写入环境文件
   或备份。
5. NIM 原机恢复保留当前 `CADDY_DATA_DIR`。跨机恢复不在本流程支持范围内，不能把准备
   新 TLS identity 当作本脚本已经支持跨机迁移的依据。
6. 把 `.tar.gz` 和同名 `.tar.gz.sha256` 放在同一目录，并确认空间足够同时容纳当前目录、
   staging 和回滚副本。

## 4. 执行恢复

NVIDIA NIM：

```bash
make v2-nim-restore \
  V2_RESTORE_ARGS="--archive /media/secure-backup/relicscope-v2-nim/relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz --confirm-restore"
```

原有 vLLM：

```bash
make v2-restore \
  V2_RESTORE_ARGS="--archive /media/secure-backup/relicscope-v2/relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz --confirm-restore"
```

`--confirm-restore` 是必需的显式确认。脚本会先：

- 校验归档 sidecar 和逐文件 SHA-256；
- 拒绝绝对路径、`..`、符号链接、硬链接、设备文件、重复路径和范围外成员；
- 校验 manifest 的精确 payload 边界和秘密/缓存排除声明；
- 在目标目录所在文件系统准备并复核 staging；
- 比对 runtime kind、源码、服务、模型来源、profile、served model、vision/Caddy 镜像引用
  和本地 OCI image ID。

NIM 恢复对运行时指纹实行 fail-closed：任何一项不一致都会拒绝，且不允许使用
`--allow-version-mismatch` 绕过。旧 vLLM 格式继续保留该参数，供已完成 schema、TLS 与
运行时兼容性评审后的人工迁移使用；它不表示兼容性自动成立。

该校验只验证原 Spark 上已存在的运行时条件，不会从归档恢复 NIM cache、拉取或导入容器
镜像，也不会重建 TLS 私钥。跨机镜像供应、TLS 重建和迁移验收需另行设计与批准。

切换时不会删除原目录，而会保留 UTC 时间戳的同级回滚副本。NIM 路径只切换业务数据和
Caddy 配置；vLLM 格式 v1 还会切换 Caddy data：

```text
<data-dir>.pre-restore-YYYYMMDDTHHMMSSZ
<caddy-config-dir>.pre-restore-YYYYMMDDTHHMMSSZ
<caddy-data-dir>.pre-restore-YYYYMMDDTHHMMSSZ   # 仅 vLLM 格式 v1
```

若切换中途失败，脚本会尝试自动回滚。验收完成前不要删除这些目录。

## 5. 恢复后验收

恢复完成后 Compose 保持停止。NIM 路径执行：

```bash
make v2-nim-preflight
make v2-nim-start
make v2-nim-health
```

vLLM 路径执行：

```bash
make v2-preflight
make v2-start
make v2-health
```

随后使用一台已配发的 Scout 提交一组新的多视角图像并取回结果，验证旧设备 token 哈希、
TLS 信任、作业/媒体/VLM/结果轮询闭环。还需抽查恢复前作业与媒体 SHA-256，并完成一次
断网重启验证。

跨机恢复不在当前验收路径内。若未来完成合规镜像供应、精确镜像恢复与 TLS identity
重建，目标 Spark 仍须从零执行部署预检、Scout CA 更新、真实多图闭环、断网重启和灾备
演练，形成新的验收记录。

目标 DGX Spark 上至少完成一次全流程演练，记录耗时、归档大小、NIM cache 重建耗时、
恢复前后样本作业哈希和健康检查结果，才可签署灾备验收。

## 6. 可恢复与需重建内容

| 内容 | NIM 格式 v2 | vLLM 格式 v1 |
|---|---:|---:|
| 作业、报告、Scout 设备哈希、媒体 | 恢复 | 恢复 |
| Caddy 无密钥配置 | 恢复 | 恢复 |
| Caddy TLS 私钥/本地 CA | 不归档；保留或重建 | 兼容恢复；按高敏感凭据保护 |
| NIM/HF/vLLM 模型缓存 | 不归档；原机保留，必要时按冻结 image/profile 合规重建 | 冻结 model revision 重建 |
| service/NGC/设备明文凭据 | 独立秘密管理 | 独立秘密管理 |
| 容器镜像 | 不归档、不跨机转移；要求原机同一本地 OCI image ID | 可复现部署重建 |
| 跨机恢复 | 当前不支持；需另行供应精确镜像、重建 TLS 并重新验收 | 保留格式 v1 兼容路径，仍需人工兼容性评审 |
| 源码、驱动、操作系统 | 不归档；另行按受控部署恢复 | 可复现部署重建 |
