# 离线模型、数据目录与交付打包

## 1. 推荐生产目录

脚本默认使用项目内`runtime/`，便于Demo。机构部署建议在每台Spark的加密磁盘上使用绝对路径：

```text
/srv/relicscope/
├── releases/
│   ├── 1.1.0/                  # 只读发布源码与部署文件
│   └── current -> 1.1.0/
├── state/                      # Spark B：SQLite、上传、报告、审计
│   └── reference-library/      # 真实参考/负向案例、索引、冻结校准（受控数据）
├── models/
│   └── huggingface/            # 已批准模型快照；运行期只读
├── cache/
│   └── vllm/                   # 可重建的vLLM编译缓存
├── backups/                    # 加密、限制访问、最好跨盘/跨设备
├── packages/                   # 发布及离线转运包
└── secrets/                    # 不进入任何发布或备份包
```

对应`.env`示例：

```dotenv
RELICSCOPE_DATA_HOST_DIR=/srv/relicscope/state
HF_CACHE_DIR=/srv/relicscope/models/huggingface
VLLM_CACHE_DIR=/srv/relicscope/cache/vllm
BACKUP_DIR=/srv/relicscope/backups
PACKAGE_DIR=/srv/relicscope/packages
SERVICE_API_KEY_FILE=/srv/relicscope/secrets/service_api_key
HF_TOKEN_FILE=/srv/relicscope/secrets/hf_token
```

Spark A的`state`通常为空，但保留独立目录可简化统一预检；Spark B是唯一持久写入的业务节点。模型目录运行期只读，vLLM编译缓存可写且可重建。

## 2. 数据分类

| 数据 | 位置 | 是否备份 | 是否可进入发布包 |
|---|---|---|---|
| 版本化演示知识 | `data/` | 随发布版本保存 | 是；保持`DEMO/SYNTHETIC`标记 |
| 会话、原始图片/视频、派生帧、证据、报告 | `RELICSCOPE_DATA_HOST_DIR` | 是；由`backup.sh`一致性备份 | 否 |
| 50 件参考图、负向案例证据、来源/审签、向量索引与校准 | `RELICSCOPE_DATA_HOST_DIR/reference-library` | 是；按高价值藏品数据等级加密与授权 | 永不进入公开/离线发布包 |
| Hugging Face模型快照 | `HF_CACHE_DIR` | 可从获准来源重建；离线站点按机构模型治理流程做受控副本 | 不进入本交付包 |
| vLLM编译缓存 | `VLLM_CACHE_DIR` | 通常不备份 | 否 |
| 服务key、HF token | `secrets/`或外部密钥系统 | 按密钥政策托管 | 永不进入发布、备份或离线模型包 |
| 第三方评测数据 | 独立研究区 | 按许可证 | 默认禁止 |

## 3. 发布包

```bash
make package ROLE=all
```

默认包包含源代码、部署脚本、文档、测试和版本化演示知识；排除`.env`、`secrets/`、`runtime/`、缓存和运行证据。

## 4. 离线包

在已经完成批准预缓存的节点上按角色执行：

```bash
make package-offline ROLE=spark-a
make package-offline ROLE=spark-b
make package-offline ROLE=single
```

离线包额外包含：

- 当前角色实际选择的Docker镜像；
- 当前角色所需的模型ID和已观察revision清单；
- manifest与逐文件SHA-256。

脚本不会联网，也不会主动下载或打包任何第三方模型权重与数据。单机角色会包含应用、vLLM 与 reference-embedding 三个已构建镜像，以及三类所需模型的 ID/revision 需求；它不会包含模型权重、真实参考库、负向案例证据、专家审签、HF token、服务key、`.env`、会话、上传或报告。容器镜像包仍可能很大，必须先确认目标介质容量、加密和保管人。

在目标节点导入前：

1. 验证外层`.sha256`；
2. 解包到新的临时目录，不覆盖当前发布；
3. 在临时目录运行`sha256sum -c SHA256SUMS`；
4. 用`docker load --input container-images.tar`导入镜像；
5. 审核`MODEL_REQUIREMENTS.txt`，通过机构批准且符合许可证/地域限制的独立渠道把模型准备到`HF_CACHE_DIR`；
6. 运行角色预检，核对镜像ID、模型revision和模型端点实际报告的ID。

## 5. 明确禁止打包的研究资产

- MVTec AD数据许可含非商业限制，不进入商业演示包，也不作为默认商业训练源。
- VisuCeram 采用 Apache-2.0，但对象是卫生陶瓷工业缺陷，与古陶瓷鉴证存在域差异；本交付不下载、复制或打包该数据与权重。
- CE5-DET许可证与数据来源边界不清，不下载、不缓存、不集成。
- 未完成模型卡、地域、再分发和商业使用复核的权重，不得因“本机能运行”而进入离线包。
- HyperSpy及其他GPL组件在法律审查和架构决定前不随专有运行包分发。
- 互联网文物图片、博物馆网页图、拍卖图和专家文献图片均需逐项确认权利，不因公开可访问而自动取得训练或再分发许可。

## 6. 离线并不只是一组环境变量

`OFFLINE_RUNTIME=1`、`HF_HUB_OFFLINE=1`和`TRANSFORMERS_OFFLINE=1`防止正常库路径自动下载；真正可证明的离线运行还需要：

- 主机或上游网关阻断公网出站；
- 不在运行容器中挂载HF token；
- 启动使用`--pull never`和`--no-build`；
- 断网状态下完成一次端到端演示和出站流量检查；
- 保存镜像ID、模型revision、发布包hash和验收时间。
