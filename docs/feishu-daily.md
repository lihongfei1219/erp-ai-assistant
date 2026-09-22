# 飞书群销售日报

已提供日报预览、Webhook发送、发送状态记录和独立定时进程。当前没有配置日报Webhook，没有发送日报或启动定时推送；用于群内问数的应用机器人已单独接入，见[飞书问数](feishu-analytics.md)。

## 查看演示

登录前端后选择 **飞书日报**。默认将 **2026-09-16** 作为昨日，按该日有效订单统计金额、订单数、采购企业数、客单价、排除订单数、客户／商品 Top 5 及前一天参考金额。

日报沿用快照保存的规则、币种、时区和权限范围，不把整个期间金额当作单日销售。9 月 16 日数据截至 14:33:41，卡片标注“演示”和“当日数据不完整”，不计算与完整前一天的环比；完整日且前一天金额非零时才计算金额环比。

从项目根目录生成本地预览（不发送）：

```powershell
Set-Location backend
uv run python -m workers.feishu_daily --date 2026-09-16 --demo
```

输出日报和卡片 JSON 到被 Git 忽略的 `.local/`。`--output` 可指定新文件，不覆盖已有预览；`--report-path` 可指定另一经营快照。

## 配置指定群

1. 在飞书群设置中添加 **自定义机器人**，复制 Webhook。
2. 将地址单独保存到项目根目录 `.local/feishu-webhook.txt`，UTF-8 文本，不加引号。
3. 如果启用了签名校验，将签名密钥保存到 `.local/feishu-secret.txt`。
4. 若设置关键词，使用 **销售日报**；若设置 IP 白名单，允许发送电脑的出口 IP。
5. 页面点击“刷新预览”，确认已配置后点击“发送演示日报到飞书群”。

也可配置进程环境变量 `FEISHU_WEBHOOK_URL`、`FEISHU_WEBHOOK_SECRET`，它们优先于文件；显式空值禁用对应文件值。`.env.example` 仅为说明，不自动读取 `.env`。文件每次请求重新读取，无需重启；环境变量需在启动进程前设置。

本模块使用群 Webhook，无需 App ID / App Secret。已安装的 `lark-oapi` 保留供后续应用机器人使用。浏览器不接触凭据，HTTP 请求不能指定其他接收群。当前为本机运营开发入口；机器人所在群应仅包含有权查看当前快照范围的人员。

在 `backend/` 手动发送（执行此命令才对外发送）：

```powershell
uv run python -m workers.feishu_daily --date 2026-09-16 --demo --send
```

本机接口无需访问令牌；飞书发送仍需服务端配置Webhook：

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/feishu/daily?day=2026-09-16&demo=true` | 演示预览、配置就绪状态，不发送 |
| `GET /api/v1/feishu/daily` | 真实昨日预览；范围不足返回 409 |
| `POST /api/v1/feishu/send` | 请求体 `{"day":"2026-09-16","demo":true}`，发送到配置群 |

## 每天北京时间 08:00

群配置和每日数据更新就绪后，在 `backend/` 运行：

```powershell
uv run python -m workers.feishu_daily --watch --report-path ../.local/reports/platform-operating-20260916.json
```

这是独立常驻进程，**必须保持进程与电脑运行**；按 Ctrl+C 停止。前后端启动不会自动启动它。每 30 秒检查一次，08:00 后执行，可能有约 30 秒偏差；晚启动会尝试当天任务，不回放过去多天。

定时模式使用真实昨天，禁止固定日期或 `--demo`。每次尝试重新读取指定快照，要求窗口覆盖昨天且来源截至时刻达到今天 00:00。当前 9 月 16 日静态备份不能支持后续日期；数据过期记录失败，不伪造零销售。本次不包含 ERP 自动取数、实时同步或开机自启。更新快照后可重新指定路径并重启调度。

明确失败最多尝试 3 次，间隔 5 分钟；不确定结果不自动重试。调度记录在 `.local/feishu-delivery/schedule-*.json`，包含任务日期、次数和不含凭据的状态说明。

## 重复发送与失败处理

- 按 **群 Webhook、日期、演示/正式模式、权限范围** 去重；成功后刷新页面、重启进程或重算相同日期不会再次发送。
- 进程间锁阻止并发发送；网络请求前记录 `sending`，成功后记录 `sent`。
- 飞书明确拒绝记录 `failed`，修正配置后可手动重试。
- 超时、未知响应或进程中断留下 `unknown` / `sending`。先到群内核实；确认未收到后，停止相关发送进程，备份该日期对应发送记录，将其 `status` 改为 `failed` 后手动发送；确认已收到则改为 `sent`。不要删除整个状态目录。
- 状态文件不保存 Webhook、密钥、企业名称或正文；预览 JSON 可能含业务名称，只存本地。

Webhook 网络异常下无法保证端到端“恰好一次”；当前优先避免重复，并保留不确定结果供核实。

参考：[飞书官方 Webhook 指南](https://www.feishu.cn/content/7271149634339422210)、[自定义机器人文档](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)。
