# 第一阶段验证记录

日期：2026-09-16。范围：QY 备份只读接入、pandas 销售订单计算、本地不可覆盖快照、FastAPI 查询接口。数据所有者确认备份覆盖平台全部商家；正式成交口径仍需确认。

## 数据核验

从项目根目录实际执行：

```powershell
.\scripts\Inspect-ErpSchema.ps1
.\scripts\Profile-Erp.ps1
```

两个命令均完成。元数据导出覆盖 697 张表、14,486 个字段；业务字典导出 451 条所选单据字段定义。主明细、客户商品编码、退货原单及收款覆盖检查见 [数据字典](data-dictionary.md)。输出留在 `.local/discovery/`，未提交业务记录。

## 自动化测试

最终在 `backend/` 执行：

```powershell
$env:ERP_RUN_INTEGRATION = '1'
..\.venv\Scripts\python.exe -m pytest tests -q --basetemp ../.local/test-tmp-verified -o cache_dir=../.local/pytest-cache
..\.venv\Scripts\ruff.exe check . --no-cache
..\.venv\Scripts\ruff.exe format . --check --no-cache
```

结果：**55 passed，0 failed**，其中 52 项合成数据单元／API 测试、3 项 SQL Server 只读集成测试。Ruff 静态检查通过，18 个 Python 文件格式检查通过。

覆盖：

- 全部状态订单指标、客户跨日去重、单订单多明细、商品排行、金额四位精度与空结果。
- 重复键、空字段、缺字段、孤立明细、无明细订单、主明细差异和 SQL 控制总数差异。
- 拒绝浮点金额，保留 ERP 金额并记录数量 × 单价差异，输入 DataFrame 不被修改。
- 日期起止、来源截至时刻、备份之后日期不补零、读取范围和行数上限。
- 企业范围在 SQL 中参数化并在分析函数再次校验；注入形式的企业编码不会扩大查询。
- 业务 API 的开发令牌、未授权拒绝、无快照状态、坏快照、分页、未知筛选拒绝、受限快照内不存在的企业订单不可读取。
- 快照读写、精度保持、文件不能覆盖、失败时临时文件清理。

测试依赖产生两条弃用警告：Starlette 对 httpx TestClient 的兼容路径，以及 AnyIO 的 BlockingPortal 别名。当前锁定版本测试通过；后续升级测试栈时处理，未隐藏警告。

第一次在 Windows 沙箱默认临时目录运行单元／API 测试时，测试用例已执行，但收尾未正常退出；该进程被中止，未计为通过。改为项目内显式临时目录并在正常 Windows 身份下重跑，以上最终命令正常退出。Ruff 的沙箱写入权限问题同样通过仅针对项目文件的正常身份运行解决。

## 真实快照与 HTTP 验证

实际执行独立分析命令：

```powershell
..\.venv\Scripts\python.exe -m workers.analyze_sales --start 2026-09-01 --end 2026-09-17 --source-as-of 2026-09-16T14:33:41+08:00 --all-buyers --output ../.local/reports/platform-sales-20260916.json
```

成功生成本地开发快照：152 张订单、191 行明细；逐单表头／明细差异为 0，独立 SQL 控制总数及金额一致。所选日期包含备份当日，接口明确提示仅截至备份时刻有数据。

这一次运行记录：源数据读取约 0.069 秒，分析约 0.127 秒，DataFrame 内存约 134 KB，采样时进程峰值工作集约 97 MB。该峰值在计算阶段记录，不包含后续文件序列化；结果只代表本地小范围试点，不能外推全平台生产容量。

使用随机本机回环端口临时启动真实 Uvicorn 服务，验证 `/dashboard/summary`、`/sales/trends`、`/sales/breakdown`、`/sales/states`、`/data/status`、`/orders` 六个业务接口返回 200、带正确版本与范围，未授权请求返回 401。验证结束已停止临时服务，没有保留后台监听。

PowerShell 的三个交付脚本均通过语法解析检查。数据库备份、真实结果、虚拟环境和已有 `.ssh_known_hosts` 已验证被 Git 忽略；`.ssh_known_hosts` 原有内容未修改。

## 未覆盖范围

没有修改源库，没有验证线上 ERP 应用兼容性，没有声称现有 Windows 账号已经是数据库层只读账号。生产接入仍需正式配置对象受限账号和负载预算。

本阶段没有前端业务页面、平台 SSO、多角色权限、助手 SQL Server 数据库、增量同步、持久化任务调度、支付／退款计算、AI、Top 5 或运营待办。因此也没有对这些功能作出测试通过或生产可用的声明。
