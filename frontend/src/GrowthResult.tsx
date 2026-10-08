import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CalendarDays,
  ChevronRight,
  Info,
  Search,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import {
  shiftDay,
  type AnalysisResult,
  type AnalysisStep,
} from "./analytics-api";
import { GrowthActions, GrowthExport } from "./GrowthAnalysis";
import { money } from "./api";
import "./growth.css";

type Row = AnalysisResult["rows"][number];
type Group = {
  basis: string;
  unit: string;
  delta: string;
  groups: number;
  unchanged?: number;
  current_amount?: string;
  previous_amount?: string;
  current_quantity?: string;
  previous_quantity?: string;
  positive?: string;
  negative?: string;
  increase_other?: string;
  decrease_other?: string;
};
type Period = { start: string; end_exclusive: string };
const labels: Record<string, string> = {
  previous: "上期",
  year_over_year: "去年同期",
};
function number(value: unknown, signed = false) {
  const formatted = money(String(value ?? "0"));
  return `${signed && formatted !== "—" && formatted !== "0.00" && !formatted.startsWith("-") ? "+" : ""}${formatted}`;
}
function dateRange(period?: Period) {
  return period
    ? `${period.start} 至 ${shiftDay(period.end_exclusive, -1)}`
    : "—";
}

export function GrowthResult({
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
  const groups = result.totals.groups as Group[];
  const periods = result.totals.periods as Record<string, Period>;
  const bases = [...new Set(groups.map((group) => group.basis))];
  const [basis, setBasis] = useState(bases[0] || "previous");
  const [unit, setUnit] = useState(groups[0]?.unit || "");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const quantity = step.metric === "quantity";
  const metric = quantity ? "quantity" : "amount";
  const units = groups
    .filter((group) => group.basis === basis)
    .map((group) => group.unit);
  const activeUnit = units.includes(unit) ? unit : units[0] || "";
  const group = groups.find(
    (item) => item.basis === basis && item.unit === activeUnit,
  );
  const rows = result.rows.filter(
    (row) => row.basis === basis && (!quantity || row.unit === activeUnit),
  );
  const filtered = rows.filter((row) =>
    [row.name, row.code, row.specification, row.manufacturer].some((v) =>
      String(v ?? "")
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
    ),
  );
  const unitLabel = quantity ? activeUnit : "元";
  const detailId = useId();
  const detail = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected) {
      detail.current?.focus({ preventScroll: true });
      detail.current?.scrollIntoView({ block: "nearest" });
    }
  }, [selected]);
  function changeBasis(value: string) {
    setBasis(value);
    setSelected(null);
  }
  const current = group?.[`current_${metric}`];
  const previous = group?.[`previous_${metric}`];
  const tone =
    Number(group?.delta) < 0
      ? "down"
      : Number(group?.delta) > 0
        ? "up"
        : "neutral";
  return (
    <article className="panel analytics-result growth-result">
      <header className="growth-heading">
        <div className="growth-title">
          <span className="growth-title-icon">
            <BarChart3 size={23} aria-hidden="true" />
          </span>
          <div>
            <h3>{result.title}</h3>
            <p>
              从整体变化，找到{step.dimension === "buyer" ? "客户" : "品种"}贡献
            </p>
          </div>
        </div>
        <GrowthExport step={step} />
      </header>
      <div className="growth-period">
        <CalendarDays size={15} aria-hidden="true" />
        本期：{dateRange(periods.current)}
        <span>已确认销售出库 · {quantity ? "数量分单位" : "人民币"}</span>
      </div>
      <div className="growth-toolbar">
        <div className="growth-segment" role="group" aria-label="结果比较基准">
          {bases.map((key) => (
            <button
              key={key}
              aria-pressed={basis === key}
              onClick={() => changeBasis(key)}
            >
              比较{labels[key]}
            </button>
          ))}
        </div>
        {quantity && (
          <label className="growth-unit">
            数量单位
            <select
              aria-label="结果数量单位"
              value={activeUnit}
              onChange={(e) => {
                setUnit(e.target.value);
                setSelected(null);
              }}
            >
              {units.map((value) => (
                <option key={value} value={value}>
                  {value || "无记录"}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="growth-baseline">
          {labels[basis]}：{dateRange(periods[basis])}
        </span>
      </div>
      <div className="growth-metrics" aria-label="变化概览">
        <div className="growth-metric">
          <span>本期出库{quantity ? "数量" : "金额"}</span>
          <strong title={String(current ?? 0)}>
            {number(current)}
            <small>{unitLabel}</small>
          </strong>
          <p>当前筛选范围内的全部记录</p>
        </div>
        <div className="growth-metric">
          <span>
            {labels[basis]}出库{quantity ? "数量" : "金额"}
          </span>
          <strong title={String(previous ?? 0)}>
            {number(previous)}
            <small>{unitLabel}</small>
          </strong>
          <p>{dateRange(periods[basis])}</p>
        </div>
        <div className={`growth-metric growth-net ${tone}`}>
          <span>净变化 · 比较{labels[basis]}</span>
          <strong title={String(group?.delta ?? 0)}>
            {tone === "down" ? (
              <TrendingDown size={24} aria-hidden="true" />
            ) : (
              <TrendingUp size={24} aria-hidden="true" />
            )}
            {number(group?.delta, true)}
            <small>{unitLabel}</small>
          </strong>
          <p>
            增加 {number(group?.positive)} − 减少 {number(group?.negative)}
          </p>
        </div>
      </div>
      <div className="growth-rank-heading">
        <div>
          <h4>变化贡献榜</h4>
          <p>
            每个方向最多 {step.top_n || 10} 项 · 按
            {step.growth_sort === "rate" ? "变化率" : "增减额／量"}排序 ·
            点击条目查看详情
          </p>
        </div>
        <label className="growth-search">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="搜索当前榜单"
            placeholder="搜索当前榜单名称或编码"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      <div className="growth-ranks">
        {(["增长", "下降"] as const).map((direction) => {
          const members = filtered.filter((row) => row.direction === direction);
          const allDirection = rows.filter(
            (row) => row.direction === direction,
          );
          const max = Math.max(
            ...allDirection.map((row) => Math.abs(Number(row.delta))),
            0,
          );
          const up = direction === "增长";
          const disabledDirection =
            step.growth_direction === (up ? "decrease" : "increase");
          return (
            <section
              className={`growth-rank ${up ? "up" : "down"}`}
              key={direction}
              aria-label={`${direction}贡献榜`}
            >
              <div className="growth-rank-title">
                {up ? (
                  <ArrowUpRight size={20} aria-hidden="true" />
                ) : (
                  <ArrowDownRight size={20} aria-hidden="true" />
                )}
                <h4>{direction}贡献</h4>
                <span>{members.length} 项</span>
              </div>
              <div className="growth-rank-list">
                {members.map((row, index) => (
                  <button
                    className={`growth-rank-row ${selected === row ? "selected" : ""}`}
                    key={JSON.stringify([
                      row.code,
                      row.specification,
                      row.manufacturer,
                      row.unit,
                    ])}
                    aria-pressed={selected === row}
                    aria-controls={detailId}
                    onClick={() => setSelected(row)}
                  >
                    <span className="growth-rank-index">{index + 1}</span>
                    <span className="growth-rank-main">
                      <span className="growth-row-top">
                        <strong>{String(row.name)}</strong>
                        <b title={String(row.delta)}>
                          {number(row.delta, true)}
                          <small> {unitLabel}</small>
                        </b>
                      </span>
                      <span className="growth-row-meta">
                        <span>
                          {step.dimension === "buyer"
                            ? String(row.code)
                            : `${row.specification} · ${row.unit}`}
                        </span>
                        <span>{String(row.change_percent)}</span>
                      </span>
                      <span className="growth-bar-track" aria-hidden="true">
                        <span
                          style={{
                            width: `${max ? (Math.abs(Number(row.delta)) / max) * 100 : 0}%`,
                          }}
                        />
                      </span>
                    </span>
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                ))}
              </div>
              {!members.length && (
                <p className="growth-empty">
                  {disabledDirection
                    ? "本次查询未选择此方向"
                    : query
                      ? "当前榜单没有匹配项"
                      : "当前排序条件下没有记录"}
                </p>
              )}
              <p className="growth-rank-foot">
                未列入此榜的{direction}：
                {number(
                  up ? group?.increase_other : group?.decrease_other,
                  true,
                )}{" "}
                {unitLabel}
                {query && "（搜索仅过滤已返回榜单）"}
              </p>
            </section>
          );
        })}
      </div>
      <p className="growth-chart-note">
        条形长度表示增减{quantity ? "量" : "额"}
        ，各方向独立刻度。汇总包含榜单之外的变化；持平 {group?.unchanged ||
          0}{" "}
        组。
      </p>
      <section
        id={detailId}
        ref={detail}
        tabIndex={-1}
        className="growth-detail"
        aria-label="选中条目详情"
        aria-live="polite"
      >
        {selected ? (
          <>
            <div className="growth-detail-heading">
              <div>
                <h4>{String(selected.name)}</h4>
                <p>
                  {String(selected.code)} · {String(selected.specification)} ·{" "}
                  {String(selected.manufacturer)} · {String(selected.unit)}
                </p>
              </div>
              <button
                className="analytics-evidence"
                onClick={() => setSelected(null)}
              >
                收起详情
              </button>
            </div>
            <div className="growth-detail-values">
              <span>
                本期{" "}
                <b>
                  {number(selected[`current_${metric}`])} {unitLabel}
                </b>
              </span>
              <span>
                {labels[basis]}{" "}
                <b>
                  {number(selected[`previous_${metric}`])} {unitLabel}
                </b>
              </span>
              <span>
                变化率 <b>{String(selected.change_percent)}</b>
              </span>
              <span>
                {String(selected.contribution_basis)}{" "}
                <b>{String(selected.contribution_percent)}</b>
              </span>
            </div>
            <p>
              {String(selected.record_state)} · {String(selected.comparability)}
            </p>
            <div className="growth-detail-actions">
              <GrowthActions
                row={selected}
                step={step}
                busy={busy}
                onRun={onRun}
              />
            </div>
          </>
        ) : (
          <p>
            <Info size={17} aria-hidden="true" />
            点击上方品种或客户，查看贡献占比、可比性说明和出库证据。
            {step.dimension !== "buyer" && "还可继续查看该品种的客户贡献。"}
          </p>
        )}
      </section>
      <details className="growth-full">
        <summary>
          展开完整字段与精确数值（当前返回的 {result.rows.length} 项）
        </summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {Object.entries(result.columns).map(([key, label]) => (
                  <th key={key}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, index) => (
                <tr key={index}>
                  {Object.keys(result.columns).map((key) => (
                    <td key={key}>{String(row[key] ?? "—")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <div className="growth-footnote">
        <Info size={16} aria-hidden="true" />
        <span>
          已确认售出单，未扣退货；未按药品类别筛选。金额／数量展示两位，完整字段与下载保留原始精度。
        </span>
      </div>
      <details className="growth-full">
        <summary>统计口径与比较说明</summary>
        {result.findings.concat(result.notes).map((note, i) => (
          <p key={i}>{note}</p>
        ))}
      </details>
    </article>
  );
}
