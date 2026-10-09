import { useEffect, useRef, useState } from "react";
import {
  Calculator,
  CalendarClock,
  Download,
  PackageSearch,
  Search,
  Truck,
} from "lucide-react";
import { money } from "./api";
import {
  shiftDay,
  type AnalysisResult,
  type AnalysisStep,
} from "./analytics-api";
import "./price-analysis.css";

const format = (value: unknown) => (value == null ? "—" : money(String(value)));
type Row = AnalysisResult["rows"][number];
const identity = (row: Row) =>
  JSON.stringify([row.code, row.specification, row.manufacturer, row.unit]);

async function post(path: string, body: unknown) {
  const response = await fetch(`/api/v1/analysis/stocking/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "请检查填写的数量、日期和天数。",
    );
  return data;
}

export function StockingPanel({
  day,
  ready,
  busy,
  onRun,
}: {
  day: string;
  ready: boolean;
  busy: boolean;
  onRun: (step: AnalysisStep) => void;
}) {
  const [start, setStart] = useState(day ? shiftDay(day, 1) : "");
  const [end, setEnd] = useState(day ? shiftDay(day, 30) : "");
  return (
    <section className="panel analytics-input price-input">
      <h3>先选备货期间，再逐个品种测算</h3>
      <p>
        库存依据：{day || "未加载"}{" "}
        的备份。选择计划期间，查看去年同期及最近30个完整日的出库情况。
      </p>
      {!ready ? (
        <p role="status">当前快照缺少带效期的库存依据，请先补齐库存快照。</p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onRun({
              domain: "inventory",
              kind: "stocking",
              metric: "stock",
              start_date: start,
              end_date_exclusive: shiftDay(end, 1),
              top_n: 50,
            });
          }}
        >
          <div className="growth-controls">
            <label>
              备货开始日期
              <input
                aria-label="备货开始日期"
                type="date"
                value={start}
                min={shiftDay(day, 1)}
                max={shiftDay(day, 365)}
                required
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              备货结束日期
              <input
                aria-label="备货结束日期"
                type="date"
                value={end}
                min={start}
                max={start ? shiftDay(start, 89) : undefined}
                required
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy} type="submit">
              <CalendarClock size={17} aria-hidden />
              {busy ? "正在整理依据…" : "查看备货依据"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function Scenario({ row, step }: { row: Row; step: AnalysisStep }) {
  const [transit, setTransit] = useState("");
  const [lead, setLead] = useState("");
  const [arrival, setArrival] = useState("");
  const [demand, setDemand] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const revision = useRef(0);
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    section.current?.focus();
  }, []);
  function changed() {
    revision.current++;
    setResult(null);
    setError("");
  }
  return (
    <section
      ref={section}
      tabIndex={-1}
      className="stocking-scenario"
      aria-label="品种备货情景"
    >
      <h4>
        <Calculator size={19} aria-hidden /> {String(row.name)} · 手动情景测算
      </h4>
      <p>
        {String(row.code)} · {String(row.specification)} ·{" "}
        {String(row.manufacturer)} · 单位：{String(row.unit)}
      </p>
      <p>
        默认需求 {format(row.baseline_demand)} {String(row.unit)}
        （去年同期日均折算）；近期速度折算 {format(row.recent_reference)}{" "}
        {String(row.unit)}，仅供对照。
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const current = revision.current;
          setBusy(true);
          setError("");
          setResult(null);
          try {
            const data = await post("scenario", {
              step,
              product_code: row.code,
              variant: {
                specification: row.specification,
                manufacturer: row.manufacturer,
                unit: row.unit,
              },
              in_transit_quantity: transit,
              lead_time_days: Number(lead),
              expected_arrival_date: Number(transit) > 0 ? arrival : null,
              expected_demand: demand === "" ? null : demand,
            });
            if (current === revision.current) setResult(data);
          } catch (e) {
            if (current === revision.current)
              setError(e instanceof Error ? e.message : "测算失败，请重试。");
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="growth-controls">
          <label>
            在途数量（{String(row.unit)}）
            <input
              aria-label="在途数量"
              type="number"
              min="0"
              max="1000000000000"
              step="0.0001"
              required
              placeholder="请填写，无在途填0"
              value={transit}
              onChange={(e) => {
                changed();
                setTransit(e.target.value);
              }}
            />
          </label>
          <label>
            供货周期（天）
            <input
              aria-label="供货周期"
              type="number"
              min="0"
              max="365"
              step="1"
              required
              placeholder="请手动填写"
              value={lead}
              onChange={(e) => {
                changed();
                setLead(e.target.value);
              }}
            />
          </label>
          {Number(transit) > 0 && (
            <label>
              预计到货日
              <input
                aria-label="预计到货日"
                type="date"
                required
                value={arrival}
                onChange={(e) => {
                  changed();
                  setArrival(e.target.value);
                }}
              />
            </label>
          )}
          <label>
            目标需求（{String(row.unit)}）
            <input
              aria-label="目标需求"
              type="number"
              min="0"
              max="1000000000000"
              step="0.0001"
              required={row.needs_manual_demand === true}
              placeholder={
                row.needs_manual_demand
                  ? "去年同期无出库，请填写"
                  : "留空沿用去年同期"
              }
              value={demand}
              onChange={(e) => {
                changed();
                setDemand(e.target.value);
              }}
            />
          </label>
        </div>
        <p className="analytics-hint">
          在途须未计入快照库存、效期覆盖目标期间，并假定专用于该期间。若到货前有其他用途，请只填可留给本次备货的数量。
        </p>
        <button className="button primary" type="submit" disabled={busy}>
          <Calculator size={16} aria-hidden />
          {busy ? "正在测算…" : "计算备货情景"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && (
        <div
          className="stocking-outcome"
          role="region"
          aria-label="备货测算结果"
        >
          <div className="price-summary">
            <div>
              <PackageSearch size={20} aria-hidden />
              <span>预计缺口 · {String(row.unit)}</span>
              <strong>{format(result.estimated_gap)}</strong>
              <span>情景估算，需人工复核</span>
            </div>
            <div>
              <span>目标开始时库存估算</span>
              <strong>{format(result.projected_start_stock)}</strong>
              <span>已扣除开始前预计消耗</span>
            </div>
            <div>
              <Truck size={20} aria-hidden />
              <span>计入的在途数量</span>
              <strong>{format(result.counted_in_transit)}</strong>
              <span>目标开始日或之前到货</span>
            </div>
            <div>
              <CalendarClock size={20} aria-hidden />
              <span>倒推最晚下单日</span>
              <strong>{String(result.latest_order_date)}</strong>
              <span>
                {result.lead_time_tight
                  ? "早于库存时点，需重新核对供货安排"
                  : "按手填供货周期倒推"}
              </span>
            </div>
          </div>
          <p>
            需求依据：{String(result.demand_source)}；目标需求{" "}
            {format(result.expected_demand)}，效期覆盖库存{" "}
            {format(result.eligible_stock)}，开始前预计消耗{" "}
            {format(result.consumption_before_target)} {String(row.unit)}。
          </p>
          <p>
            晚到在途 {format(result.late_in_transit)} {String(row.unit)}
            ，未抵减期初缺口。排除效期覆盖不足的库存{" "}
            {format(result.excluded_stock)} {String(row.unit)}。
          </p>
          <p>
            缺口 = max(0，目标需求 − 预计期初库存 −
            计入在途)。未计安全库存、锁库和采购包装取整，不是确定的采购下单量。
          </p>
          <details className="price-definition">
            <summary>查看全部测算假设</summary>
            <ul>
              {(result.assumptions as string[]).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}

export function StockingResult({
  result,
  step,
}: {
  result: AnalysisResult;
  step: AnalysisStep;
}) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [allRows, setAllRows] = useState<Row[]>(result.rows);
  const [loadingAll, setLoadingAll] = useState(false);
  const rows = allRows.filter((row) =>
    [row.name, row.code, row.specification, row.manufacturer].some((v) =>
      String(v ?? "").includes(search),
    ),
  );
  return (
    <article className="panel price-result" aria-label="旺季备货结果">
      <div className="growth-result-heading">
        <div>
          <h3>备货依据与情景测算</h3>
          <p>
            库存时点 {String(result.totals.snapshot_date)} · 目标{" "}
            {step.start_date} 至 {shiftDay(step.end_date_exclusive, -1)}
          </p>
        </div>
        <button
          className="button secondary"
          onClick={async () => {
            setError("");
            try {
              const data = await post("export", step);
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(data, null, 2)], {
                  type: "application/json",
                }),
              );
              const a = document.createElement("a");
              a.href = url;
              a.download = `备货完整依据-${step.start_date}.json`;
              a.click();
              URL.revokeObjectURL(url);
            } catch (e) {
              setError(e instanceof Error ? e.message : "下载失败");
            }
          }}
        >
          <Download size={16} aria-hidden />
          下载完整备货依据
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <p>
        去年同期：{String(result.totals.previous_start)} 至{" "}
        {shiftDay(String(result.totals.previous_end_exclusive), -1)}；近期：
        {String(result.totals.recent_start)} 至{" "}
        {shiftDay(String(result.totals.recent_end_exclusive), -1)}。
      </p>
      <div className="price-toolbar">
        <label>
          <Search size={17} aria-hidden />
          <input
            aria-label="搜索备货品种"
            placeholder="搜索已展示品种或编码"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      <p className="analytics-hint">
        共 {String(result.totals.product_count)} 个品种规格组合，已加载{" "}
        {allRows.length} 项，搜索匹配 {rows.length}{" "}
        项。不同单位分别测算；未核实的采购订单不自动视为在途。
      </p>
      {allRows.length < Number(result.totals.product_count) && (
        <button
          className="button secondary"
          disabled={loadingAll}
          onClick={async () => {
            setLoadingAll(true);
            setError("");
            try {
              const data = await post("export", step);
              setAllRows(data.rows);
            } catch (e) {
              setError(e instanceof Error ? e.message : "加载失败，请重试");
            } finally {
              setLoadingAll(false);
            }
          }}
        >
          {loadingAll ? "正在加载…" : "加载全部备货品种"}
        </button>
      )}
      <div className="table-scroll">
        <table className="price-table">
          <thead>
            <tr>
              <th>品种 / 单位</th>
              <th>去年同期 / 近期出库量</th>
              <th>目标需求参考</th>
              <th>库存 / 效期覆盖</th>
              <th>填写条件</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={identity(row)}>
                <td>
                  <strong>{String(row.name)}</strong>
                  <small>
                    {String(row.code)} ·{" "}
                    {String(row.specification ?? "规格缺失")} ·{" "}
                    {String(row.unit)}
                  </small>
                  <small>{String(row.manufacturer ?? "厂家缺失")}</small>
                </td>
                <td>
                  {format(row.previous_quantity)} /{" "}
                  {format(row.recent_quantity)}
                </td>
                <td>
                  {format(row.baseline_demand)}
                  <small>近期速度折算 {format(row.recent_reference)}</small>
                  {row.needs_manual_demand === true && (
                    <small>同期无出库，需手填需求</small>
                  )}
                </td>
                <td>
                  {format(row.stock_quantity)} / {format(row.eligible_stock)}
                  <small>
                    效期不足 {format(row.excluded_stock)}；效期未知{" "}
                    {format(row.unknown_expiry_stock)}
                  </small>
                </td>
                <td>
                  <button
                    className="button secondary"
                    disabled={row.scenario_ready !== true}
                    aria-pressed={
                      selected !== null && identity(selected) === identity(row)
                    }
                    onClick={() => setSelected(row)}
                  >
                    填写备货条件
                  </button>
                  {row.scenario_ready !== true && (
                    <small>规格厂家或效期资料待核实</small>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <p role="status">
          当前已加载列表没有匹配品种，可加载全部品种、清除搜索或下载完整依据。
        </p>
      )}
      {selected && (
        <Scenario key={identity(selected)} row={selected} step={step} />
      )}
      <details className="price-definition">
        <summary>备货参考与库存口径</summary>
        <ul>
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}
