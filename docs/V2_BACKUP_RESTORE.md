# RelicScope V2 主 Spark 备份与恢复

本文档覆盖主 Spark 上 V2 现场数据和本地 TLS 身份的灾难恢复。它不备份模型，也不能替代异地副本、磁盘加密和客户的数据保留策略。

## 1. 边界

备份包含：

- `RELICSCOPE_DATA_HOST_DIR` 的完整内容，包括 Scout SQLite 数据库、可能存在的 `-wal`/`-shm` 文件、作业状态、结构化结果和 `scout-media` 原始图像；净化后的模型输入不落盘，归档包含结果中记录的模型输入 SHA-256 与请求证明；
- `CADDY_DATA_DIR`，包括 Caddy 本地 CA、私钥、证书和 TLS identity；
- `CADDY_CONFIG_DIR` 的运行状态；
- 一份不含秘密的 manifest、逐文件 `SHA256SUMS`，以及整个 `tar.gz` 的相邻 SHA-256 sidecar。

备份明确排除：

- `.env.v2`；
- `SERVICE_API_KEY_FILE`；
- Hugging Face 模型缓存、vLLM 缓存和容器镜像；
- Scout 明文 bearer token。服务端 SQLite 只保存 token 的加盐哈希；Android/配发文件中的现有凭据应由客户独立安全保管。

因此，恢复到新 Spark 前仍需用当前部署流程准备代码、`.env.v2`、同一模型 revision、镜像和 service key。service key 不随数据归档流转，降低单一备份泄露后的横向风险。

## 2. 创建备份

主机需提供 Docker Compose、Python 3、GNU `tar`/`sha256sum`、`rsync` 和 util-linux 的 `flock`；脚本缺少任一依赖时会直接退出。以安装 V2 的同一个非 root 运维账号执行。输出目录必须是明确的绝对路径，建议位于加密移动盘、NAS 挂载点或受控的异地同步目录，且不能位于任一被备份目录或源码仓库中。备份、恢复与 Scout 设备的注册、启用、停用、换钥共用同一个独占维护锁，避免数据库身份状态在备份或恢复期间发生变化；设备列表使用只读连接，不获取维护锁。

```bash
cd /path/to/relicscope-spark-suite
./deploy/v2-backup.sh --output-dir /media/secure-backup/relicscope-v2
```

脚本先在服务运行期间预同步体积较大的媒体文件；随后停止 `ingress` 和 `gateway`，做一次最终增量同步，再恢复它们先前的运行状态。`vision` 模型服务无需停止。这个窗口保证 SQLite/WAL、媒体索引和 Caddy TLS 状态来自同一个无写入时段，同时把现场中断缩短到最终增量复制所需的时间。

创建归档前，脚本还要求源码工作树干净，并核对当前提交、`.env.v2`、实际 gateway
容器的 OCI revision、实际模型身份/revision、模型容器 digest，以及 ingress 实际使用的
Caddy immutable image ref 与本地 OCI image ID 一致。manifest 同时记录 Caddy ref 和 ID，
因此清单对应被备份服务的真实运行身份，而非碰巧位于目录中的另一份代码、标签或配置。

成功后得到两个必须一起保管的文件：

```text
relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz
relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

建议把二者复制到第二介质并定期做恢复演练。归档包含艺术品图像和设备身份数据，应使用全盘加密、最小权限和离线/异地副本；不要上传到公共对象存储。

## 3. 恢复前检查

1. 安装或检出与 manifest 中 `source.git_commit` 相同的 V2 代码，保持工作树干净。
2. 按部署文档生成本机 `.env.v2` 和 service key，并准备 manifest 记录的模型与 immutable revision。
3. 把 `.tar.gz` 和同名 `.tar.gz.sha256` 放在同一目录。
4. 确认目标 Spark 有足够空间同时容纳“当前目录 + 恢复目录 + 回滚目录”。
5. 在维护窗口执行；恢复脚本会停止整个 V2 Compose，并在完成后保持停止状态。

若当前代码、模型或 ingress Caddy 的 immutable image ref/本地 OCI image ID 与备份 manifest 不一致，脚本默认拒绝恢复。只有在已完成 schema、TLS 与运行时兼容性评审时才可增加 `--allow-version-mismatch`，该参数不代表兼容性已经成立。旧版 manifest 若未记录 Caddy 身份，也只能经过这一显式评审开关恢复。

## 4. 执行恢复

`--confirm-restore` 是必需的显式确认：

```bash
cd /path/to/relicscope-spark-suite
./deploy/v2-restore.sh \
  --archive /media/secure-backup/relicscope-v2/relicscope-v2-backup-YYYYMMDDTHHMMSSZ.tar.gz \
  --confirm-restore
```

脚本在停止服务前完成以下工作：

- 校验归档 sidecar SHA-256；
- 拒绝绝对路径、`..`、符号链接、硬链接、设备文件、重复路径和范围外成员；
- 校验 manifest 和每一个 payload 文件的 SHA-256；
- 在各目标目录的同一文件系统上准备恢复 staging 目录。

切换时，原目录不会被删除，而会移动为三个带 UTC 时间戳的同级回滚目录：

```text
<data-dir>.pre-restore-YYYYMMDDTHHMMSSZ
<caddy-data-dir>.pre-restore-YYYYMMDDTHHMMSSZ
<caddy-config-dir>.pre-restore-YYYYMMDDTHHMMSSZ
```

若切换中途失败，脚本会尝试把这些目录移回原位，并保留已经切入但失败的数据副本。不要在验收完成前删除回滚目录。

## 5. 恢复后验收

恢复成功后 Compose 仍为停止状态。依次执行：

```bash
make v2-preflight
make v2-start
make v2-health
```

然后使用一台恢复前已配发的 Scout 和它原有的设备凭据，提交一组新的多视角图像并取回结果。该 smoke 同时验证：

- SQLite 中的旧设备 token 哈希仍可认证；
- 恢复的 Caddy TLS identity 与 Scout 已安装的 CA/证书信任一致；
- 作业、媒体、VLM 和结果轮询的端到端链路正常。

验收还应抽查恢复前的已完成作业及其媒体 SHA-256。确认业务、TLS 和旧设备凭据都正常，并把新备份写入第二介质后，才由客户的数据保留策略决定何时清理 `.pre-restore-*` 回滚目录。

## 6. 能恢复什么，不能恢复什么

此流程可恢复 V2 的现场作业状态、Scout 设备注册记录、媒体、报告数据和 Caddy 本地 CA。它不会自动恢复 Spark 操作系统、Docker、GPU 驱动、容器镜像、模型权重、源码、`.env.v2` 或 service key。后者由可复现部署流程和独立秘密管理负责。
