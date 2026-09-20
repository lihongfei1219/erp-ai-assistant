# 飞书自然语言数据分析

飞书群问数与网页共用 Pydantic AI 计划器、原问题校验、Pandas/Decimal 计算和口径解释。使用现有应用机器人、授权群及独立助手库；启动命令仍是 `scripts/start_feishu_bot.py --sales`，无需新增飞书权限或建表。

## 提问示例

在已授权群中 @机器人，发送完整问题：

- `分析2026年9月1日至5号的销售数据，哪些药品卖的好`
- `统计2026年9月1日至7日销售概览`
- `2026年9月1日至7日每日销售趋势，并列出商品销售额前5名`
- `2026年9月1日至7日客户订单数排行前10名`
- `比较2026年9月8日至14日与2026年9月1日至7日的销售额，按商品拆解变化贡献`
- `2026年9月1日至7日日波动线索`

日期须在快照完整范围内，可发送 `数据范围` 查询。相对日期以飞书消息发送日期、Asia/Shanghai 为准，不使用处理日期或备份日期替代。当前备份完整日期为2026-09-01至2026-09-15，未接入实时同步。

“卖得好／卖的好／畅销”默认销售金额前10名，显式订单数、前N名优先。“药品”按用户确认的当前商品范围处理，回复标注未按药品类别筛选。指定药名、仅药品、排除器械、销量／件数、库存、利润等尚不能执行，不会悄悄丢掉条件。

## 追问

收到分析结果后，30分钟内在同一群再次 @机器人：

- `换成按订单数排，取前5名`
- `取前3名`
- `再看客户排行`
- `清除追问上下文` 或 `重新开始`

上下文按应用、租户、群、用户隔离，仅继承已成功送达的结构化计划。有效期按追问入队时刻判断，排队延迟不改变语义。快照变更或上下文过期后需重新指定日期；多期间上下文不能明确继承时要求澄清。清除请求按入队顺序形成屏障，不依赖清除提示是否先送达。机器人重启不丢失尚有效的计划。

## 回复内容

自然语言负责选择分析目标，结构化结果类型决定固定布局。卡片按“日期与范围 → 关键数字 → 图表／分页明细 → 折叠说明”组织；支持六类结果及组合分析，无需为不同问法设计排版。

| 分析 | 默认展示 |
|---|---|
| 销售概览 | 销售金额、订单、采购企业和商品指标，平均每单金额 |
| 商品／采购企业排行 | 前5项横向条形图，完整请求结果的分页表格 |
| 每日趋势 | 完整日期序列折线图，分页明细 |
| 期间比较 | 本期、比较期、差额、变化率和贡献明细 |
| 波动线索 | 规则说明、当日／前日金额及变化率表格 |

新版使用飞书卡片2.0，要求客户端7.20及以上；更早客户端仅显示标题和升级提示。原生图表使用本地计算结果生成VChart定义，不嵌入网页Plotly。模型不生成卡片代码。未知展示模板使用通用字段表格，空数据有明确提示；澄清和错误沿用简洁文本提示卡片。

表格每页5行，单卡最多5个表格和2个图表；第6个明细使用折叠精简列表。统计口径、分析说明默认折叠，药品范围说明保持可见。卡片同时限制28KB和200个元素：先移除可选图表，再逐步减少明细；节选明确显示行数，缩短日期或减少排名条数可重新查询。截短趋势时移除图表，避免误认为覆盖完整区间。

金额以Decimal格式化为千分位两位小数，完整计算及留存结果仍为四位；超大数值不转为不可靠的绘图坐标。名称及编码作为纯文本处理，同名对象的图表序号独立。日期、范围、来源、规则版本与查询编号保留，订单依据ID保存在完整结果及文本回退中。比较结果保留其余维度贡献；规则波动不声称因果。完整报告外链须待可访问且有鉴权的部署地址就绪后接入，当前不生成本机链接。

展示模块见[架构规划](erp-ai-assistant-plan.md)。新增分析能力时，先定义可信的结果字段，再扩展模板；无法支持的业务问题仍会澄清，不会仅为了生成卡片而猜数据。

原生组件依据飞书官方[卡片2.0结构](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-structure)、[表格](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/table)、[图表](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/chart)和[折叠面板](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/containers/collapsible-panel)规范；表格只允许根级放置，不能放进折叠区。

## 首次初始化与运行

先完成[应用接入与授权](feishu-app-setup.md)。已有运行进程无需重复初始化或启动；新环境从根目录执行：

```powershell
.venv/Scripts/python.exe scripts/init_assistant_db.py
.venv/Scripts/python.exe scripts/start_feishu_bot.py --sales --report-path .local/reports/platform-operating-20260916.json
```

初始化只创建独立`ERP_AI_Assistant`及任务表，不修改`ERP_Local`。运行时不自动建库；`ERP_ASSISTANT_ODBC`也必须指向助手库。发现、连接测试和销售问数共用单进程锁，切换前先停止本应用原进程。需要电脑、SQL Server、网络和机器人进程持续运行，尚未配置Windows服务或开机启动。

## 配置、降级与恢复

继续使用 `.local/feishu-app.json`、根目录 `.env` 中已有 `ERP_AI_*` 配置。模型仅接收当前问题、日期范围和前次计划，不接收ERP记录、结果、群／用户身份或原始对话历史。模型调用前持久化 `analysis_planning` 并移除活动记录中的原问题；成功后先保存 `analysis` 计划，再执行计算。不确定的模型调用和发送不自动重试。

模型只读取白名单：优先`ERP_AI_BASE_URL`、`ERP_AI_MODEL`、`ERP_AI_API_KEY`；缺项时兼容已有`MAIN_VITE_PI_NORMALIZER_BASE_URL`、`MAIN_VITE_PI_NORMALIZER_MODEL`、`DASHSCOPE_API_KEY`。环境变量优先于根目录`.env`，`ERP_AI_ENABLED=false`可关闭。变更模型配置、应用凭据、租户或快照路径后重启。群／用户授权在接收、分析和发送前重读，撤销后阻止继续处理。

固定日期指令不调用模型，模型不可用时仍可发：

```text
2026-09-01至2026-09-05 销售概览
2026-09-01至2026-09-05 每日销售趋势
2026-09-01至2026-09-05 商品销售额排行 前10
```

旧版排队任务保持兼容；新版六类功能通过 `analysis_planner` 分支运行。源ERP库保持只读；任务、结果与上下文复用独立 `ERP_AI_Assistant.erp_ai.feishu_sales_jobs`，不新增会话表。

事件只入队，后台Worker解释和计算；消息／事件唯一约束防重复，同用户正在处理时提示等待。分析租约120秒，过期可重新领取，旧租约不能覆盖结果。已保存的结构化计划可恢复本地计算；重启遇到`analysis_planning`或历史`interpreting`时提示重新提问，避免不确定模型调用重复计费。

`ready`表示结果已保存，调用飞书前标为`sending`，收到成功确认后为`sent`。明确业务拒绝为`rejected`，记录数字错误码；网络中断或未知回执为`unknown`。重启遗留`sending`也转为`unknown`：先核对群内消息，再发送新问题，不能批量重置或盲目重发。查询编号、原计划、完整结果和证据保留在助手库；日志／事务备份保留期及自动清理仍需后续运维建设。

## 验证

默认单元及API测试不调用模型或飞书。以下显式测试从 `backend/` 执行：

```powershell
$env:ERP_RUN_ASSISTANT_TESTS='1'
../.venv/Scripts/python.exe -m pytest tests/integration/test_feishu_jobs.py -q
Remove-Item Env:ERP_RUN_ASSISTANT_TESTS
$env:ERP_RUN_MODEL_TESTS='1'
../.venv/Scripts/python.exe -m pytest tests/integration/test_feishu_analytics_model.py -q
Remove-Item Env:ERP_RUN_MODEL_TESTS
```

SQL测试仅操作随机应用ID下的合成助手任务并清理自身记录；真实模型评测使用合成问题／快照，卡片在本地捕获，不向群发送。运行状态与实际送达应分别记录，不能把这些测试称为群内已收到结果。

最近功能版本的执行结果见 [验证记录](validation.md)。

可复用的合成布局检查，从根目录执行：

```powershell
.venv/Scripts/python.exe scripts/preview_feishu_display.py
node scripts/check_feishu_display.cjs --fetch-chart
```

首次显式下载固定版本VChart公开脚本至`.local/`；之后去掉`--fetch-chart`可离线复查。需要已安装前端依赖和Chrome，或通过`PLAYWRIGHT_CHANNEL`指定已安装浏览器。生成13组样例及26张截图，检查图表渲染、翻页、折叠和页面溢出；表格／容器为近似预览，实际视觉仍以飞书客户端为准。输出仅含合成数据，保存在`.local/feishu-display/`。
