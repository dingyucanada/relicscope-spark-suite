# 运维、备份、恢复与回滚

## 1. 日常开机检查

1. 确认两台Spark系统时间同步、磁盘无只读/满盘告警；启用视频 Demo 时额外核对 Spark B 原始媒体与备份空间。
2. 确认25/100 GbE专用链路和管理链路均正常。
3. 先检查Spark A，再检查Spark B：

```bash
make status ROLE=spark-a
make health ROLE=spark-a
make status ROLE=spark-b
make health ROLE=spark-b
```

4. 确认`/api/health`中的`mode=dual-node`、`offline=true`、`tensor_parallel=false`、节点ID、知识版本和模型ID符合发布记录。
5. 浏览器只访问Spark B入口，不直接访问模型端口。

## 2. 启停

```bash
# 开机顺序
make start ROLE=spark-a
make start ROLE=spark-b

# 关机顺序
make stop ROLE=spark-b
make stop ROLE=spark-a
```

启动脚本尽量幂等：已运行容器保持不变，停止的已知模型容器会重新启动，Compose应用会协调到声明状态。修改模型、端口或挂载后，应先停止旧角色再启动，避免沿用旧容器配置。

## 3. 日志与诊断

```bash
# Spark A
docker logs --tail 200 relicscope-vision
docker logs --tail 200 relicscope-embedding

# Spark B
docker compose --env-file .env -f compose.yml logs --tail 200 app
docker compose --env-file .env -f compose.yml --profile reasoner logs --tail 200 reasoner
```

不把完整器物图片/视频、专家意见、服务key或HF token复制到工单、聊天或Codex任务。分享诊断前先审查并脱敏。媒体问题优先记录会话 ID、服务端 SHA-256、字节数、媒体类型、错误类别和时间，不附原始内容。

## 4. 一致性备份

只有Spark B保存会话、原始图片/视频、派生帧、证据和报告。执行：

```bash
make backup ROLE=spark-b
```

备份脚本会：

- 检测应用是否正在运行；
- 短暂停止`app`，制作一致性tar归档，再恢复原运行状态；
- 写入SHA-256和manifest；
- 排除服务密钥、HF token、模型缓存和`.env`。

默认目录为`BACKUP_DIR`。生产应配置为加密、受控且与运行数据不同的存储设备，并把归档复制到机构备份系统。视频会显著增加备份时间和体积；上线前以最大允许视频、预期日会话量和保留天数测算容量，并实际复演备份与恢复，不能只检查文件存在。

## 5. 恢复

```bash
make restore ROLE=spark-b ARCHIVE=/absolute/path/relicscope-data-YYYYMMDDTHHMMSSZ.tar.gz
make health ROLE=spark-b
```

恢复脚本默认要求并校验`.sha256` sidecar，拒绝绝对路径、`..`、链接和设备文件，停止正在运行的应用，并将当前数据移动为：

```text
<data-dir>.pre-restore-<timestamp>
```

旧数据不会自动删除。如恢复失败，新提取内容保留为`.failed-restore-*`，脚本尽可能把旧数据移回原位。确认多个业务周期后再按机构保留政策处理这些目录。

## 6. 应用镜像回滚

先做数据备份，再在`.env`中设置已缓存的固定镜像：

```dotenv
PREVIOUS_APP_IMAGE=relicscope-ai-demo:<previous-fixed-tag>-arm64
```

执行：

```bash
./deploy/rollback.sh --role spark-b --restore-app
make health ROLE=spark-b
```

回滚不会自动降级SQLite模式或数据结构。未来若引入数据库迁移，每个发布必须同时提供经过测试的前向、兼容和恢复方案；不能只切换镜像。

## 7. 发布升级

1. 用`make package ROLE=all`生成不含凭据和运行数据的发布包。
2. 校验外层SHA-256和包内`SHA256SUMS`。
3. 在隔离目录解包，不覆盖当前发布目录。
4. 比较`.env.example`，人工合并新增配置，禁止直接覆盖现有`.env`。
5. 在两台节点分别运行预检。
6. 备份Spark B数据。
7. 先升级Spark A并验收，再升级Spark B。
8. 保留上一发布目录、应用镜像和恢复备份，直到观察期结束。

## 8. 事件处置

- **密钥疑似泄漏：** 停止两端服务；生成新key；通过安全通道在两端原子替换；重启并验证旧key被拒绝；审查日志。
- **模型身份不符：** 停止该模型容器；核对预缓存manifest、镜像ID和模型revision；禁止用“能响应”代替模型身份验收。
- **数据库/磁盘异常：** 停止Spark B写入；保存日志和文件系统证据；从已验证备份恢复到新目录。
- **视频上传导致空间不足：** 停止接收新媒体，保留数据库和已完成哈希；按机构保留政策处置，禁止直接删除仍被会话、证据图或报告引用的文件。
- **浏览器无法解码视频：** 保留原视频登记失败/预览状态，换用当前支持的 MP4/WebM 或重新采集；不得只改扩展名规避格式验证。
- **链路降速：** 检查光模块/线缆、交换机、网卡协商和错误计数；不要通过关闭健康检查掩盖问题。
- **科学结果冲突：** 保留原始结果、参数、质量指标和状态；标记`CONFLICT/UNCERTAIN`并升级专家，不覆盖先前证据。
