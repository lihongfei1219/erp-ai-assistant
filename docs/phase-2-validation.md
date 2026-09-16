# 暂定业务规则与经营看板验证

日期：2026-09-16。用户授权：按常识采用可调整默认规则继续开发，未确定业务细节汇总到一个文档。

## 本次交付

- [业务假设台账](business-assumptions.md)：集中记录 B01—B18 共 18 项规则与未知事项，包含默认做法、影响、修改位置、优先级和确认状态。
- `backend/config/business-rules.json`：暂定有效销售纳入订单完成、已出库；CNY 与 Asia/Shanghai 暂定。无资金事件时不生成支付／退款事实。
- pandas 派生有效销售指标，保留全部原始订单；卡片、趋势、排行和订单列表使用同一套规则。未知状态排除并提示。
- 快照保存规则全文、policy_id 和内容指纹；改配置后重算新文件，旧快照与原始指标不变。
- React + TypeScript 看板：经营概览、订单明细及证据弹窗、业务口径及台账下载；支持原始／有效视图、加载、失败重试、空结果、退出和移动端布局。
- SQL 仅增读授权订单中的企业／商品名称用于展示，统计键仍是业务编码。数据和截图不进入 Git。
- 本地 FastAPI 同源提供已构建页面，访问令牌仍限制业务接口；前端不请求第三方字体、图片或模型服务。

## 自动化验证

在 `backend/` 实际执行：

```powershell
$env:ERP_RUN_INTEGRATION = '1'
..\.venv\Scripts\python.exe -m pytest tests -q --basetemp ../.local/phase2-backend-final -o cache_dir=../.local/pytest-cache
..\.venv\Scripts\ruff.exe check . --no-cache
```

结果：**76 passed**，包括 73 项单元／API 测试和 3 项 SQL Server 只读集成测试；Ruff 检查通过。新增覆盖状态规则变更、原始／有效金额分离、排行／趋势一致性、未知状态、全部排除、错误配置、内容指纹、快照保留规则、显示名称空值、旧快照兼容与台账下载权限。

在 `frontend/` 实际执行：

```powershell
npm run build
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm run test:e2e
```

构建含 TypeScript 类型检查，通过。**8 项前端测试通过**：

1. 金额显示使用十进制字符串与 BigInt，覆盖浮点精度之外的大金额、负数及四位小数。
2. 登录、原始／有效总额切换、订单分页、明细弹窗及退出清除令牌。
3. 口径解释与集中台账下载。
4. 错误令牌返回登录页，不展示业务数据。
5. 快照失败显示错误及重试，不能显示成零成交。
6. 无有效订单时明确空结果，平均金额显示为空。
7. 390×844 手机尺寸导航可用，无页面横向溢出。
8. 1440×1080 桌面正常渲染，无运行时异常。

浏览器测试通过真实 FastAPI 服务提供合成记录，不连接 ERP。测试服务器自动启停，仅绑定本机 8765 端口。桌面与手机截图已人工查看，保存在被 Git 忽略的 `.local/`。

首次浏览器测试因本机缺少 Playwright 匹配版本 Chromium 1243 而未启动，改用已安装的 Chrome 后全部通过，没有为此下载新浏览器。后端保留此前两条测试依赖弃用警告；Vite 构建对 Lucide 的 `use client` 指令产生忽略提示，此项目使用浏览器 React 渲染，构建与浏览器测试均完成。未隐藏这些提示。

## 真实备份走查

实际执行：

```powershell
..\.venv\Scripts\python.exe -m workers.analyze_sales --start 2026-09-01 --end 2026-09-17 --source-as-of 2026-09-16T14:33:41+08:00 --all-buyers --output ../.local/reports/platform-operating-20260916.json
```

新快照包含 152 张原始订单、191 行明细，其中暂定有效订单 148 张、排除 4 张、未知状态 0。原始主明细及 SQL 控制总数／金额对账均一致，旧快照保留未覆盖。

用本机 Chrome 和临时随机回环端口验证真实看板：有效 148／原始 152 的切换、实际订单明细弹窗和手机布局均通过；浏览器未产生运行时异常，应用页面没有向外部地址发起数据请求。真实截图仅保存在 `.local/phase2-real-dashboard.png`，不提交版本控制。临时服务在验证结束后已停止。

`Start-LocalApi.ps1` 优先加载新的经营快照，启动入口为 `http://127.0.0.1:8000/`；令牌位置及完整命令见 README。脚本语法解析检查通过。

## 已知范围

本次没有修改源数据库，没有新增线上发布、数据库迁移或外部模型调用。页面的日期范围来自当前快照；“重新读取快照”重新获取 API 结果，变更日期或业务规则仍需后台重算新快照并重启服务。当前是本机试点，正式多用户权限、助手库、任务调度、增量同步、AI 问数、Top 5 和运营待办仍待实现。

技术选择参考：[Vite 6 环境要求](https://v6.vite.dev/guide/)、[React TypeScript](https://react.dev/learn/typescript)、[Playwright 浏览器测试](https://playwright.dev/docs/intro)。本次 Vite 6 已在本机 Node 20.18 上完成构建与测试。
