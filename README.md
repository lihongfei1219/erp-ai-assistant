# ERP AI Assistant

面向B2B平台内部运营的只读数据分析助手。Python读取ERP销售快照，Pandas/Decimal计算事实，Pydantic AI理解自然语言；网页和飞书展示同一套分析结果，不执行ERP业务操作。

**当前已实现：**经营看板、订单证据、销售／退货／销售出库／库存自然语言分析、组合问题与追问、飞书应用机器人及统一卡片展示。查询日期与库存时点取决于部署时加载的快照，可在飞书询问“数据范围”；本地最新数据版本见项目进度。自动数据更新、正式SSO和生产进程守护尚未接入。

项目进度、待验收事项和下一步统一看 **[项目进度](docs/project-status.md)**。

## 快速启动

已有本机环境：

```powershell
uv run python start.py
```

根目录`start.py`在同一终端管理网页和飞书两个子进程，默认网页端口8001。打开`http://127.0.0.1:8001`即可进入工作台；等待飞书日志显示长连接建立后即可在授权群@机器人。按一次`Ctrl+C`一起停止两个服务，最多等待30秒退出；任一服务进程退出时会关闭另一个并返回失败，不自动重启。仅管理本次创建的子进程，不停止其他实例或重置任务。

可先执行`uv run python start.py --check`进行离线检查；检查快照、前端构建、飞书授权配置、端口和已有机器人锁，不连接数据库、模型或飞书。正式启动仍需助手数据库和网络可用。`--port`可更换网页端口，`--report-path`给两个服务指定同一份快照，`--config`指定飞书配置。默认优先四域快照，其次经营／销售快照；统一入口要求具备有效销售规则和全平台授权。

网页仅监听本机，无需本地令牌或登录。若只需网页，原命令`uv run python scripts/start_backend.py --port 8001`仍保留；该单服务脚本默认端口仍为8000。

Python统一由uv项目管理，在项目根目录执行：

```powershell
uv sync --locked
uv run python scripts/check_environment.py
uv run python start.py
```

`pyproject.toml`声明依赖，`uv.lock`锁定直接及间接依赖，`.python-version`选择Python 3.11；虚拟环境仍为根目录`.venv`，不需要手动激活。新增运行依赖使用`uv add 包名`，开发依赖使用`uv add --dev 包名`，移除使用`uv remove 包名`。已精确固定版本的直接依赖用`uv add "包名==新版本"`更新；在声明允许的范围内更新锁定版本可用`uv lock --upgrade-package 包名`，然后执行`uv sync --locked`。依赖声明和锁文件一起提交；不再维护`backend/requirements.lock`。这些命令采用[uv项目管理流程](https://docs.astral.sh/uv/concepts/projects/sync/)。

前端继续由npm管理。首次安装或前端依赖改变后，在`frontend/`运行`npm ci`和`npm run build`。Windows也可在项目根目录执行`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-Environment.ps1`，它依次调用uv同步、npm安装／构建和本地检查；运行前关闭前端开发／测试进程。数据快照缺失会单独提示，安装依赖不会生成模拟业务数据或连接ERP。

FastAPI同源提供`frontend/dist`；分别开发时在`frontend/`执行`npm run dev`，Vite默认代理至8000后端。SQL只读取数需要本机SQL Server实例和ODBC Driver 18；现有快照上的网页分析无需重新取数。

网页和飞书机器人仍是独立进程，由`start.py`统一启动和停止。部署配置统一放根目录`.env`：模型、飞书凭据和授权、数据库、监听地址、快照路径及会话签名。参照[.env.example](.env.example)填写；系统环境变量优先，默认不再读取`.local`中的旧配置文件。若只需飞书，可单独检查并启动：

```powershell
uv run python scripts/start_feishu_bot.py --check
uv run python scripts/start_feishu_bot.py --sales
```

同一状态目录只运行一个机器人进程，启动前检查现有进程。首次部署步骤见[应用接入](docs/feishu-app-setup.md)和[问数说明](docs/feishu-analytics.md)。群内示例：`@机器人 九月十五号哪个药品卖得最好`。

## 配置与服务器迁移

日常只修改根目录`.env`。启动参数`--host`、`--port`、`--report-path`优先于系统环境变量和`.env`；相对路径始终相对项目根目录。可在进程环境设置`ERP_ENV_FILE`指定其他私有配置文件。值不进行变量插值，含空格、`#`、反斜杠或密码时使用引号，示例见`.env.example`。`FEISHU_ALLOWED_CHAT_IDS`和`FEISHU_ALLOWED_USER_OPEN_IDS`用逗号分隔；原有租户／群限制及全员模式保持不变。

旧安装可先运行`uv run python scripts/migrate_env.py`预览，再加`--apply`迁移。脚本保留已有`.env`值，转换飞书JSON／Webhook文本／签名密钥，校验往返读取后原子写入`.env`，再删除已迁移的旧配置文件；不删除运行数据。正常启动只读`.env`，旧飞书JSON仅在显式传`--config`时兼容；对应环境配置仍优先。

迁移到服务器：

1. 复制代码及私有`.env`；保留`ERP_CONTEXT_SIGNING_KEY`原值。新部署可在飞书授权配置填写完成后运行迁移脚本生成该随机密钥。
2. 复制`ERP_REPORT_PATH`指向的快照；需要保留会话和消息去重时，停服后复制LangGraph、飞书状态目录，并迁移独立的`ERP_AI_Assistant`数据库。不要让新旧实例同时使用同一机器人。
3. 修改三个`ERP_*_ODBC`连接串，服务器不能沿用本机`lpc:.\\ERPLOCAL`和Windows集成认证。源库账户只读，助手库账户需可写；`ERP_ASSISTANT_ADMIN_ODBC`只用于显式初始化命令，已有库无需重新建库。
4. 安装Python／uv、Node及Microsoft ODBC Driver 18，执行`uv sync --locked`；在`frontend`执行`npm ci`和`npm run build`。
5. 执行`uv run python start.py --check`，通过后运行`uv run python start.py`。离线检查不代表远程数据库和飞书网络已连通。

`ERP_HOST`默认`127.0.0.1`，`ERP_PORT`默认`8001`；容器或服务器需要对外监听时可调整地址。网页仍为免登录入口，外网使用时应由有鉴权的反向代理提供访问控制。飞书使用主动长连接，无需公开机器人回调端口。进程守护可由服务器的服务管理器托管统一入口，本次未安装系统服务。

`.local`只保留快照、日志、锁及会话等运行数据；路径在`.env`中配置。版本化的业务口径与语义目录仍留在`backend/config`，不混入部署凭据。私有`.env`不提交Git，服务器需通过安全渠道单独复制；配置变更后重启服务。

## 文档导航

| 要了解什么 | 入口 |
|---|---|
| 完成了什么、接下来做什么 | [项目进度](docs/project-status.md) |
| 架构、模块职责和实施边界 | [架构规划](docs/erp-ai-assistant-plan.md) |
| 网页分析与接口 | [AI数据分析](docs/analysis-assistant.md) |
| 飞书提问、追问、卡片与任务恢复 | [飞书自然语言分析](docs/feishu-analytics.md) |
| 飞书应用权限、配置和连接 | [应用机器人接入](docs/feishu-app-setup.md) |
| 日报预览、Webhook及可选调度 | [飞书日报](docs/feishu-daily.md) |
| 表字段与业务口径 | [数据字典](docs/data-dictionary.md)、[指标定义](docs/metrics-v1.md)、[业务假设](docs/business-assumptions.md) |
| 测试证据、复现命令与缓存清理 | [验证记录](docs/validation.md) |
| 开发协作约束 | [AGENT.md](AGENT.md) |

## 目录与维护

```text
backend/             Python API、计算、模型适配、飞书、Worker和测试
frontend/            React/TypeScript页面与浏览器测试
scripts/             启动、数据摸底、合成评测、展示预览和缓存清理
docs/                10份常用文档；进度与验证集中更新
.local/              本地配置、快照、运行状态及可重建产物（不提交）
database_backups/    数据库备份、安装介质及本机说明（不提交）
```

`.local`包含凭据和去重状态，不能整体删除。`scripts/Clean-Workspace.ps1`默认只预览清单，显式加`-Apply`才清理已识别生成物；`-IncludeDownloadCaches`额外清理本项目的uv/npm下载缓存。它保留已安装依赖、运行构建和业务数据。详见[维护说明](docs/validation.md)。

从项目根目录运行`uv run pytest -q -p no:cacheprovider`及`uv run ruff check backend --no-cache`。外部模型、源库和助手库测试使用独立开关，默认跳过；完整说明见验证记录。

自然语言标注评测从项目根目录运行`uv run python scripts/evaluate_dialogue.py`，默认离线回放；加`--cloud`才调用已配置的大模型。支持`--case`选择场景和`--output`保存报告，详见[评测说明](docs/analysis-assistant.md#自然语言标注评测)。
