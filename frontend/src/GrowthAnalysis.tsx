import { useEffect, useRef, useState } from "react";
import { Download, Users, FileSearch } from "lucide-react";
import {
  shiftDay,
  type AnalysisStep,
  type AnalysisResult,
} from "./analytics-api";

export function GrowthPanel({
  coverage,
  busy,
  onRun,
}: {
  coverage: { start: string; end_exclusive: string }[];
  busy: boolean;
  onRun: (step: AnalysisStep) => void;
}) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [metric, setMetric] = useState<"amount" | "quantity">("amount");
  const [basis, setBasis] = useState<"both" | "previous" | "year_over_year">(
    "both",
  );
  const [direction, setDirection] = useState<"both" | "increase" | "decrease">(
    "both",
  );
  const [sort, setSort] = useState<"delta" | "rate">("delta");
  useEffect(() => {
    const month = [...coverage]
      .sort((a, b) => b.start.localeCompare(a.start))
      .find(
        (p) =>
          p.start.endsWith("-01") &&
          p.end_exclusive.endsWith("-01") &&
          (Date.parse(p.end_exclusive) - Date.parse(p.start)) / 86400000 <= 31,
      );
    if (month) {
      setStart(month.start);
      setEnd(shiftDay(month.end_exclusive, -1));
    }
  }, [coverage]);
  return (
    <section className="panel analytics-input">
      <h2>品种变化与客户贡献</h2>
      <p>
        比较上期和去年同期，分别查看增长与下降。金额与数量独立计算；当前商品范围未筛药品类别。
      </p>
      {!coverage.length ? (
        <p>当前快照尚未加载可比较的历史出库数据，请先生成历史快照。</p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onRun({
              domain: "shipping",
              kind: "growth",
              start_date: start,
              end_date_exclusive: shiftDay(end, 1),
              metric,
              dimension: "product",
              growth_basis: basis,
              growth_direction: direction,
              growth_sort: sort,
              top_n: 10,
            });
          }}
        >
          <div className="growth-controls">
            <label>
              本期开始
              <input
                aria-label="品种比较开始日期"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                required
              />
            </label>
            <label>
              本期结束
              <input
                aria-label="品种比较结束日期"
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                required
              />
            </label>
            <label>
              指标
              <select
                aria-label="品种比较指标"
                value={metric}
                onChange={(e) => setMetric(e.target.value as typeof metric)}
              >
                <option value="amount">出库金额</option>
                <option value="quantity">出库数量（分单位）</option>
              </select>
            </label>
            <label>
              比较基准
              <select
                aria-label="品种比较基准"
                value={basis}
                onChange={(e) => setBasis(e.target.value as typeof basis)}
              >
                <option value="both">上期及去年同期</option>
                <option value="previous">上期</option>
                <option value="year_over_year">去年同期</option>
              </select>
            </label>
            <label>
              变化方向
              <select
                aria-label="品种变化方向"
                value={direction}
                onChange={(e) =>
                  setDirection(e.target.value as typeof direction)
                }
              >
                <option value="both">增长和下降</option>
                <option value="increase">增长</option>
                <option value="decrease">下降</option>
              </select>
            </label>
            <label>
              排序
              <select
                aria-label="品种变化排序"
                value={sort}
                onChange={(e) => setSort(e.target.value as typeof sort)}
              >
                <option value="delta">增减额／量</option>
                <option value="rate">变化率（排除零基数）</option>
              </select>
            </label>
          </div>
          <p>
            完整自然月比较上一自然月；其他区间比较紧邻等长区间。同比按去年相同月日。每期最多90天，实际覆盖由执行时核验。
          </p>
          <button
            className="button"
            disabled={busy || !start || !end || start > end}
          >
            {busy ? "正在比较…" : "比较品种变化"}
          </button>
        </form>
      )}
    </section>
  );
}

type Evidence = {
  total: number;
  offset: number;
  items: Record<string, string | number>[];
};

export function GrowthExport({ step }: { step: AnalysisStep }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/analysis/${step.kind}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(step),
      });
      const payload = await response.json();
      if (!response.ok)
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : "下载失败",
        );
      const url = URL.createObjectURL(
        new Blob([JSON.stringify({ step, result: payload }, null, 2)], {
          type: "application/json;charset=utf-8",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `${step.kind === "margin" ? "销量毛利" : step.kind === "price" ? "售价变化" : "品种变化"}完整明细-${step.start_date}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "下载失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        className="button secondary"
        disabled={busy}
        onClick={() => void download()}
      >
        <Download size={15} aria-hidden="true" />
        {busy ? "正在生成明细…" : "下载完整变化明细"}
      </button>
      {error && <p role="alert">{error}</p>}
    </>
  );
}

export function GrowthActions({
  row,
  step,
  busy,
  onRun,
}: {
  row: AnalysisResult["rows"][number];
  step: AnalysisStep;
  busy: boolean;
  onRun: (step: AnalysisStep) => void;
}) {
  const [opened, setOpened] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const [period, setPeriod] = useState("current");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!opened) return;
    dialog.current?.showModal();
    const controller = new AbortController();
    setData(null);
    setError("");
    fetch(`/api/v1/analysis/${step.kind}/evidence`, {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        step,
        period,
        offset,
        product_code: row.product_code,
        specification: row.specification,
        manufacturer: row.manufacturer,
        unit: row.unit,
        ...(step.dimension === "buyer" ? { buyer_code: row.code } : {}),
        limit: 50,
      }),
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok)
          throw new Error(
            typeof payload.detail === "string"
              ? payload.detail
              : "证据请求失败",
          );
        return payload as Evidence;
      })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [opened, period, offset, step, row]);
  return (
    <>
      {step.dimension !== "buyer" && (
        <button
          className="analytics-evidence"
          disabled={busy}
          onClick={() =>
            onRun({
              ...step,
              dimension: "buyer",
              filters: [
                ...(step.filters || []).filter(
                  (f) => f.field !== "product" || f.operator === "exclude",
                ),
                {
                  field: "product",
                  operator: "equal",
                  code: String(row.product_code),
                  variant: {
                    specification: String(row.specification),
                    manufacturer: String(row.manufacturer),
                    unit: String(row.unit),
                  },
                },
              ],
            })
          }
        >
          <Users size={15} aria-hidden="true" />
          {step.kind === "margin"
            ? "查看客户毛利"
            : step.kind === "price"
              ? "查看客户售价"
              : "查看客户贡献"}
        </button>
      )}
      <button className="analytics-evidence" onClick={() => setOpened(true)}>
        <FileSearch size={15} aria-hidden="true" />
        查看出库证据
      </button>
      {opened && (
        <dialog
          ref={dialog}
          className="analytics-dialog"
          aria-label="出库证据"
          onCancel={() => setOpened(false)}
        >
          <div className="panel growth-evidence">
            <button
              className="button secondary"
              onClick={() => setOpened(false)}
            >
              关闭证据
            </button>
            <h3>{String(row.name)} · 出库证据</h3>
            <label>
              证据期间
              <select
                aria-label="证据期间"
                value={period}
                onChange={(e) => {
                  setPeriod(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="current">本期</option>
                {step.growth_basis !== "year_over_year" && (
                  <option value="previous">上期</option>
                )}
                {step.growth_basis !== "previous" && (
                  <option value="year_over_year">去年同期</option>
                )}
              </select>
            </label>
            {error && <p role="alert">{error}</p>}
            {!data && !error && <p>正在读取…</p>}
            {data && (
              <>
                <p>
                  共{data.total}条，当前显示第{data.total ? offset + 1 : 0}至
                  {offset + data.items.length}条
                </p>
                <div className="analytics-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        {[
                          "单据",
                          "日期",
                          "客户",
                          "数量",
                          "单位",
                          "金额",
                          ...(step.kind === "growth" ? [] : ["保存的采购单价"]),
                          "来源",
                        ].map((label) => (
                          <th key={label}>{label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item) => (
                        <tr key={String(item.source)}>
                          {[
                            "document_number",
                            "occurred_at",
                            "buyer_name",
                            "quantity",
                            "unit",
                            "amount",
                            ...(step.kind === "growth"
                              ? []
                              : ["purchase_unit_cost"]),
                            "source",
                          ].map((key) => (
                            <td key={key}>{String(item[key] ?? "")}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  上一页
                </button>
                <button
                  disabled={offset + data.items.length >= data.total}
                  onClick={() => setOffset(offset + 50)}
                >
                  下一页
                </button>
              </>
            )}
          </div>
        </dialog>
      )}
    </>
  );
}
