# ERP AI Assistant

面向B2B平台内部运营的只读数据分析助手。Python读取ERP销售快照，Pandas/Decimal计算事实，Pydantic AI理解自然语言；网页和飞书展示同一套分析结果，不执行ERP业务操作。

**当前已实现：**经营看板、订单证据、销售／退货／销售出库／库存自然语言分析、组合问题与追问、飞书应用机器人及统一卡片展示。销售、退货和销售出库完整日期为2026-09-01至09-15，库存为09-16 14:33:41备份时点；自动数据更新、正式SSO和生产进程守护尚未接入。

项目进度、待验收事项和下一步统一看 **[项目进度](docs/project-status.md)**。

## 快速启动

已有本机环境：

```powershell
.venv/Scripts/python.exe scripts/start_backend.py --port 8001
```

打开`http://127.0.0.1:8001`即可进入工作台，无需本地令牌或登录；启动脚本不再生成访问令牌。启动前先确认端口；脚本默认端口为8000。服务仅监听本机。优先加载`.local/reports/platform-four-domain-20260922.json`，不存在则回退原销售快照。可用`--report-path`指定另一份已对账快照。

首次安装或修改依赖、前端后：

```powershell
uv venv --python 3.11 .venv
uv pip sync --python .venv/Scripts/python.exe backend/requirements.lock
Set-Location frontend
npm ci
npm run build
```

FastAPI同源提供`frontend/dist`；分别开发时在`frontend/`执行`npm run dev`，Vite默认代理至8000后端。SQL只读取数需要本机SQL Server实例和ODBC Driver 18；现有快照上的网页分析无需重新取数。

飞书已有授权配置和助手库时，从根目录运行：

```powershell
.venv/Scripts/python.exe scripts/start_feishu_bot.py --sales
```

同一状态目录只运行一个机器人进程，启动前检查现有进程。首次部署步骤见[应用接入](docs/feishu-app-setup.md)和[问数说明](docs/feishu-analytics.md)。群内示例：`@机器人 九月十五号哪个药品卖得最好`。

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

从`backend/`运行`../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`及`../.venv/Scripts/ruff.exe check . --no-cache`。外部模型、源库和助手库测试使用独立开关，默认跳过；完整说明见验证记录。

自然语言标注评测从项目根目录运行`.venv/Scripts/python.exe scripts/evaluate_dialogue.py`，默认离线回放；加`--cloud`才调用已配置的大模型。支持`--case`选择场景和`--output`保存报告，详见[评测说明](docs/analysis-assistant.md#自然语言标注评测)。
