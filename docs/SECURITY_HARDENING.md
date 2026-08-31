# 安全加固基线

## 1. 威胁边界

本Demo处理高价值器物图像、来源、专家意见和检测记录。主要风险包括：公网暴露、共享key泄漏、未经授权的数据导出、错误模型/知识版本、容器供应链、运行数据篡改和运维误操作。

当前包提供私网绑定、共享服务key、只读应用容器、非root应用用户、最小Linux capabilities、离线缓存和哈希审计。它尚未提供机构SSO、用户级RBAC、TLS/mTLS、外部可信时间戳、HSM签章、SIEM或高可用数据库；这些是正式生产前的硬门槛。

## 2. 网络分区

- 默认单机配置把 `app`、`vision` 与 `reference-embedding` 放在 `internal: true` 的专用 Docker 网络中，只发布应用入口；两个模型端口都不得映射到主机。`make health ROLE=single` 与实机验收必须核对网络的 `Internal=true` 和模型容器无 published port。
- 仅在启用次级双机拓扑时，Spark A 模型端口才允许 Spark B 固定 IP；其他来源一律拒绝。
- 默认单机应用保持 loopback；仅在启用双机或受控局域网入口时，Spark B/应用的 8088 才允许经批准终端网段。
- 管理SSH使用独立管理网或跳板机，不与模型数据平面混用。
- 25/100 GbE直连或VLAN不路由到公网。
- 运行阶段阻断公网出站；临时更新窗口需审批、记录和关闭确认。
- 正式机构部署在应用入口前增加 TLS/mTLS 反向代理、按路由请求大小限制、速率限制和身份认证。普通图片与专用视频登记端点使用不同上限，不能把 256 MiB 视频上限放宽到所有 API。

## 3. 身份与密钥

- 服务key至少32字节，权限`600`；两台使用同一环境专用key。
- key只通过只读Docker secret文件注入，不写进`.env`、镜像、源码、命令参数或聊天。
- HF token只在预缓存的一次性容器挂载；运行容器不接收该token。
- 当前共享key只适合受控Demo。生产应升级为每服务独立身份、短期凭据或mTLS，并建立轮换、撤销和审计。
- `VLLM_API_KEY` 仅是纵深控制。vLLM 官方公告 [GHSA-94f4-hr76-p5j6](https://github.com/vllm-project/vllm/security/advisories/GHSA-94f4-hr76-p5j6) 将 0.20.0 列入认证绕过影响范围；Nemotron 当前 Spark playbook 又要求该版本，因此必须保留内部网络、无模型端口映射、loopback 应用入口和主机防火墙补偿控制。完成 Nemotron 对 vLLM 0.22+ 的兼容与回归验收后优先升级。
- Codex登录凭据、OpenAI API key与RelicScope服务key是不同信任域，禁止互相复用。

## 4. 主机和容器

- 使用机构批准的DGX OS、驱动、固件和安全更新节奏。
- 启用全盘加密、安全启动及主机审计时，遵循站点支持矩阵。
- 应用以非root UID/GID运行，根文件系统只读，`cap_drop: ALL`、`no-new-privileges`。
- NVIDIA vLLM镜像的用户模型和设备权限需纳入站点容器基线审核，不能因官方来源跳过扫描。
- Qwen 基线不启用远程模型代码。Nemotron 候选需要的 `--trust-remote-code` 只在该 profile 打开，并用缓存中的不可变 revision 同时绑定模型、tokenizer 与 code；禁止运行期按 `main` 漂移。
- 参考 embedding sidecar 只接受本机缓存中 40/64 位不可变 revision，模型缓存只读，派生模块/临时缓存位于容器 tmpfs；根文件系统只读、`cap_drop: ALL`，且通过同一 Docker secret 验证应用请求。Sentence Transformers/Transformers 依赖固定在专用 lock 文件中。
- 严格 DGX Spark 身份由三项共同构成：device-tree 主机型号包含 `DGX Spark`、`nvidia-smi` 返回的 GPU 名称包含 `GB10`、运行架构为 `aarch64`/`arm64`。仅有 NVIDIA GPU、仅有 GB10 或仅有 ARM64 都不能标记为 DGX Spark 已核验。
- 镜像使用固定tag并记录ID；生产进一步使用digest、签名、SBOM和漏洞扫描。
- 不把Docker socket挂进应用容器。
- 日志轮转已有限额；正式站点应把安全事件导向受控日志系统，同时避免记录原始对象和token。

## 5. 文件和数据

- `.env`、服务key、HF token权限`600`；数据、缓存、备份目录权限`700`。
- 真实参考图、负向案例证据、来源/许可、专家审签、向量索引和校准记录只进入 `RELICSCOPE_DATA_HOST_DIR/reference-library`；它们不进入公开 Git、构建上下文或发布包。向量索引和校准记录必须绑定原图/manifest/index/模型 revision/指令哈希。
- 模型运行期只读；默认单机的业务数据由同机应用用户写入，双机扩展的权威业务数据由 Spark B 应用用户写入。
- 备份加密、异地/离线复制并定期恢复演练。
- 数据导出保留操作者、时间、对象、目的和hash；生产版增加机构签章和可信时间戳。
- 删除遵循机构保留政策，当前脚本不会自动删除历史数据或备份。
- 默认单机中，原始视频保存在同一 Spark 的受控应用目录，校验后只经 `internal: true` 容器网络送往同机模型端点，不发生 Spark 间传输。
- 双机扩展中，Spark B 保存权威原视频。代表帧路径只向 Spark A 发送当前任务需要的有限派生帧；显式调用原生视频路径时，Spark B 会把通过服务端校验的受限整段视频请求字节经私有模型平面发送至 Spark A。Spark A 不应把请求体作为业务原件持久化，报告和普通日志均不得嵌入原视频字节。
- 登记路径可保存获准的其他视频容器供代表帧分析；原生模型路径只接受由服务端解析确认的单视频轨 H.264 MP4，并以容器时长为准。默认上限为 15 秒与 32 MiB，是当前实测 Demo 安全闸门；提高任一上限前必须按模型 profile 复验解码、延迟、统一内存、日志和结构化输出。浏览器文件名、MIME 声明、客户端时长和客户端哈希都不能单独作为信任依据。

## 6. 科学与模型安全

- 把模型输出标记为观察，绑定输入hash、模型版本、阈值、校准和适用范围。
- 低置信度、分布外对象、多模态冲突和质量失败进入拒绝/升级路径。
- 异常热图不等于真伪结论。
- 同一实物候选、相关参考和负向相似信号三者分开；无真实受控库或独立复拍/开集校准时保持 `CALIBRATION_REQUIRED`。负向案例命中不能直接生成假货结论，未命中也不能证明为真。
- `/api/health` 中的 `endpoint_identity_ready=true` 只表示模型端点在线且 configured/served identity 一致，不证明具体推理请求已经完成。对外声称“真实运行”必须引用验收包中的成功 `model_runs`，并核对 provider request ID、finish reason、模型 revision、应用 commit、输入/输出哈希和完成时间。
- Nemotron 是 English-first 原生视频候选；机器门槛不能自动晋升。专家必须按中文术语或忠实翻译、时序完整性、事实/推测分离、边界安全和可操作性评分。A/B 完成或异常恢复后都要核对 Qwen 为默认 served model。
- 真实辐射、激光和取样操作必须由有权限人员批准，并由仪器联锁和站点程序控制；应用建议不直接绕过设备安全控制。
- 第三方框架升级需重新检查许可证、输入格式、数值差异和基准表现，不能只看版本号。

## 7. 上线前最低验收

1. 默认单机只暴露 loopback 应用入口；双机扩展中，从非允许网段访问 8001/8003/8088 均被拒绝。
2. 模型端口没有主机映射，单机 Docker 网络 `Internal=true`；无 key 的内部 `/v1` 请求被拒绝仅作为补充证据。
3. 运行期没有HF token挂载，断公网仍能启动。
4. 模型 ID、镜像 ID、模型 revision 和知识版本与发布 manifest 一致；`endpoint_identity_ready` 只登记为端点身份证据。
5. 服务key轮换演练完成，旧key被拒绝。
6. 单机验收包含图片、服务端解析 H.264 MP4 和报告三类成功 completion 记录；严格 DGX 身份三项均通过。
7. 双机扩展中 Spark A 停机时 Spark B 明确降级，不生成伪模型结果；原生与代表帧两条媒体边界分别验证。
8. 备份恢复到新目录成功，审计链可重新验证。
9. 日志抽查不含密钥、完整器物图或不必要个人信息。
10. A/B 后 served model 已恢复 Qwen，且专家评审未被机器 scorecard 替代。
11. 真实仪器接入前完成校准、权限、安全联锁和失败模式验收。
12. 50 件参考库、至少 10 件负向案例、向量索引和校准记录全部通过哈希/模型身份复核；缺失时 readiness 必须失败关闭。
