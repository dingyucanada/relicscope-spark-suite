# 单台 DGX Spark：50 件瓷器参考库与负向案例部署

> **路径状态：Legacy / 独立实验。** 本文对应 `compose.single.yml` 与旧版浏览器/科学工作流，尚未接入当前 `compose.v2.nim.yml` + `app.scout_main` 的 Scout V2 NIM 主链路。本文命令不应作为 V2 当前部署入口；当前 V2 结果必须保持 `reference_library_used=false`。后续需实现独立的 V2 参考库适配层，并重新完成数据、模型、运行时与端到端验收，才能进入 Scout 主流程。
>
> 实现范围：独立实验路径的部署材料已实现；尚未在项目方 DGX Spark 与真实受控样本上完成硬件或领域验收。
>
> 适用对象：数据负责人、瓷器专家、模型工程师和现场运维人员。

## 1. 这套能力解决什么问题

单机正式路径新增一个本地多模态向量服务，用同一套冻结模型处理参考图和用户上传图：

```text
受控参考库（50 件 × ≥5 视角） ──导入/校验──► 元数据索引
                                             │
                                             ▼
                                  Qwen3-VL-Embedding-2B
                                             │
                                             ▼
                                     冻结多视角向量索引
                                             │
独立复拍 + 开集负样本 ──评测/审签──► 冻结阈值与校准记录
                                             │
用户拍照 ──质量门控/多视角──► 精确余弦检索 ─┼─► 同一实物候选
                                             ├─► 相关参考与依据
经审核负向案例（≥10 件） ────────────────────┘    负向相似信号
```

系统明确分开三件事：

1. `KNOWN_ARTIFACT_CANDIDATE` 表示上传图可能对应库中同一件实物；
2. `RELATED_REFERENCES_ONLY` 表示只能找到视觉相关参考，不能确认同一实物；
3. 负向案例命中只表示“需要交叉复核”的反证信号，不等于“假货结论”。

目录识别、负向相似性和生成式说明都不直接给出真伪、年代、窑口、作者、价值或法律结论。报告中的 `authenticity_state` 保持 `NOT_ASSESSED`。

## 2. 单机运行结构与安全边界

`compose.single.yml` 在同一台 Spark 运行三个服务：

| 服务 | 作用 | 网络与数据 |
|---|---|---|
| `vision` | Qwen3-VL 可见观察与受约束报告摘要 | 仅内部网络；不映射主机端口；模型缓存只读 |
| `reference-embedding` | `Qwen/Qwen3-VL-Embedding-2B` 图像向量 | 仅内部网络；不映射主机端口；GPU；服务密钥；模型缓存只读 |
| `app` | 导入状态、检索、证据图、报告和唯一浏览器入口 | 只默认映射 `127.0.0.1:8088`；受控库位于持久数据目录 |

参考 embedding 使用独立 2B 模型，避免让生成模型把“解释能力”冒充稳定的实例检索距离。约 250 张精品参考图加负向案例的 Demo 规模采用精确余弦检索，先获得可复核的确定性基线；只有数据量显著扩大后才需要评估 cuVS 等近邻索引。

专用镜像固定 `sentence-transformers[image]==5.4.0` 与 `transformers==4.57.3`，使用 Sentence Transformers 5.4 的正式多模态 image+text 输入接口；不使用早期仅文本 `prompt=` 调用模拟图像 embedding。PyTorch/CUDA 由固定的 NVIDIA/vLLM 基础镜像提供，构建时执行依赖一致性检查，目标 Spark 仍需完成实际启动与数值验收。[Qwen3-VL-Embedding-2B 模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)

模型身份必须绑定到缓存中真实存在的 40/64 位 commit revision。启动脚本从 `refs/main` 解析 revision，容器和向量索引都会校验；不接受手写 `latest`、`main` 或虚构哈希。运行期设置：

```dotenv
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
REFERENCE_EMBEDDING_MODEL_SOURCE=Qwen/Qwen3-VL-Embedding-2B
REFERENCE_EMBEDDING_MODEL=qwen3_vl_embedding_2b
REFERENCE_EMBEDDING_MODEL_REVISION=
REFERENCE_EMBEDDING_DIMENSION=2048
```

初次预缓存前，`.env` 中的 revision 留空是有意设计；`make prefetch` 下载后从 `refs/main` 解析真实不可变 revision，并以 `600` 权限原子写回 `.env`。启动/预检会再次同只读缓存比对，不能手写或替换成另一个 revision。

## 3. 正式数据硬门

公开仓库不包含真实馆藏图、假货图、授权文件或专家意见，也不会伪造它们。正式 Demo 数据批次至少满足：

- 恰好 50 件经审签参考器物；
- 每件至少 5 张不同有效视角，总计至少 250 张参考图；
- 每件具有唯一实物 ID、来源/馆藏记录、使用授权、拍摄和校准记录、专家审签；
- 至少 10 件经审核负向案例，附来源、判定依据、差异描述和审签；
- 图片、证据文件、记录、内容集合和 manifest 均以 SHA-256 绑定；
- 负向案例与真实参考分库存储、分开计分；有争议或无准入许可的记录拒绝进入正式索引。

完整字段与目录规则见 [`data/reference_library/README.md`](../data/reference_library/README.md) 和 `manifest.schema.json`。建议把受控文件放在：

```text
runtime/data/reference-library/
├── manifest.json
├── images/...
├── sources/...
├── reviews/...
├── calibrations/...
├── counterfeit-evidence/...
├── held-out-queries/...       # 独立复拍与开集负样本；不得复用入库媒体
├── evaluation-manifest.json   # 冻结查询真值、文件哈希和采集批次
└── calibration-input.json     # evaluate 自动生成的未签封结果
```

`runtime/` 已从 Git 排除。真实数据应由机构批准的加密介质或受控传输方式进入 Spark，并由数据负责人核对权属与哈希。

开始采集前可运行 `make reference-scaffold`，得到 50 件精品、10 条负向案例以及每件 5 个标准视角的空白采集表和目录。输出仅是不可导入的占位工作区，不包含任何藏品、来源、授权或真伪陈述；填写并审签后仍需按 schema 生成正式 `manifest.json`。

## 4. 一次性准备顺序

### 4.1 初始化与预缓存

```bash
make install ROLE=single INSTALL_ARGS="--generate-key"
```

在批准的联网准备窗口审核模型许可后，临时配置：

```dotenv
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
PREFETCH_REFERENCE_EMBEDDING=1
```

执行：

```bash
make prefetch ROLE=single
```

该步骤构建应用、共享 VLM 和私有参考 embedding 三个固定镜像，下载所选模型，把参考 embedding 的实际 commit revision 原子写回 `.env`，并把模型 revision 与镜像 ID 写入 `runtime/prefetch-manifest-single.txt`。这只证明缓存准备完成，不证明识别准确率，也不代表已在 Spark 完成运行验收。

随后恢复离线策略：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 4.2 放入并验证受控数据

将批准的数据批次复制到 `runtime/data/reference-library/`，保持目录本身权限为 `700`，文件仅允许运行账号读取。先做全量只读验证：

```bash
make reference-verify
```

验证器会检查数量、视角、格式、图片解码、路径逃逸、符号链接、SHA-256、来源、许可、校准、专家审签和负向案例关联。任何一项失败都会拒绝整批数据。

### 4.3 原子导入元数据

```bash
make reference-import
```

输出 `index.sqlite3`。该文件是完整性绑定的元数据索引；其中的 8 维诊断向量只用于质量诊断，不用于正式实例识别。

### 4.4 在本地 GPU 构建图像 embeddings

```bash
make reference-build
```

该命令只启动内部 `reference-embedding` sidecar，通过 Compose 私有网络和服务密钥批量计算向量；不会开放模型端口。输出 `embeddings.npz`，并绑定：

- 原图 SHA-256 与图片/实物 ID；
- 固定 embedding 指令哈希；
- 模型 source、served name、不可变 revision 和维度；
- 元数据 manifest/index 哈希；
- 每个向量和整套矩阵哈希。

如果图片、manifest、模型 revision、指令或维度发生变化，旧向量索引会被拒绝，必须重建并重新校准。

### 4.5 用独立拍摄数据校准

入库图片不能同时当测试图片。数据与专家团队至少准备：

- 库内 50 件实物的独立复拍查询；
- 与建库拍摄不同的设备/距离/光照扰动；
- 按 physical object 隔离，排除完全相同媒体字节；
- 至少 20 个样本库之外的独立开集瓷器查询；
- 经审核假货对照，但不以负向相似直接代替专家结论；
- 按既定代价函数选择 identity、related、margin、质量和覆盖阈值，并记录误接收/误拒绝。

按 [`evaluation-manifest.schema.json`](../data/reference_library/evaluation-manifest.schema.json) 把冻结查询集和真值写入 `runtime/data/reference-library/evaluation-manifest.json`，先运行实际检索评测：

```bash
make reference-evaluate
```

该命令在同一私有 Qwen3-VL-Embedding-2B sidecar 上嵌入独立查询，读取当前 metadata/vector index，计算库内 top-1/top-5、误接收、误拒绝、开放集拒绝和分视角指标，并按预先设定的目标 FAR 生成**未签封** `calibration-input.json`。默认目标 FAR 是 `0.02`，它是阈值选择输入，不是准确率承诺；正式项目应由专家、产品风险负责人和统计负责人事先批准。

20 个开集查询只够验证 Demo 的拒识流程和错误记录是否闭环，不能统计证明 FAR 已低于 2%，同一批数据也不能同时承担调参与最终认证。举例而言，即便观察到零次误接收，要让 95% 单侧上界接近 2%，也约需 149 个相互独立的开集查询；正式准确率披露仍须预注册独立最终测试集、按场景分层并给出置信区间。因此运行摘要会标记 `validation_scope=DEMO_HELD_OUT_MEASUREMENT_NOT_CERTIFIED` 和 `accuracy_claim_status=NOT_CERTIFIED`。

如需使用另一个冻结 manifest、目标 FAR 或 top-k：

```bash
make reference-evaluate REFERENCE_ARGS="--evaluation-manifest approved-eval-v1.json --calibration-input approved-calibration-v1.json --target-far 0.01 --top-k 5"
```

评测器会拒绝完全相同媒体字节、查询泄漏、无独立复拍、无开集负样本、模型/索引绑定不一致和不完整真值。团队复核 `calibration-input.json` 顶层指标和 `evaluation_details`，完成双人审签后再执行：

```bash
make reference-seal
```

若使用另一个文件名：

```bash
make reference-seal REFERENCE_ARGS="--evaluation-manifest approved-eval-v1.json --calibration-input approved-calibration-v1.json"
make reference-status REFERENCE_ARGS="--evaluation-manifest approved-eval-v1.json --calibration-input approved-calibration-v1.json"
```

自定义文件名时，`reference-evaluate`、`reference-seal` 与后续 `reference-status` 必须传入相同的 `--evaluation-manifest` / `--calibration-input`，避免把不同评测批次混在一次部署门中。

封存输出 `calibration.json`，与当前 manifest、向量索引、冻结评测 manifest、独立拍摄批次、评测结果、模型 revision 和指令哈希绑定。不得用入库图自匹配结果生成“高准确率”或事后降低阈值以适配现场样本。

### 4.6 查看部署门并正式启动

```bash
make reference-status
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
```

启动后检查：

```bash
curl -fsS http://127.0.0.1:8088/api/reference-library/summary
```

正式状态必须同时满足：

- 参考元数据索引完整；
- embedding 索引与当前模型身份一致；
- 冻结校准记录完整且绑定一致；
- 私有 embedding 服务在线；
- `/health/ready` 为 `ready`。

## 5. 未就绪时的可见行为

| 缺失或失败 | 状态/行为 |
|---|---|
| 无受控 manifest/index | `METADATA_INDEX_MISSING`，不运行目录识别 |
| 有元数据、无向量 | `VECTOR_INDEX_MISSING`，不生成相似候选 |
| 有向量、无合格校准 | `CALIBRATION_REQUIRED`，禁止接受同一实物身份 |
| 模型 revision/指令/哈希不一致 | `VECTOR_INDEX_INVALID` 或 `EMBEDDING_UNAVAILABLE`，失败关闭 |
| 图片质量或视角覆盖不足 | `INSUFFICIENT_CAPTURE`，要求补拍 |
| 未达到同一实物阈值但存在相关项 | `RELATED_REFERENCES_ONLY`，只提供相关性依据 |
| 开集且无可靠相关项 | `OPEN_SET_NO_MATCH`，明确弃权 |

默认正式配置启用参考库，因此缺少真实库或冻结校准时，应用 readiness 会保持未就绪，防止现场把空库/未校准系统当成正式识别系统。如果只需查看与目录识别无关的旧 Demo，可暂时设置：

```dotenv
RELICSCOPE_REFERENCE_LIBRARY_ENABLED=false
```

此模式不能用于参考识别验收，恢复 `true` 后仍需完成上述全部数据与校准门。

## 6. 运维、更新与回滚

- 50 件库的任何图片或元数据变化都创建新 library version；重新导入、重建 embeddings、独立校准，不原地修改旧证据。
- 模型、revision、维度或 embedding 指令变化时必须建立新向量空间，不能混用旧向量。
- `make backup ROLE=single` 会备份 `runtime/data`，包括受控索引与校准；备份不包含服务密钥或模型缓存。备份介质按真实藏品数据等级保护。
- 离线发布包包含三个容器镜像和模型需求/实际 revision 清单，但不重新分发第三方模型权重，也不包含真实参考数据。
- 硬件验收需保存 Git commit、镜像 ID、模型 revision、manifest/index/calibration 哈希、GPU/内存观测、冻结查询集结果和专家签字。
- 运行中批准新库后，调用 `POST /api/reference-library/refresh` 或重启应用，再检查 summary/readiness；刷新失败时继续使用旧受控发布或停止正式识别，不能绕过完整性门。

当前提交只提供可复现的部署入口与失败关闭控制。只有在目标 DGX Spark 上完成真实数据导入、独立校准、运行验收和专家复核后，才可标记为 `HARDWARE_VERIFIED` 或对外披露准确率。

完成上述门并反复验证冷启动后，可选安装单机开机服务：

```bash
RELICSCOPE_RUN_ACCOUNT="$(id -un)"
sudo make install-systemd ROLE=single SYSTEMD_ARGS="--user ${RELICSCOPE_RUN_ACCOUNT}"
```

不要在参考库仍为 `CALIBRATION_REQUIRED` 时加 `--now`；先手工启动、检查与回滚，再由机构运维批准自启动。
