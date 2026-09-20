# 飞书应用机器人接入

本步骤连接用于群内 @问答的 **企业自建应用机器人**。日报自定义机器人Webhook与其凭据和作用不同。本页说明首次应用配置与连接测试；完成后按[飞书自然语言分析](feishu-analytics.md)启用`--sales`六类问数。本机现有应用已接入，不需要重复发现或授权。

## 1. 创建应用并填写凭据

在 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用，建议命名“ERP 经营助手”，添加“机器人”能力。

在“凭证与基础信息”复制 App ID、App Secret，填写项目 `.local/feishu-app.json`：

```json
{
  "app_id": "cli_替换为应用ID",
  "app_secret": "替换为应用密钥",
  "tenant_key": "",
  "allowed_chat_ids": [],
  "allowed_user_open_ids": []
}
```

本机已准备空模板。其他机器可复制 `backend/config/feishu-app.example.json`，不要覆盖已有凭据。UTF-8 保存，保留双引号；密钥不用发到聊天中。

也支持 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 进程环境变量，优先于文件；群、用户和租户授权仍在文件配置。`.env` 不自动读取。配置修改后重启连接进程。

在项目根目录执行离线检查（不会请求飞书）：

```powershell
.\.venv\Scripts\python.exe .\scripts\start_feishu_bot.py --check
```

## 2. 开通权限和机器人可见范围

在应用后台“权限管理”申请：

- 获取群聊中 @机器人的消息：`im:message.group_at_msg:readonly`。
- 以应用身份发消息：`im:message:send_as_bot`。

如后台权限名称调整，以对应接收／回复消息 API 的权限说明为准。当前仅接收群内 @本机器人的文本，不需要读取整个群所有聊天消息，不处理私聊。

按企业管理要求配置可见范围并发布应用版本。只将机器人加入用于全平台经营分析的内部运营群。后台权限、发布和管理员审批未完成时，即使长连接建立也不代表能正常收发。

## 3. 启动长连接，再保存事件订阅

先验证应用凭据和机器人身份（不发送消息）：

```powershell
.\.venv\Scripts\python.exe .\scripts\start_feishu_bot.py --probe
```

再启动发现模式：

```powershell
.\.venv\Scripts\python.exe .\scripts\start_feishu_bot.py --discover
```

保持进程运行，看到“飞书长连接已建立”后，在应用后台“事件与回调／事件订阅”选择 **使用长连接接收事件**，保存并添加 **接收消息 v2.0**（`im.message.receive_v1`）。如这些变更要求重新发布，请发布后再测试。长连接由本机主动访问飞书，不需要本机公网回调地址。

## 4. 发现并授权指定群和用户

将应用机器人加入内部测试群，由允许使用机器人的运营人员发送：

```text
@ERP 经营助手 连接测试
```

需要从飞书 @候选列表中选择机器人，普通文字输入名称不算真正 @。

发现模式不会回复。控制台提示收到事件后，打开 `.local/feishu-app/discovery.json`，找到目标群和发送人的记录：

```json
[
  {
    "app_id": "cli_应用ID",
    "tenant_key": "企业租户标识",
    "chat_id": "oc_群标识",
    "user_open_id": "ou_运营用户标识"
  }
]
```

核对这些确实是刚才的目标内部群和运营人员，将租户标识填入 `tenant_key`，群 ID 填入 `allowed_chat_ids` 数组，用户 ID 填入 `allowed_user_open_ids` 数组。发现记录不会自动授权。当前一个应用配置一个租户，授权用户可在任一授权群中进行连接测试。

文件只保存最多 20 组标识，不保存聊天正文。获取其他运营人员的标识时，让其在同一目标群内再次 @机器人。

## 5. 开启授权连接测试

按 Ctrl+C 停止发现模式，运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\start_feishu_bot.py --listen
```

再次在群内 @机器人发送“连接测试”，预期在原消息下收到：

```text
机器人连接正常：已收到群内 @消息，并成功调用原消息回复接口。
当前处于连接测试阶段，尚未启用销售问数。
```

其他文本返回连接阶段帮助信息，不会发送订单或销售数据。未授权租户、群、用户，机器人自身消息，以及没有 @当前机器人的消息都忽略。

连接测试消息先持久化到 `.local/feishu-app/inbox/`，再由后台线程发送，避免阻塞事件确认。按原消息 ID 去重，进程重启后恢复待处理项；发送中断或结果不明确时保留 `sending` / `unknown`，不自动重复回复。可查看群内消息后，发送一条新的“连接测试”进行排查，不要批量删除状态记录。

此本地收件箱只服务接入测试；当前`--sales`已使用独立助手SQL Server任务库。两者的状态都不属于可随意删除的缓存。

## 排查顺序

| 现象 | 检查 |
|---|---|
| 配置检查失败 | JSON 格式、App ID 是否 cli_ 开头、密钥是否非空；检查同名环境变量是否覆盖文件 |
| 凭据验证失败 | App ID／Secret 配对、机器人能力、网络访问；应用是否处于可用状态 |
| 无法保存长连接订阅 | 发现进程是否仍运行、是否出现已建立提示、后台所选应用是否与本机一致 |
| 群里看不到机器人 | 机器人能力、可见范围、版本发布、群类型和企业管理员策略 |
| 发现模式没有记录 | 接收消息事件是否订阅并生效、@消息权限、是否真正 @应用机器人而非旧 Webhook 机器人 |
| 发现有记录但不回复 | 发现模式本来不回复；填好授权配置后重启为 --listen |
| 授权模式仍不回复 | 租户、群、用户标识是否一致；发消息权限是否已批准；本地 inbox 状态是否 unknown |

参考：[官方 Python SDK](https://github.com/larksuite/oapi-sdk-python)、[事件处理](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events)、[接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)、[回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)。
