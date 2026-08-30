# Codex Desktop / CLI：仅作为开发与运维入口

## 1. 定位

Codex Desktop或Codex CLI可以帮助维护人员检查差异、运行部署命令、分析日志和准备发布。它们不参与器物采集、推理、知识检索、证据图、报告或systemd自启动，也不是RelicScope生产依赖。

即使Codex完全不可用，以下操作仍可独立完成：

```bash
make preflight ROLE=spark-a
make start ROLE=spark-a
make health ROLE=spark-a
make backup ROLE=spark-b
make stop ROLE=spark-b
```

## 2. 推荐安装位置

首选在受控开发/管理工作站安装Codex，通过机构批准的SSH或跳板机操作两台Spark。这样可以把AI工具登录态与生产服务身份分开，也避免在生产节点增加不必要的软件和公网依赖。

若机构允许在Spark Linux管理账户使用Codex CLI，应满足：

- 仅交互式管理员账户，不使用root或RelicScope服务用户；
- 不加入systemd、cron或应用容器；
- 不读取或上传`secrets/`、`.env`、原始器物、专家意见和未授权知识；
- 运行期离线策略开启时，不把Codex可用性作为故障恢复条件；
- 任务结束后按机构要求退出、撤销或清理管理会话。

## 3. Codex CLI安装

在受控管理工作站按照官方Codex CLI页面执行当前安装脚本：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
```

首次运行`codex`并按官方流程登录。下载并执行远程安装脚本前，机构可先在受控环境审阅脚本内容；部署日也应重新核对支持系统、认证和更新说明：[OpenAI Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。不要把Codex登录令牌或OpenAI API key写入RelicScope`.env`。

当前仓库验证过的最短路径为：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout v1.1.0-r2
codex
```

进入 Codex 后要求它先阅读 `AGENTS.md`，再运行 `./scripts/reproduce-demo.sh --install`。安装脚本来源会随时间更新，因此仓库不把某个 Codex 版本声明为 RelicScope 的生产依赖；每次部署只记录实际执行的 `codex --version`。公开仓库的只读克隆不需要 GitHub 凭据；只有推送变更时才使用经批准的开发者身份，且不得把凭据写入脚本或 `.env`。

## 4. Desktop / Linux App

当前[OpenAI Linux app官方说明](https://learn.chatgpt.com/docs/linux/linux-app)列出的预览支持范围为Ubuntu 24.04/26.04、Debian 13、Fedora 43/44，并提供ARM64支持；具体范围可能更新，安装前重新核对。若DGX OS不在支持范围，使用管理工作站上的Desktop或Codex CLI，不通过修改系统依赖强行安装。Linux preview当前不支持Computer Use，因此本部署不把该能力写入Spark运维路径。

## 5. 建议工作流

1. 在开发工作站对发布包和SHA-256做检查。
2. 让Codex只读取脱敏后的源码、部署文档和健康输出。
3. 所有变更经人工diff和代码审查后再打包。
4. 通过SSH在Spark上运行固定的`make`入口，不让Codex临时拼接生产命令。
5. 发布后保存预检、健康、镜像ID和manifest；不要保存服务key。

示例：

```bash
ssh spark-a-admin 'cd /srv/relicscope/current && make health ROLE=spark-a'
ssh spark-b-admin 'cd /srv/relicscope/current && make backup ROLE=spark-b'
```

## 6. 明确禁止

- 把Codex CLI或Desktop当作常驻推理服务、Agent运行时或健康检查依赖；
- 把Codex凭据打进Docker镜像、离线包、备份或systemd环境；
- 将服务key、HF token、完整`.env`或受限器物数据粘贴到任务中；
- 让AI工具未经审批修改防火墙、网卡、用户权限或删除备份；
- 以Codex生成的说明替代两台真实Spark上的现场验收证据。
