import { useState } from "react";
import {
  CircleDollarSign,
  TrendingUp,
  Package,
  AlertTriangle,
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

type Summary = {
  basis: string;
  total: number;
  shown: number;
  attention: number;
  current_amount: string;
  current_cost: string;
  current_profit: string;
  previous_profit: string;
  delta: string;
};
const format = (value: unknown, precision = 2) =>
  value === null || value === undefined ? "—" : money(String(value), precision);

export function MarginResult({
  result,
  step,
  busy,
  onRun,
}: {
  result: AnalysisResult;
  step: AnalysisStep;
  busy: boolean;
  onRun: (step: AnalysisStep) => void;
}) {
  const summaries = result.totals.groups as Summary[];
  const periods = result.totals.periods as Record<
    string,
    { start: string; end_exclusive: string }
  >;
  const [basis, setBasis] = useState(summaries[0]?.basis || "previous");
  const [attention, setAttention] = useState(false);
  const [search, setSearch] = useState("");
  const summary = summaries.find((s) => s.basis === basis)!;
  const rows = result.rows.filter(
    (r) =>
      r.basis === basis &&
      (!attention || r.volume_up_profit_not_up === true) &&
      [r.name, r.code, r.specification].some((v) =>
        String(v).includes(search.trim()),
      ),
  );
  return (
    <article className="panel price-result" aria-label="销量毛利结果">
      <div className="price-result-heading">
        <h3>{result.title}</h3>
        <GrowthExport step={step} />
      </div>
      <p className="price-explanation">
        采购成本口径毛利 · 未扣退货、税额未拆分 · 不等同于净利润
      </p>
      {step.dimension === "buyer" && (
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => onRun({ ...step, dimension: "product" })}
        >
          返回该品种毛利
        </button>
      )}
      <div className="price-basis" role="group" aria-label="毛利比较结果切换">
        {summaries.map((s) => (
          <button
            key={s.basis}
            aria-pressed={basis === s.basis}
            onClick={() => {
              setBasis(s.basis);
              setAttention(false);
            }}
          >
            {s.basis === "previous" ? "较上期" : "较去年同期"}
          </button>
        ))}
      </div>
      <p className="analytics-hint">
        本期：{periods.current.start} 至{" "}
        {shiftDay(periods.current.end_exclusive, -1)} · 比较期：
        {periods[basis].start} 至 {shiftDay(periods[basis].end_exclusive, -1)} ·
        CNY
      </p>
      <div className="price-summary" aria-label="毛利概览">
        {[
          {
            label: "本期出库收入",
            value: summary.current_amount,
            icon: CircleDollarSign,
          },
          { label: "本期采购成本", value: summary.current_cost, icon: Package },
          {
            label: "本期毛利",
            value: summary.current_profit,
            icon: TrendingUp,
          },
          { label: "毛利变化", value: summary.delta, icon: TrendingUp },
        ].map(({ label, value, icon: Icon }) => (
          <div
            className={`price-stat ${value.startsWith("-") ? "decrease" : "increase"}`}
            key={label}
          >
            <span>
              <Icon size={18} aria-hidden />
              {label}
            </span>
            <strong className="margin-number">{format(value)}</strong>
            <span>
              {label === "毛利变化"
                ? `比较期毛利 ${format(summary.previous_profit)}`
                : "CNY"}
            </span>
          </div>
        ))}
      </div>
      <div className="price-toolbar">
        <label>
          <Search size={17} aria-hidden />
          <input
            aria-label="搜索毛利结果"
            placeholder="搜索已展示的品种或客户"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <button
          className="button secondary"
          aria-pressed={attention}
          onClick={() => setAttention(!attention)}
        >
          <AlertTriangle size={16} aria-hidden />
          {attention
            ? "显示全部明细"
            : `销量增加但毛利未增（${summary.attention}组）`}
        </button>
      </div>
      <p className="analytics-hint">
        概览覆盖全部 {summary.total} 组；已返回 {summary.shown} 组，当前筛选展示{" "}
        {rows.length} 组。各方向最多 {step.top_n ?? 10}{" "}
        组，完整明细可下载。表格可横向滚动。
      </p>
      {rows.length ? (
        <div className="table-scroll">
          <table className="price-table margin-table">
            <thead>
              <tr>
                <th>{step.dimension === "buyer" ? "客户" : "品种"} / 规格</th>
                <th>比较期 → 本期销量</th>
                <th>收入 / 采购成本</th>
                <th>比较期 → 本期毛利</th>
                <th>变化来自哪里</th>
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
                    {row.volume_up_profit_not_up === true && (
                      <span className="price-direction decrease">
                        销量增、毛利未增
                      </span>
                    )}
                  </td>
                  <td>
                    {format(row.previous_quantity, 4)} →{" "}
                    {format(row.current_quantity, 4)}
                    <small>{String(row.unit)}</small>
                  </td>
                  <td>
                    收入 {format(row.current_amount)}
                    <small>成本 {format(row.current_cost)}</small>
                  </td>
                  <td>
                    {format(row.previous_profit)} →{" "}
                    <strong>{format(row.current_profit)}</strong>
                    <small>变化 {format(row.delta, 4)}</small>
                    <small>
                      毛利率 {String(row.previous_margin_percent ?? "—")} →{" "}
                      {String(row.current_margin_percent ?? "—")}
                    </small>
                    <small>
                      {String(
                        row.change_percent ?? "比较期毛利非正，不计算增长率",
                      )}
                    </small>
                  </td>
                  <td>
                    <details>
                      <summary>查看三因素拆解</summary>
                      <p>
                        均价 {format(row.previous_price, 4)} →{" "}
                        {format(row.current_price, 4)}
                      </p>
                      <p>
                        单位成本 {format(row.previous_unit_cost, 4)} →{" "}
                        {format(row.current_unit_cost, 4)}
                      </p>
                      <p>销量影响 {format(row.volume_effect, 4)}</p>
                      <p>售价影响 {format(row.price_effect, 4)}</p>
                      <p>成本影响 {format(row.cost_effect, 4)}</p>
                      <p>舍入调整 {format(row.rounding_adjustment, 4)}</p>
                      <small>{String(row.record_state)}</small>
                    </details>
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
          当前已展示明细中没有匹配项。可清空搜索、切换关注筛选或下载完整明细。
        </p>
      )}
      <details className="price-definition">
        <summary>毛利口径与分解公式</summary>
        <ul>
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}
