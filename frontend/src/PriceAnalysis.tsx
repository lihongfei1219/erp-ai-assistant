import { useEffect, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CircleHelp,
  Equal,
  Search,
} from "lucide-react";
import { money } from "./api";
import {
  shiftDay,
  type AnalysisResult,
  type AnalysisStep,
} from "./analytics-api";
import { GrowthActions, GrowthExport } from "./GrowthAnalysis";
import "./price-analysis.css";

type Coverage = { start: string; end_exclusive: string }[];
type Run = { busy: boolean; onRun: (step: AnalysisStep) => void };

function latestMonth(coverage: Coverage) {
  const intervals = [...coverage].sort((a, b) =>
    a.start.localeCompare(b.start),
  );
  function covered(start: string, end: string) {
    let cursor = start;
    for (const part of intervals)
      if (part.start <= cursor && cursor < part.end_exclusive)
        cursor = part.end_exclusive;
    return cursor >= end;
  }
  if (!intervals.length) return null;
  const last = new Date(
    `${intervals[intervals.length - 1].end_exclusive}T00:00:00Z`,
  );
  const month = (offset: number) =>
    new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth() + offset, 1))
      .toISOString()
      .slice(0, 10);
  for (let i = 0; i > -36; i--) {
    const start = month(i),
      end = month(i + 1);
    if (
      covered(start, end) &&
      covered(month(i - 1), start) &&
      covered(month(i - 12), month(i - 11))
    )
      return { start, end };
  }
  return null;
}

export function PricePanel({
  coverage,
  busy,
  onRun,
  mode = "price",
  costAvailable = false,
}: Run & {
  coverage: Coverage;
  mode?: "price" | "margin";
  costAvailable?: boolean;
}) {
  const margin = mode === "margin";
  const label = margin ? "毛利" : "售价";
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [basis, setBasis] = useState<AnalysisStep["growth_basis"]>("both");
  const [sort, setSort] = useState<AnalysisStep["growth_sort"]>("delta");
  useEffect(() => {
    const value = latestMonth(coverage);
    if (value) {
      setStart(value.start);
      setEnd(shiftDay(value.end, -1));
    }
  }, [coverage]);
  return (
    <section className="panel analytics-input price-input">
      <h3>
        {margin ? "销量增长是否带来了更多毛利" : "比较同品种的加权平均售价"}
      </h3>
      <p>
        {margin
          ? "销售出库收入减去对应采购成本，再拆解销量、售价和成本变化的影响。"
          : "出库金额 ÷ 出库数量。同规格、厂家和单位分别比较，可继续查看客户售价和出库证据。"}
      </p>
      <div className="price-cost-notice">
        <CircleHelp size={18} aria-hidden />
        <span>
          {costAvailable
            ? "成本采用出库行保存的采购单价；未扣退货、税额未拆分，不等于净利润。"
            : "当前快照未加载核验后的采购成本；售价可查，毛利空间需补充成本快照。"}
        </span>
      </div>
      {!coverage.length ? (
        <p role="status">
          {margin
            ? "当前快照缺少可比较的已核验采购成本，请生成带成本的新快照。"
            : "当前快照缺少可比较的历史出库数据，请先补充快照。"}
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onRun({
              domain: "shipping",
              kind: mode,
              metric: margin ? "gross_profit" : "unit_price",
              dimension: "product",
              start_date: start,
              end_date_exclusive: shiftDay(end, 1),
              growth_basis: basis,
              growth_sort: sort,
              top_n: 10,
            });
          }}
        >
          <div className="growth-controls">
            <label>
              本期开始
              <input
                aria-label={`${label}比较开始日期`}
                type="date"
                required
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <label>
              本期结束
              <input
                aria-label={`${label}比较结束日期`}
                type="date"
                required
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <label>
              比较基准
              <select
                aria-label={`${label}比较基准`}
                value={basis}
                onChange={(e) => setBasis(e.target.value as typeof basis)}
              >
                <option value="both">上期及去年同期</option>
                <option value="previous">上期</option>
                <option value="year_over_year">去年同期</option>
              </select>
            </label>
            <label>
              优先查看
              <select
                aria-label={`${label}比较排序`}
                value={sort}
                onChange={(e) => setSort(e.target.value as typeof sort)}
              >
                <option value="delta">
                  {margin ? "毛利变化最大的品种" : "每单位价差最大的品种"}
                </option>
                <option value="rate">变化率最大的品种</option>
              </select>
            </label>
          </div>
          <p className="analytics-hint">
            默认选最近可同时比较上期和去年的完整月份；也可自选 1–90
            天。未扣退货、税额未拆分。
          </p>
          <button
            className="button"
            disabled={busy || !start || !end || start > end}
          >
            {busy ? "正在比较…" : margin ? "比较销量与毛利" : "比较售价变化"}
          </button>
        </form>
      )}
    </section>
  );
}

type Summary = {
  basis: string;
  total: number;
  shown: number;
  decrease: number;
  increase: number;
  unchanged: number;
  unavailable: number;
};
const labels: Record<string, string> = {
  previous: "较上期",
  year_over_year: "较去年同期",
  current: "本期",
};
const choices = [
  { key: "decrease", label: "下降", icon: ArrowDownRight },
  { key: "increase", label: "上涨", icon: ArrowUpRight },
  { key: "unchanged", label: "持平", icon: Equal },
  { key: "unavailable", label: "不可比", icon: CircleHelp },
] as const;
const format = (value: unknown) =>
  value === null || value === undefined ? "—" : money(String(value), 4);

export function PriceResult({
  result,
  step,
  busy,
  onRun,
}: Run & { result: AnalysisResult; step: AnalysisStep }) {
  const groups = result.totals.groups as Summary[];
  const periods = result.totals.periods as Record<
    string,
    { start: string; end_exclusive: string }
  >;
  const [basis, setBasis] = useState(groups[0]?.basis || "previous");
  const [direction, setDirection] = useState("全部");
  const [search, setSearch] = useState("");
  const group = groups.find((item) => item.basis === basis);
  const rows = result.rows.filter(
    (row) =>
      row.basis === basis &&
      (direction === "全部" || row.direction === direction) &&
      [row.name, row.code, row.specification, row.manufacturer].some((text) =>
        String(text ?? "")
          .toLocaleLowerCase()
          .includes(search.trim().toLocaleLowerCase()),
      ),
  );
  return (
    <article className="panel price-result" aria-label="售价变化结果">
      <div className="price-result-heading">
        <h3>{result.title}</h3>
        <GrowthExport step={step} />
      </div>
      {step.dimension === "buyer" && (
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => onRun({ ...step, dimension: "product" })}
        >
          返回该品种售价
        </button>
      )}
      <div className="price-basis" role="group" aria-label="售价比较结果切换">
        {groups.map((item) => (
          <button
            key={item.basis}
            aria-pressed={basis === item.basis}
            onClick={() => {
              setBasis(item.basis);
              setDirection("全部");
            }}
          >
            {labels[item.basis]}
          </button>
        ))}
      </div>
      <p className="analytics-hint">
        本期：{periods.current.start} 至{" "}
        {shiftDay(periods.current.end_exclusive, -1)} · 比较期：
        {periods[basis].start} 至 {shiftDay(periods[basis].end_exclusive, -1)} ·
        CNY / 对应单位
      </p>
      <div className="price-summary">
        {choices.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            className={`price-stat ${key}`}
            aria-pressed={direction === label}
            onClick={() => setDirection(direction === label ? "全部" : label)}
          >
            <span>
              <Icon size={18} aria-hidden />
              {label === "不可比" ? "不可比较" : `均价${label}`}
            </span>
            <strong>
              {group?.[key] ?? 0}
              <small>组</small>
            </strong>
            <span>点击筛选明细</span>
          </button>
        ))}
      </div>
      <p className="price-explanation">
        均价变化可能来自客户采购占比变化，并不直接代表实际调价。点击“查看客户售价”，核对同一客户的情况。
      </p>
      <div className="price-toolbar">
        <label>
          <Search size={17} aria-hidden />
          <input
            aria-label="搜索售价结果"
            placeholder="搜索当前已展示的品种、客户或编码"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <button
          className="button secondary"
          onClick={() => {
            setSearch("");
            setDirection("全部");
          }}
        >
          查看全部方向
        </button>
      </div>
      <p className="analytics-hint">
        概览共 {group?.total ?? 0} 组，当前比较基准返回 {group?.shown ?? 0}{" "}
        组；筛选后显示 {rows.length} 组。每个方向最多 {step.top_n ?? 10}{" "}
        组，完整结果可下载。
      </p>
      {rows.length ? (
        <div className="table-scroll">
          <table className="price-table">
            <thead>
              <tr>
                <th>
                  {step.dimension === "buyer"
                    ? "客户 / 品种规格"
                    : "品种 / 规格"}
                </th>
                <th>均价变化</th>
                <th>比较期 → 本期均价</th>
                <th>比较期 → 本期数量</th>
                <th>单位采购成本 / 毛利空间</th>
                <th>核对依据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  <td>
                    <strong>{String(row.name)}</strong>
                    <small>
                      {String(row.code)} · {String(row.specification)} ·{" "}
                      {String(row.unit)}
                    </small>
                    <small>{String(row.manufacturer)}</small>
                  </td>
                  <td>
                    <span
                      className={`price-direction ${row.direction === "下降" ? "decrease" : row.direction === "上涨" ? "increase" : "neutral"}`}
                    >
                      {row.direction === "下降" ? (
                        <ArrowDownRight size={16} aria-hidden />
                      ) : row.direction === "上涨" ? (
                        <ArrowUpRight size={16} aria-hidden />
                      ) : (
                        <Equal size={16} aria-hidden />
                      )}
                      {String(row.direction)}
                    </span>
                    <strong>{format(row.delta)}</strong>
                    <small>
                      {String(row.change_percent ?? "无有效变化率")}
                    </small>
                  </td>
                  <td>
                    <span>
                      {format(row.previous_price)} →{" "}
                      <strong>{format(row.current_price)}</strong>
                    </span>
                    <small>CNY / {String(row.unit)}</small>
                  </td>
                  <td>
                    <span>
                      {format(row.previous_quantity)} →{" "}
                      {format(row.current_quantity)}
                    </span>
                    <small>{String(row.record_state)}</small>
                    {step.dimension !== "buyer" && (
                      <small>
                        两期均有出库的客户：{String(row.common_customers)} 家
                      </small>
                    )}
                  </td>
                  <td>
                    <span>
                      成本：{format(row.previous_unit_cost)} →{" "}
                      {format(row.current_unit_cost)}
                    </span>
                    <small>
                      空间：{format(row.previous_spread)} →{" "}
                      {format(row.current_spread)}
                    </small>
                    <small>
                      {row.spread_delta == null
                        ? "成本或可比销量不足"
                        : `空间变化 ${format(row.spread_delta)} / ${String(row.unit)}`}
                    </small>
                  </td>
                  <td>
                    <GrowthActions
                      row={row}
                      step={step}
                      busy={busy}
                      onRun={onRun}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status">
          当前筛选没有明细。可切换方向、清空搜索，或下载完整结果。
        </p>
      )}
      <details className="price-definition">
        <summary>计算口径与数据限制</summary>
        <ul>
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}
