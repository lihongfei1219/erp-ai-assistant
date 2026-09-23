import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowDownToLine, BarChart3, Search, Sparkles, X } from "lucide-react";
import { api, money, timestamp, type Order } from "./api";
import {
  loadPlotly,
  shiftDay,
  type Figure,
  type AnalysisCatalog,
  type AnalysisKind,
  type AnalysisResponse,
  type AnalysisStep,
  type DialogueChoice,
  type DialogueIntent,
  type DialogueRequest,
  type DialogueTurn,
} from "./analytics-api";
import "./analytics.css";

export function Analytics() {
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
  const [conversationToken, setConversationToken] = useState<string | null>(
    null,
  );
  const [dialogue, setDialogue] = useState<DialogueTurn | null>(null);
  const [expired, setExpired] = useState("");
  const [retry, setRetry] = useState<{
    path: "run" | "converse";
    body: unknown;
  } | null>(null);
  const [evidence, setEvidence] = useState<{
    ids: number[];
    step: AnalysisStep;
    product_code?: string;
    buyer_code?: string;
  } | null>(null);
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | null>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const expiredReply = useRef("");
  const isRanking = kind === "buyer_ranking" || kind === "product_ranking";

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setConversationToken(null);
    setDialogue(null);
    setExpired("");
    setRetry(null);
    setResult(null);
    setBusy(false);
    api<AnalysisCatalog>("/analysis/catalog", controller.signal)
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
        setError(
          cause instanceof Error ? cause.message : "无法读取可用分析范围",
        );
      });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [reload]);

  async function submit(path: "run" | "converse", body: unknown) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    setRetry(null);
    setResult(null);
    setEvidence(null);
    try {
      const response = await fetch(`/api/v1/analysis/${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
        cache: "no-store",
      });
      if (controller.signal.aborted) return;
      const payload = await response.json();
      if (controller.signal.aborted) return;
      if (!response.ok) {
        if (
          response.status === 409 &&
          payload.detail?.code === "context_expired"
        ) {
          setExpired(
            payload.detail.message || "会话已过期，可以确认后恢复当前草稿。",
          );
          setConversationToken(null);
          const attempted = body as DialogueRequest;
          const choice = dialogue?.choices.find(
            (item) => item.id === attempted.choice_id,
          );
          const target = dialogue?.draft.intents.find(
            (item) => item.id === choice?.action.intent_id,
          );
          expiredReply.current =
            attempted.question ||
            (choice
              ? `${target ? `对于“${target.label}”，` : ""}${choice.label}${attempted.date_range ? `：${attempted.date_range.start} 至 ${shiftDay(attempted.date_range.end_exclusive, -1)}（含首尾两天）` : ""}`
              : "");
          return;
        }
        if (
          response.status === 503 ||
          payload.detail?.code === "request_failed"
        ) {
          setRetry({
            path,
            body:
              path === "converse"
                ? {
                    ...(body as DialogueRequest),
                    request_id: crypto.randomUUID(),
                  }
                : body,
          });
        } else if (
          response.status >= 500 ||
          payload.detail?.code === "request_busy"
        ) {
          setRetry({ path, body });
        }
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : typeof payload.detail?.message === "string"
              ? payload.detail.message
              : "分析参数无效，请检查日期、比较区间和指标。",
        );
      }
      if (!controller.signal.aborted) {
        if (path === "converse") {
          const turn = payload as DialogueTurn;
          setDialogue(turn);
          setResult(turn.result);
          setConversationToken(turn.conversation_token);
          setExpired("");
        } else {
          setResult(payload);
          setConversationToken(null);
          setDialogue(null);
          setExpired("");
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "分析未完成，请重试");
        if (cause instanceof TypeError) setRetry({ path, body });
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function converse(body: Omit<DialogueRequest, "conversation_token">) {
    void submit("converse", {
      ...body,
      request_id: crypto.randomUUID(),
      conversation_token: conversationToken,
    });
  }

  function clearConversation() {
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setDialogue(null);
    setConversationToken(null);
    setExpired("");
    setError("");
    setRetry(null);
    setQuestion("");
  }

  function editCondition(text: string) {
    setQuestion(text);
    questionInput.current?.focus();
    questionInput.current?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
  }

  function restoreDraft() {
    if (!dialogue) return;
    const draft = [
      dialogue.understood_summary,
      ...dialogue.draft.intents.map(
        (intent, index) =>
          `第${index + 1}个目标：${intent.label}；${Object.entries(
            intent.fields,
          )
            .filter(([, value]) => value !== null)
            .map(
              ([field, value]) =>
                `${fieldLabel(field)}：${fieldValue(field, value!, intent.fields.domain)}`,
            )
            .join(
              "；",
            )}${intent.constraints.map((item) => `；${item.label}`).join("")}`,
      ),
      expiredReply.current ? `继续补充：${expiredReply.current}` : "",
    ]
      .filter(Boolean)
      .join("\n");
    if (draft.length > 1000) {
      setQuestion(draft);
      setExpired("草稿内容较长，请在输入框精简至 1000 字以内，再重新提交。");
      questionInput.current?.focus();
      return;
    }
    void submit("converse", { question: draft, conversation_token: null });
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
            <p>
              直接说你想了解的业务，助手会记住已明确的条件，逐步帮你补齐问题。
            </p>
          </div>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (question.trim() && question.length <= 1000)
              converse({ question: question.trim() });
          }}
        >
          <label htmlFor="analysis-question">分析问题</label>
          <textarea
            id="analysis-question"
            ref={questionInput}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              setRetry(null);
            }}
            placeholder={
              dialogue
                ? "继续补充、修改条件，或提出新的问题……"
                : "用自己的话说说想了解什么，不需要固定问法……"
            }
            required
            maxLength={1000}
            rows={3}
            disabled={busy}
          />
          <div className="analytics-actions">
            <button
              className="button"
              disabled={
                busy ||
                !catalog?.model_enabled ||
                !question.trim() ||
                question.length > 1000
              }
            >
              <Sparkles size={16} />
              {busy ? "正在分析…" : "开始智能分析"}
            </button>
            {(dialogue || conversationToken) && (
              <button
                type="button"
                className="button secondary"
                onClick={clearConversation}
              >
                清除追问上下文
              </button>
            )}
            <small>
              {catalog?.model_enabled
                ? dialogue || conversationToken
                  ? "可直接补充日期或指标，也可继续追问；上下文保留30分钟。"
                  : `支持自由提问；当前快照可分析：${Object.values(
                      catalog.semantic_domains || {
                        sales: { label: "销售", executable: true },
                      },
                    )
                      .filter((d) => d.executable)
                      .map((d) => d.label)
                      .join("、")}。`
                : "自然语言暂不可用，仍可使用下方手动分析。"}
            </small>
          </div>
        </form>
        {dialogue && (
          <DialogueGuide
            turn={dialogue}
            busy={busy}
            expired={expired}
            pendingReply={expiredReply.current}
            onChoice={converse}
            onEdit={editCondition}
            onRestore={restoreDraft}
          />
        )}
        {expired && !dialogue && (
          <p className="analytics-hint">{expired}请重新说明想了解的内容。</p>
        )}
      </section>
      <section className="panel analytics-input">
        <div className="analytics-title">
          <BarChart3 size={21} />
          <div>
            <h2>手动分析</h2>
            <p>
              {catalog &&
              catalog.available_end_exclusive > catalog.available_start
                ? `销售完整数据：${catalog.available_start} 至 ${shiftDay(catalog.available_end_exclusive, -1)}；单次最多 90 天。`
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
          {retry && (
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void submit(retry.path, retry.body)}
            >
              重试本次请求
            </button>
          )}
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
                            {(!item.domain || item.domain === "sales") &&
                            Array.isArray(row.evidence_ids) &&
                            row.evidence_ids.length > 0 ? (
                              <button
                                className="analytics-evidence"
                                onClick={() =>
                                  setEvidence({
                                    ids: row.evidence_ids as number[],
                                    step: result.plan.steps[index],
                                    ...(item.kind === "product_ranking" ||
                                    (item.kind === "comparison" &&
                                      result.plan.steps[index].dimension !==
                                        "buyer")
                                      ? { product_code: String(row.code) }
                                      : {}),
                                    ...(item.kind === "buyer_ranking" ||
                                    (item.kind === "comparison" &&
                                      result.plan.steps[index].dimension ===
                                        "buyer")
                                      ? { buyer_code: String(row.code) }
                                      : {}),
                                  })
                                }
                              >
                                查看证据
                              </button>
                            ) : item.domain && item.domain !== "sales" ? (
                              "见来源记录与业务单号"
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
              销售纳入状态：{result.provenance.included_statuses.join("、")}
              ；规则版本：{result.provenance.policy_id}
            </p>
            <p>退货、出库、库存的日期、状态和来源以各项结果说明为准。</p>
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
        <AnalysisEvidence {...evidence} onClose={() => setEvidence(null)} />
      )}
    </div>
  );
}

const fieldLabels: Record<string, string> = {
  domain: "业务",
  operation: "分析",
  target: "对象",
  metric: "指标",
  time: "日期",
  comparison_time: "比较日期",
  limit: "展示数量",
  order: "排序",
  scope: "范围",
  generic_product_scope: "商品范围",
};
const fieldValues: Record<string, Record<string, string>> = {
  domain: {
    sales: "销售",
    returns: "退货",
    shipping: "出库",
    inventory: "库存",
    unknown: "待明确",
  },
  operation: {
    summary: "概览",
    trend: "趋势",
    ranking: "排行",
    comparison: "期间比较",
    anomalies: "波动线索",
    list: "明细",
    existence: "是否发生",
    unknown: "待明确",
  },
  target: {
    product: "商品",
    buyer: "客户",
    region: "地区",
    category: "类别",
    warehouse: "仓库",
    supplier: "供应商",
  },
  metric: {
    amount: "销售金额",
    orders: "订单数",
    quantity: "数量",
    return_rate: "退货率",
    stock: "库存",
    turnover: "周转率",
    payment: "收款",
    profit: "利润",
    unknown: "待明确",
  },
  order: { descending: "从高到低", ascending: "从低到高" },
  scope: { authorized: "当前授权范围", all_buyers: "全平台客户" },
};
function fieldLabel(field: string) {
  return fieldLabels[field] || field;
}
function fieldValue(
  field: string,
  value: string | number | boolean,
  domain?: string | number | boolean | null,
) {
  if (field === "metric" && (domain === "returns" || domain === "shipping")) {
    if (value === "amount") return `${fieldValues.domain[domain]}单据金额`;
    if (value === "orders") return `${fieldValues.domain[domain]}单据数`;
  }
  if (field === "generic_product_scope")
    return value ? "当前商品范围，未按药品类别筛选" : "按当前请求条件";
  return fieldValues[field]?.[String(value)] || String(value);
}

function DialogueGuide({
  turn,
  busy,
  expired,
  pendingReply,
  onChoice,
  onEdit,
  onRestore,
}: {
  turn: DialogueTurn;
  busy: boolean;
  expired: string;
  pendingReply: string;
  onChoice: (body: Omit<DialogueRequest, "conversation_token">) => void;
  onEdit: (text: string) => void;
  onRestore: () => void;
}) {
  const [dateChoice, setDateChoice] = useState<DialogueChoice | null>(null);
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const canChoose = !busy && !expired && !!turn.conversation_token;
  const covered =
    turn.available_dates.end_exclusive > turn.available_dates.start;
  const sourceLabels = {
    explicit: "已明确",
    inherited: "沿用",
    default: "默认",
  };
  useEffect(() => {
    setDateChoice(null);
  }, [turn]);

  function select(choice: DialogueChoice) {
    if (choice.action.kind === "date_range") {
      setDateChoice(choice);
      setDateStart("");
      setDateEnd("");
    } else onChoice({ choice_id: choice.id });
  }

  function edit(intent: DialogueIntent, index: number, detail: string) {
    onEdit(`修改第${index + 1}个目标“${intent.label}”的${detail}：`);
  }

  return (
    <section className="analytics-guide" aria-label="分析引导" aria-busy={busy}>
      <div className="analytics-guide-summary" aria-live="polite">
        <strong>已理解的需求</strong>
        <p>{turn.understood_summary}</p>
      </div>
      <div className="analytics-draft">
        {turn.draft.intents.map((intent, index) => (
          <div key={intent.id} className="analytics-draft-intent">
            <h3>
              {turn.draft.intents.length > 1 ? `${index + 1}. ` : ""}
              {intent.label}
            </h3>
            <div className="analytics-condition-list">
              {Object.entries(intent.fields)
                .filter(([, value]) => value !== null)
                .map(([field, value]) => (
                  <button
                    type="button"
                    className="analytics-condition"
                    key={field}
                    disabled={busy || !!expired}
                    aria-label={`修改${intent.label}的${fieldLabel(field)}`}
                    onClick={() => edit(intent, index, fieldLabel(field))}
                  >
                    <span>
                      {fieldLabel(field)}：
                      {fieldValue(field, value!, intent.fields.domain)}
                    </span>
                    {intent.field_sources[field] && (
                      <small>{sourceLabels[intent.field_sources[field]]}</small>
                    )}
                  </button>
                ))}
              {intent.constraints.map((condition) => (
                <button
                  type="button"
                  className="analytics-condition"
                  key={condition.id}
                  disabled={busy || !!expired}
                  aria-label={`修改条件：${condition.label}`}
                  onClick={() =>
                    edit(intent, index, `条件“${condition.label}”`)
                  }
                >
                  {condition.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {turn.applied_defaults.length > 0 && (
        <div className="analytics-guide-defaults">
          {turn.applied_defaults.map((item, index) => (
            <p key={index}>{item}</p>
          ))}
        </div>
      )}
      {expired ? (
        <div className="analytics-guide-question">
          <p>{expired}</p>
          {pendingReply && (
            <p className="analytics-hint">本次补充：{pendingReply}</p>
          )}
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onRestore}
          >
            恢复这份草稿
          </button>
          <p className="analytics-hint">
            确认后会按上面展示的需求和你刚才的补充重新理解；也可以在输入框提出新问题。
          </p>
        </div>
      ) : (
        <>
          {turn.clarification && (
            <p className="analytics-guide-question">
              {turn.clarification.question}
            </p>
          )}
          {turn.repeated_clarification && (
            <p className="analytics-hint">
              已有条件已保留。你可以点选下面的建议，也可以点上方条件直接修改。
            </p>
          )}
          <div className="analytics-actions">
            {turn.choices.map((choice) => (
              <button
                type="button"
                className="button secondary"
                disabled={!canChoose}
                key={choice.id}
                onClick={() => select(choice)}
              >
                {choice.label}
              </button>
            ))}
          </div>
          {dateChoice && (
            <form
              className="analytics-guide-dates"
              onSubmit={(event) => {
                event.preventDefault();
                if (canChoose && dateStart && dateEnd && dateStart <= dateEnd)
                  onChoice({
                    choice_id: dateChoice.id,
                    date_range: {
                      start: dateStart,
                      end_exclusive: shiftDay(dateEnd, 1),
                    },
                  });
              }}
            >
              <label>
                引导开始日期
                <input
                  type="date"
                  required
                  disabled={!canChoose}
                  value={dateStart}
                  min={covered ? turn.available_dates.start : undefined}
                  max={
                    dateEnd ||
                    (covered
                      ? shiftDay(turn.available_dates.end_exclusive, -1)
                      : undefined)
                  }
                  onChange={(event) => setDateStart(event.target.value)}
                />
              </label>
              <label>
                引导结束日期
                <input
                  type="date"
                  required
                  disabled={!canChoose}
                  value={dateEnd}
                  min={
                    dateStart ||
                    (covered ? turn.available_dates.start : undefined)
                  }
                  max={
                    covered
                      ? shiftDay(turn.available_dates.end_exclusive, -1)
                      : undefined
                  }
                  onChange={(event) => setDateEnd(event.target.value)}
                />
              </label>
              <button
                className="button"
                disabled={!canChoose || !dateStart || !dateEnd}
              >
                应用日期
              </button>
              <button
                type="button"
                className="button secondary"
                onClick={() => setDateChoice(null)}
              >
                取消
              </button>
            </form>
          )}
          {turn.status !== "result" && (
            <p className="analytics-hint">
              可以选择建议，也可以在输入框用自己的话继续说。
            </p>
          )}
        </>
      )}
      {turn.status === "data_gap" && (
        <p className="analytics-hint">
          {covered
            ? `当前可选数据日期：${turn.available_dates.start} 至 ${shiftDay(turn.available_dates.end_exclusive, -1)}；库存的准确时点以说明为准。`
            : "当前暂无完整日期可供分析。"}
        </p>
      )}
    </section>
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
  step,
  product_code,
  buyer_code,
  onClose,
}: {
  ids: number[];
  step: AnalysisStep;
  product_code?: string;
  buyer_code?: string;
  onClose: () => void;
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
    const request = step.filters?.length
      ? fetch("/api/v1/analysis/evidence", {
          method: "POST",
          signal: controller.signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            step,
            order_id: id,
            product_code,
            buyer_code,
          }),
        }).then(async (response) => {
          if (!response.ok) throw new Error("无法读取匹配的订单明细");
          return response.json() as Promise<{ item: Order }>;
        })
      : api<{ item: Order }>(`/orders/${id}`, controller.signal);
    request
      .then((data) => {
        if (!controller.signal.aborted) setOrder(data.item);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setError("无法读取订单依据，请重试。");
      });
    return () => controller.abort();
  }, [id, step, product_code, buyer_code]);
  return (
    <dialog ref={dialog} className="analytics-dialog" onCancel={onClose}>
      <div className="analytics-actions">
        <h2>{step.filters?.length ? "订单依据（仅匹配明细）" : "订单依据"}</h2>
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
