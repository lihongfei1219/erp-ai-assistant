import { useEffect, useRef, useState } from "react";
import {
  CalendarClock,
  Download,
  FileSearch,
  PackageSearch,
  Search,
  X,
} from "lucide-react";
import { money } from "./api";
import {
  shiftDay,
  type AnalysisResult,
  type AnalysisStep,
} from "./analytics-api";
import "./price-analysis.css";

type Run = { busy: boolean; onRun: (step: AnalysisStep) => void };
const format = (value: unknown) => (value == null ? "—" : money(String(value)));

export function InventoryRiskPanel({
  day,
  ready,
  busy,
  onRun,
}: Run & { day: string; ready: boolean }) {
  const [lookback, setLookback] = useState(30);
  const [age, setAge] = useState(90);
  const [expiry, setExpiry] = useState(180);
  return (
    <section className="panel analytics-input price-input">
      <h3>从库龄、资金占用和效期查看库存</h3>
      <p>
        库存时点：{day || "未加载"}
        。按该时点之前的完整出库记录估算，不代表今天的实时库存。
      </p>
      {!ready ? (
        <p role="status">
          当前快照尚未加载库龄、效期和采购成本，请生成库存积压快照。
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onRun({
              domain: "inventory",
              kind: "inventory_risk",
              metric: "stock",
              start_date: day,
              end_date_exclusive: shiftDay(day, 1),
              lookback_days: lookback,
              age_threshold_days: age,
              expiry_threshold_days: expiry,
              top_n: 50,
            });
          }}
        >
          <div className="growth-controls">
            <label>
              销售观察天数
              <input
                aria-label="销售观察天数"
                type="number"
                min={7}
                max={90}
                required
                value={lookback}
                onChange={(e) => setLookback(Number(e.target.value))}
              />
            </label>
            <label>
              库龄关注阈值（天）
              <input
                aria-label="库龄关注阈值"
                type="number"
                min={1}
                max={3650}
                required
                value={age}
                onChange={(e) => setAge(Number(e.target.value))}
              />
            </label>
            <label>
              剩余效期阈值（天）
              <input
                aria-label="剩余效期阈值"
                type="number"
                min={1}
                max={730}
                required
                value={expiry}
                onChange={(e) => setExpiry(Number(e.target.value))}
              />
            </label>
          </div>
          <p className="analytics-hint">
            同品种批次按先到期先售出估算；无近期出库、已过期或资料缺失单独提示。
          </p>
          <button className="button" disabled={busy || !day}>
            {busy ? "正在分析…" : "分析库存积压"}
          </button>
        </form>
      )}
    </section>
  );
}

function BatchEvidence({
  row,
  step,
}: {
  row: AnalysisResult["rows"][number];
  step: AnalysisStep;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    setData(null);
    setError("");
    const controller = new AbortController();
    fetch("/api/v1/analysis/inventory-risk/evidence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step, record_ids: row.record_ids }),
      signal: controller.signal,
    })
      .then(async (r) => {
        const value = await r.json();
        if (!r.ok) throw new Error(value.detail || "读取批次依据失败");
        setData(value.items);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [open, row, step]);
  return (
    <>
      <button className="analytics-evidence" onClick={() => setOpen(true)}>
        <FileSearch size={15} aria-hidden />
        查看批次依据
      </button>
      {open && (
        <dialog
          ref={dialog}
          className="analytics-dialog"
          aria-label="批次库存依据"
          onCancel={() => setOpen(false)}
        >
          <button
            className="dialog-close"
            aria-label="关闭批次依据"
            onClick={() => setOpen(false)}
          >
            <X size={18} />
          </button>
          <h3>批次库存原始依据</h3>
          {error && <p role="alert">{error}</p>}
          {!data && !error && <p>正在加载…</p>}
          {data && (
            <div className="analytics-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>记录ID</th>
                    <th>批次</th>
                    <th>数量</th>
                    <th>采购单价</th>
                    <th>入库日期</th>
                    <th>到期日</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((r) => (
                    <tr key={String(r.record_id)}>
                      {[
                        "record_id",
                        "batch_code",
                        "quantity",
                        "purchase_unit_cost",
                        "received_date",
                        "expiry_date",
                      ].map((key) => (
                        <td key={key}>{String(r[key] ?? "—")}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </dialog>
      )}
    </>
  );
}

export function InventoryRiskResult({
  result,
  step,
}: {
  result: AnalysisResult;
  step: AnalysisStep;
}) {
  const totals = result.totals;
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const rows = result.rows.filter(
    (row) =>
      (filter === "all" || row[filter] === true) &&
      [row.name, row.code, row.batch_code].some((v) =>
        String(v).includes(search.trim()),
      ),
  );
  async function download() {
    setDownloading(true);
    setError("");
    try {
      const response = await fetch("/api/v1/analysis/inventory-risk/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(step),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail || "导出失败");
      const url = URL.createObjectURL(
        new Blob([JSON.stringify({ step, result: value }, null, 2)], {
          type: "application/json;charset=utf-8",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `库存积压完整明细-${step.start_date}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setDownloading(false);
    }
  }
  return (
    <article className="panel price-result" aria-label="库存积压结果">
      <div className="price-result-heading">
        <h3>{result.title}</h3>
        <button
          className="button secondary"
          disabled={downloading}
          onClick={() => void download()}
        >
          <Download size={15} aria-hidden />
          下载库存完整明细
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <p className="analytics-hint">
        库存截至 {String(totals.as_of)} · 销售速度观察：
        {String(totals.lookback_start)} 至{" "}
        {shiftDay(String(totals.lookback_end_exclusive), -1)}
      </p>
      <div className="price-summary">
        <div className="price-stat">
          <span>
            <PackageSearch size={18} aria-hidden />
            有库存批次分组
          </span>
          <strong>{String(totals.batch_count)}</strong>
          <span>同批号不同入库日或成本分开</span>
        </div>
        <div className="price-stat">
          <span>采购成本占用</span>
          <strong className="margin-number">
            {format(totals.occupied_amount)}
          </strong>
          <span>CNY · 成本缺失 {String(totals.unknown_cost_batches)} 组</span>
        </div>
        <button
          className="price-stat decrease"
          aria-pressed={filter === "aged"}
          onClick={() => setFilter(filter === "aged" ? "all" : "aged")}
        >
          <span>
            <CalendarClock size={18} aria-hidden />
            库龄 ≥{String(totals.age_threshold_days)}天
          </span>
          <strong>{String(totals.aged)}</strong>
          <span>点击筛选久存批次</span>
        </button>
        <button
          className="price-stat decrease"
          aria-pressed={filter === "near_expiry"}
          onClick={() =>
            setFilter(filter === "near_expiry" ? "all" : "near_expiry")
          }
        >
          <span>距到期 ≤{String(totals.expiry_threshold_days)}天</span>
          <strong>{String(totals.near_expiry)}</strong>
          <span>另有已过期 {String(totals.expired)} 组</span>
        </button>
      </div>
      <div className="price-toolbar">
        <label>
          <Search size={17} aria-hidden />
          <input
            aria-label="搜索库存批次"
            placeholder="搜索已展示的品种、编码或批次"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select
          aria-label="库存关注项"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">全部关注项</option>
          <option value="aged">库龄较长</option>
          <option value="near_expiry">临期</option>
          <option value="expired">已过期</option>
          <option value="no_recent_sales">近期无出库</option>
          <option value="expiry_mismatch">预计售完晚于到期</option>
          <option value="data_issue">资料待核实</option>
        </select>
      </div>
      <p className="analytics-hint">
        概览包含全部分组，列表已返回 {result.rows.length} 组，当前筛选显示{" "}
        {rows.length} 组。完整明细可下载；表格可横向滚动。
      </p>
      {rows.length ? (
        <div className="table-scroll">
          <table className="price-table">
            <thead>
              <tr>
                <th>品种 / 批次</th>
                <th>数量 / 占用</th>
                <th>库龄 / 到期日</th>
                <th>销售速度 / 预计售完</th>
                <th>关注项与依据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.source_ids)}>
                  <td>
                    <strong>{String(row.name)}</strong>
                    <small>
                      {String(row.code)} ·{" "}
                      {String(row.specification ?? "规格缺失")}
                    </small>
                    <small>{String(row.manufacturer ?? "厂家缺失")}</small>
                    <small>批次 {String(row.batch_code || "未提供")}</small>
                  </td>
                  <td>
                    {format(row.quantity)} {String(row.unit)}
                    <small>占用 {format(row.occupied_amount)} CNY</small>
                  </td>
                  <td>
                    {row.age_days == null ? "库龄未知" : `${row.age_days} 天`}
                    <small>入库 {String(row.received_date ?? "—")}</small>
                    <small>到期 {String(row.expiry_date ?? "—")}</small>
                  </td>
                  <td>
                    日均 {format(row.daily_quantity)} {String(row.unit)}
                    <small>
                      {row.estimated_clear_days == null
                        ? "暂不估算售完时间"
                        : `累计售完约 ${format(row.estimated_clear_days)} 天`}
                    </small>
                    <small>
                      {row.no_recent_sales === true
                        ? "观察期内无出库"
                        : "包含前面优先售出的批次"}
                    </small>
                  </td>
                  <td>
                    <strong>{String(row.risk_tags)}</strong>
                    {row.data_note && <small>{String(row.data_note)}</small>}
                    <BatchEvidence row={row} step={step} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status">
          当前已展示分组没有匹配项，可切换关注项或下载完整明细。
        </p>
      )}
      <details className="price-definition">
        <summary>库龄、销售速度与效期估算口径</summary>
        <ul>
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}
