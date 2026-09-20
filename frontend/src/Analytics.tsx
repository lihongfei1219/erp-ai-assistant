import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowDownToLine, BarChart3, Search, Sparkles, X } from "lucide-react";
import { api, ApiError, money, timestamp, type Order } from "./api";
import {
  loadPlotly,
  shiftDay,
  type Figure,
  type AnalysisCatalog,
  type AnalysisKind,
  type AnalysisPlan,
  type AnalysisResponse,
  type AnalysisStep,
} from "./analytics-api";
import "./analytics.css";

export function Analytics({
  token,
  onUnauthorized,
}: {
  token: string;
  onUnauthorized: () => void;
}) {
  const [catalog, setCatalog] = useState<AnalysisCatalog | null>(null);
  const [question, setQuestion] = useState("");
  const [kind, setKind] = useState<AnalysisKind>("summary");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [beforeStart, setBeforeStart] = useState("");
  const [beforeEnd, setBeforeEnd] = useState("");
  const [metric, setMetric] = useState<"amount" | "orders">("amount");
  const [dimension, setDimension] = useState<"product" | "buyer">("product");
  const [top, setTop] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [previous, setPrevious] = useState<AnalysisPlan | null>(null);
  const [evidence, setEvidence] = useState<number[] | null>(null);
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | null>(null);
  const isRanking = kind === "buyer_ranking" || kind === "product_ranking";

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<AnalysisCatalog>("/analysis/catalog", token, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setCatalog(data);
        setStart(data.available_start);
        if (data.available_end_exclusive > data.available_start) {
          const limitedEnd = shiftDay(data.available_start, 90);
          setEnd(
            shiftDay(
              data.available_end_exclusive < limitedEnd
                ? data.available_end_exclusive
                : limitedEnd,
              -1,
            ),
          );
        }
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) onUnauthorized();
        else
          setError(
            cause instanceof Error ? cause.message : "无法读取可用分析范围",
          );
      });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [token, reload]);

  async function submit(path: "run" | "ask", body: unknown) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    setResult(null);
    setEvidence(null);
    try {
      const response = await fetch(`/api/v1/analysis/${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
        cache: "no-store",
      });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await response.json();
      if (!response.ok)
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "分析参数无效，请检查日期、比较区间和指标。",
        );
      if (!controller.signal.aborted) {
        setResult(payload);
        setPrevious(payload.plan);
      }
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : "分析未完成，请重试");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function manual(event: FormEvent) {
    event.preventDefault();
    const step: AnalysisStep = {
      kind,
      start_date: start,
      end_date_exclusive: shiftDay(end, 1),
      metric: isRanking || kind === "trend" ? metric : "amount",
      top_n: isRanking || kind === "comparison" ? top : 10,
    };
    if (kind === "comparison")
      Object.assign(step, {
        dimension,
        comparison_start_date: beforeStart,
        comparison_end_date_exclusive: shiftDay(beforeEnd, 1),
      });
    void submit("run", { steps: [step] });
  }

  function download() {
    if (!result) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `analysis-${result.run_id}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <div className="analytics-workspace">
      <section className="panel analytics-input">
        <div className="analytics-title">
          <Sparkles size={22} />
          <div>
            <h2>用问题开始分析</h2>
            <p>查询销售表现、拆解变化，沿结果查看订单依据。</p>
          </div>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit("ask", { question, previous_plan: previous });
          }}
        >
          <label htmlFor="analysis-question">分析问题</label>
          <textarea
            id="analysis-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="例如：分析2026年9月1日至5号的销售数据，哪些药品卖的好"
            required
            maxLength={1000}
            rows={3}
            disabled={busy}
          />
          <div className="analytics-actions">
            <button
              className="button"
              disabled={busy || !catalog?.model_enabled || !question.trim()}
            >
              <Sparkles size={16} />
              {busy ? "正在分析…" : "开始智能分析"}
            </button>
            {previous && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => setPrevious(null)}
              >
                清除追问上下文
              </button>
            )}
            <small>
              {catalog?.model_enabled
                ? previous
                  ? "可继续追问：换成按订单数排，取前5名；或再看客户排行。"
                  : "直接说日期和想了解的内容；“卖得好”默认按销售金额展示前10名。"
                : "自然语言暂不可用，仍可使用下方手动分析。"}
            </small>
          </div>
        </form>
      </section>
      <section className="panel analytics-input">
        <div className="analytics-title">
          <BarChart3 size={21} />
          <div>
            <h2>手动分析</h2>
            <p>
              {catalog &&
              catalog.available_end_exclusive > catalog.available_start
                ? `完整数据：${catalog.available_start} 至 ${shiftDay(catalog.available_end_exclusive, -1)}；单次最多 90 天。`
                : "暂无完整日期可供分析。"}
            </p>
          </div>
        </div>
        <form onSubmit={manual}>
          <fieldset
            disabled={busy || !catalog || !end}
            className="analytics-fields"
          >
            <label>
              分析类型
              <select
                value={kind}
                onChange={(event) =>
                  setKind(event.target.value as AnalysisKind)
                }
              >
                {catalog &&
                  Object.entries(catalog.analyses).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              开始日期
              <input
                type="date"
                required
                value={start}
                min={catalog?.available_start}
                max={end}
                onChange={(event) => setStart(event.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                required
                value={end}
                min={start}
                max={
                  catalog
                    ? shiftDay(catalog.available_end_exclusive, -1)
                    : undefined
                }
                onChange={(event) => setEnd(event.target.value)}
              />
            </label>
            {(isRanking || kind === "trend") && (
              <label>
                统计指标
                <select
                  value={metric}
                  onChange={(event) =>
                    setMetric(event.target.value as "amount" | "orders")
                  }
                >
                  <option value="amount">订单金额</option>
                  <option value="orders">订单数</option>
                </select>
              </label>
            )}
            {(isRanking || kind === "comparison") && (
              <label>
                展示条数
                <input
                  type="number"
                  required
                  min={1}
                  max={50}
                  value={top}
                  onChange={(event) => setTop(Number(event.target.value))}
                />
              </label>
            )}
            {kind === "comparison" && (
              <>
                <label>
                  比较开始日期
                  <input
                    type="date"
                    required
                    value={beforeStart}
                    min={catalog?.available_start}
                    onChange={(event) => setBeforeStart(event.target.value)}
                  />
                </label>
                <label>
                  比较结束日期
                  <input
                    type="date"
                    required
                    value={beforeEnd}
                    min={beforeStart || catalog?.available_start}
                    max={
                      catalog
                        ? shiftDay(catalog.available_end_exclusive, -1)
                        : undefined
                    }
                    onChange={(event) => setBeforeEnd(event.target.value)}
                  />
                </label>
                <label>
                  贡献维度
                  <select
                    value={dimension}
                    onChange={(event) =>
                      setDimension(event.target.value as "product" | "buyer")
                    }
                  >
                    <option value="product">商品</option>
                    <option value="buyer">客户</option>
                  </select>
                </label>
              </>
            )}
            <button className="button" type="submit">
              <Search size={16} />
              {busy ? "正在计算…" : "运行分析"}
            </button>
          </fieldset>
        </form>
        {kind === "comparison" && (
          <p className="analytics-hint">
            两个期间必须天数相同且不重叠。变化贡献用于定位线索，不代表因果。
          </p>
        )}
      </section>
      {error && (
        <div className="analytics-error" role="alert">
          {error}
          {!catalog && (
            <button
              className="button secondary"
              onClick={() => setReload((n) => n + 1)}
            >
              重新加载分析目录
            </button>
          )}
        </div>
      )}
      {busy && <p role="status">正在校验分析范围并计算结果…</p>}
      {result && (
        <section data-testid="analysis-results" className="analytics-results">
          <div className="analytics-actions">
            <h2>分析结果</h2>
            <button className="button secondary" onClick={download}>
              <ArrowDownToLine size={16} />
              下载分析结果
            </button>
          </div>
          <p className="analytics-hint">
            {result.provenance.source_kind === "synthetic"
              ? "合成样例"
              : "备份快照"}{" "}
            · 数据截至{" "}
            {timestamp(
              result.provenance.source_as_of,
              result.provenance.business_timezone,
            )}{" "}
            ·{" "}
            {result.provenance.scope.all_buyers
              ? "全平台采购企业"
              : "当前授权采购企业范围"}
          </p>
          {!!result.interpretation?.length && (
            <div
              className="analytics-understanding"
              role="note"
              aria-label="本次问题理解"
            >
              <strong>本次问题理解</strong>
              {result.interpretation.map((text, index) => (
                <p key={index}>{text}</p>
              ))}
              <small>可在上方继续补充或调整分析要求。</small>
            </div>
          )}
          {result.results.map((item, index) => (
            <article
              className="panel analytics-result"
              key={`${result.run_id}-${index}`}
            >
              <h3>{item.title}</h3>
              <p className="analytics-hint">
                {result.plan.steps[index].start_date} 至{" "}
                {shiftDay(result.plan.steps[index].end_date_exclusive, -1)} ·{" "}
                {result.provenance.currency}
              </p>
              {result.plan.steps[index].comparison_start_date && (
                <p className="analytics-hint">
                  比较期：{result.plan.steps[index].comparison_start_date} 至{" "}
                  {shiftDay(
                    result.plan.steps[index].comparison_end_date_exclusive!,
                    -1,
                  )}
                </p>
              )}
              <ul className="analytics-findings">
                {item.findings.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
              {item.kind === "comparison" && (
                <p>
                  变化率：
                  {item.totals.change_rate === null
                    ? "无定义（比较期金额为零）"
                    : `${(Number(item.totals.change_rate) * 100).toFixed(2)}%`}
                  ；其余维度变化金额：{String(item.totals.other_delta)}
                </p>
              )}
              {item.chart && <AnalysisChart figure={item.chart} />}
              {item.rows.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        {Object.entries(item.columns).map(([key, label]) => (
                          <th key={key}>{label}</th>
                        ))}
                        <th>依据</th>
                      </tr>
                    </thead>
                    <tbody>
                      {item.rows.map((row, ri) => (
                        <tr key={ri}>
                          {Object.keys(item.columns).map((key) => (
                            <td key={key}>
                              {row[key] === null
                                ? "—"
                                : String(row[key] ?? "—")}
                            </td>
                          ))}
                          <td>
                            {Array.isArray(row.evidence_ids) &&
                            row.evidence_ids.length > 0 ? (
                              <button
                                className="analytics-evidence"
                                onClick={() =>
                                  setEvidence(row.evidence_ids as number[])
                                }
                              >
                                查看证据
                              </button>
                            ) : (
                              "无订单"
                            )}
                            <small>
                              {Number(row.evidence_count) > 100
                                ? `前100 / 共${row.evidence_count}张`
                                : ""}
                            </small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p>没有满足条件的记录。</p>
              )}
              <div className="analytics-hint">
                {item.notes.map((note, i) => (
                  <p key={i}>{note}</p>
                ))}
              </div>
            </article>
          ))}
          <details className="panel analytics-input">
            <summary>统计口径与追溯信息</summary>
            <p>
              纳入状态：{result.provenance.included_statuses.join("、")}
              ；规则版本：{result.provenance.policy_id}
            </p>
            <p>
              指标版本：{result.provenance.metric_version}；业务时区：
              {result.provenance.business_timezone}
            </p>
            <p>
              分析编号：{result.run_id}；规则指纹：
              {result.provenance.policy_fingerprint}
            </p>
            {result.warnings.map((text, i) => (
              <p key={i}>{text}</p>
            ))}
          </details>
        </section>
      )}
      {catalog && (
        <details className="panel analytics-input">
          <summary>支持的指标与定义</summary>
          {catalog.metrics.map((item) => (
            <p key={item.id}>
              <strong>{item.label}：</strong>
              {item.definition}
            </p>
          ))}
        </details>
      )}
      {evidence && (
        <AnalysisEvidence
          ids={evidence}
          token={token}
          onUnauthorized={onUnauthorized}
          onClose={() => setEvidence(null)}
        />
      )}
    </div>
  );
}

function AnalysisChart({ figure }: { figure: Figure }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = host.current!;
    let active = true;
    let observer: ResizeObserver | undefined;
    loadPlotly()
      .then(async (plotly) => {
        if (!active) return;
        await plotly.newPlot(element, figure.data, figure.layout, {
          responsive: true,
          displaylogo: false,
          toImageButtonOptions: { filename: "sales-analysis" },
        });
        if (!active) {
          plotly.purge(element);
          return;
        }
        observer = new ResizeObserver(() => plotly.Plots.resize(element));
        observer.observe(element);
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "图表暂不可用，可查看数据表",
          );
      });
    return () => {
      active = false;
      observer?.disconnect();
      window.Plotly?.purge(element);
    };
  }, [figure]);
  return (
    <>
      <div ref={host} className="analytics-chart" aria-label="分析图表" />
      {error && <p>{error}</p>}
    </>
  );
}

function AnalysisEvidence({
  ids,
  token,
  onClose,
  onUnauthorized,
}: {
  ids: number[];
  token: string;
  onClose: () => void;
  onUnauthorized: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [id, setId] = useState(ids[0]);
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setOrder(null);
    setError("");
    api<{ item: Order }>(`/orders/${id}`, token, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setOrder(data.item);
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) onUnauthorized();
        else setError("无法读取订单依据，请重试。");
      });
    return () => controller.abort();
  }, [id, token]);
  return (
    <dialog ref={dialog} className="analytics-dialog" onCancel={onClose}>
      <div className="analytics-actions">
        <h2>订单依据</h2>
        <button aria-label="关闭分析证据" onClick={onClose}>
          <X />
        </button>
      </div>
      <label>
        选择订单
        <select
          value={id}
          onChange={(event) => setId(Number(event.target.value))}
        >
          {ids.map((value) => (
            <option value={value} key={value}>
              订单 ID {value}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      {!order && !error && <p>正在读取订单…</p>}
      {order && (
        <>
          <h3>{order.order_number}</h3>
          <p>
            {order.buyer_name || order.buyer_code} · {order.status} ·{" "}
            {money(order.amount, 4)}
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>商品</th>
                  <th>原始行金额</th>
                </tr>
              </thead>
              <tbody>
                {order.lines.map((line) => (
                  <tr key={line.line_id}>
                    <td>{line.product_name || line.product_code}</td>
                    <td>{money(line.amount, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </dialog>
  );
}
