import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Building2,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Database,
  FileText,
  Info,
  LayoutDashboard,
  Package,
  RefreshCw,
  Send,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Wallet,
  X,
} from "lucide-react";
import {
  api,
  money,
  timestamp,
  type Breakdown,
  type Daily,
  type Dashboard,
  type Order,
  type OrdersPage,
} from "./api";
import { FeishuDaily } from "./FeishuDaily";
import { Analytics } from "./Analytics";

type Page = "overview" | "orders" | "rules" | "feishu" | "analytics";
type View = "operating" | "all";
interface Loaded {
  dashboard: Dashboard;
  rawDaily: Daily[];
  rawBuyers: Breakdown[];
  rawProducts: Breakdown[];
}

function Brand() {
  return (
    <div className="brand">
      <span className="brand-icon">
        <span />
        <span />
        <span />
      </span>
      <span>
        ERP<span className="brand-light"> / INSIGHT</span>
        <small>平台经营分析助手</small>
      </span>
    </div>
  );
}

export function App() {
  const [page, setPage] = useState<Page>("overview");
  const [view, setView] = useState<View>("operating");
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    sessionStorage.removeItem("erp-access-token");
    const controller = new AbortController();
    setLoading(true);
    setLoaded(null);
    setError("");
    Promise.all([
      api<Dashboard>("/dashboard/operating", controller.signal),
      api<{ items: Daily[] }>("/sales/trends", controller.signal),
      api<{ items: Breakdown[] }>(
        "/sales/breakdown?dimension=buyer&limit=10",
        controller.signal,
      ),
      api<{ items: Breakdown[] }>(
        "/sales/breakdown?dimension=product&limit=10",
        controller.signal,
      ),
    ])
      .then(([dashboard, daily, buyers, products]) => {
        if (!controller.signal.aborted)
          setLoaded({
            dashboard,
            rawDaily: daily.items,
            rawBuyers: buyers.items,
            rawProducts: products.items,
          });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof Error ? cause.message : "无法读取分析结果，请重试。",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);

  const data = loaded?.dashboard;
  const nav = [
    { id: "overview" as const, label: "经营概览", icon: LayoutDashboard },
    { id: "analytics" as const, label: "AI 数据分析", icon: Search },
    { id: "orders" as const, label: "订单明细", icon: ClipboardList },
    { id: "rules" as const, label: "业务口径", icon: SlidersHorizontal },
    { id: "feishu" as const, label: "飞书日报", icon: Send },
  ];
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace">
          <span className="workspace-avatar">平</span>
          <div>
            平台运营空间<small>内部经营分析</small>
          </div>
          <ChevronRight size={15} />
        </div>
        <div className="nav-label">工作台</div>
        <nav aria-label="主导航">
          {nav.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={page === item.id ? "active" : ""}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={19} />
              {item.label}
              {page === item.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <Database size={18} />
          <strong>数据有来源，分析有依据</strong>
          <p>每项指标可追溯至原始订单，当前使用本地备份快照。</p>
          <span>
            <i /> 本地数据模式
          </span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            工作台 <ChevronRight size={13} />{" "}
            <strong>{nav.find((item) => item.id === page)?.label}</strong>
          </span>
          <div>
            <span className="snapshot-tag">
              <span />
              备份快照
            </span>
            <span className="avatar">运</span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">PLATFORM ANALYTICS</div>
              <h1>
                {page === "analytics"
                  ? "AI 数据分析"
                  : page === "overview"
                    ? "经营概览"
                    : page === "orders"
                      ? "订单明细"
                      : page === "feishu"
                        ? "飞书销售日报"
                        : "业务口径与假设"}
              </h1>
              <p>
                {page === "analytics"
                  ? "从问题到数据、图表与可核验的结论。"
                  : page === "overview"
                    ? "从平台订单出发，看清经营表现与成交结构。"
                    : page === "orders"
                      ? "沿着每一个数字，找到原始业务依据。"
                      : page === "feishu"
                        ? "让每天的销售情况，准时送达飞书群。"
                        : "先用清晰的默认规则推进，业务确认后再统一调整。"}
              </p>
            </div>
            <button
              className="button secondary"
              onClick={() => setAttempt((n) => n + 1)}
              disabled={loading}
            >
              <RefreshCw size={15} className={loading ? "spinning" : ""} />
              重新读取快照
            </button>
          </div>
          {loading && (
            <div className="loading-panel" role="status">
              <RefreshCw className="spinning" size={24} />
              <strong>正在读取分析快照</strong>
              <span>正在加载指标、口径和数据依据…</span>
            </div>
          )}
          {error && (
            <div className="error-panel" role="alert">
              <Info size={22} />
              <h2>暂时无法展示分析结果</h2>
              <p>{error}</p>
              <button
                className="button"
                onClick={() => setAttempt((n) => n + 1)}
              >
                重新尝试
              </button>
            </div>
          )}
          {data && loaded && (
            <>
              <div className="rangebar">
                <div>
                  <CalendarDays size={17} />
                  <strong>
                    {data.metadata.window.start} —{" "}
                    {previousDay(data.metadata.window.end)}
                  </strong>
                  <span className="divider" />
                  <span>
                    {data.metadata.scope.all_buyers
                      ? "全平台采购企业"
                      : `已授权 ${data.metadata.scope.buyer_codes.length} 家采购企业`}
                  </span>
                </div>
                <span>
                  数据截至{" "}
                  {timestamp(
                    data.metadata.source_as_of,
                    data.operating.policy.business_timezone,
                  )}
                </span>
              </div>
              <div className="notice">
                <Info size={17} />
                <span>
                  当前按暂定业务口径统计；{data.operating.policy.currency}
                  、订单状态可后续调整。数据为备份快照，当日可能不完整。
                </span>
                <button onClick={() => setPage("rules")}>
                  查看口径 <ArrowUpRight size={14} />
                </button>
              </div>
              {(page === "overview" || page === "orders") && (
                <div className="viewbar">
                  <div className="segmented" aria-label="订单统计口径">
                    <button
                      aria-pressed={view === "operating"}
                      className={view === "operating" ? "selected" : ""}
                      onClick={() => setView("operating")}
                    >
                      有效销售（暂定）
                    </button>
                    <button
                      aria-pressed={view === "all"}
                      className={view === "all" ? "selected" : ""}
                      onClick={() => setView("all")}
                    >
                      全部原始订单
                    </button>
                  </div>
                  <small>
                    {view === "operating"
                      ? `纳入：${data.operating.policy.included_statuses.join("、")}`
                      : "包含全部状态，不作有效销售筛选"}
                  </small>
                </div>
              )}
              {page === "overview" && (
                <Overview
                  loaded={loaded}
                  view={view}
                  onOrders={() => setPage("orders")}
                />
              )}
              {page === "orders" && (
                <Orders
                  key={`${view}-${attempt}`}
                  view={view}
                  currency={data.operating.policy.currency}
                />
              )}
              {page === "rules" && <Rules data={data} />}
              {page === "feishu" && <FeishuDaily key={attempt} />}
              {page === "analytics" && <Analytics key={attempt} />}
              <footer>
                <span>
                  <ShieldCheck size={13} /> 来源：ERP 销售订单 ·
                  金额保留原始精度
                </span>
                <span>
                  {data.operating.policy.policy_id} ·{" "}
                  {data.metadata.source_kind === "synthetic"
                    ? "合成样例"
                    : "备份数据"}
                </span>
              </footer>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function previousDay(iso: string) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function Overview({
  loaded,
  view,
  onOrders,
}: {
  loaded: Loaded;
  view: View;
  onOrders: () => void;
}) {
  const data = loaded.dashboard;
  const effective = view === "operating";
  const summary = effective ? data.operating.summary : data.raw_summary;
  const daily = effective ? data.operating.daily : loaded.rawDaily;
  const buyers = effective ? data.operating.buyers : loaded.rawBuyers;
  const products = effective ? data.operating.products : loaded.rawProducts;
  const currency = data.operating.policy.currency;
  const kpis = [
    {
      label: effective ? "有效订单金额" : "原始订单金额",
      value: money(summary.order_amount),
      unit: currency,
      note: "ERP 订单金额 · 非支付成交额",
      icon: Wallet,
    },
    {
      label: effective ? "有效销售订单" : "原始销售订单",
      value: summary.order_count.toLocaleString("zh-CN"),
      unit: "单",
      note: `${summary.line_count.toLocaleString("zh-CN")} 条商品明细`,
      icon: ClipboardList,
    },
    {
      label: "下单采购企业",
      value: summary.buyer_count.toLocaleString("zh-CN"),
      unit: "家",
      note: "按企业业务编码去重",
      icon: Building2,
    },
    {
      label: "平均订单金额",
      value: money(summary.average_order_amount),
      unit: currency,
      note: "当前范围金额 ÷ 订单数",
      icon: Package,
    },
  ];
  return (
    <>
      <div className="kpi-grid">
        {kpis.map((item, index) => (
          <section
            key={item.label}
            className={`kpi-card ${index === 0 ? "primary" : ""}`}
          >
            <div className="kpi-top">
              <span>{item.label}</span>
              <item.icon size={18} />
            </div>
            <div className="kpi-value" data-testid={`kpi-${index}`}>
              {item.value}
              <small>{item.unit}</small>
            </div>
            <div className="kpi-note">
              {index === 0 && <span className="tiny-tag">暂定口径</span>}
              {item.note}
            </div>
          </section>
        ))}
      </div>
      {summary.order_count === 0 && (
        <div className="empty-inline">
          <Info size={18} />
          当前统计范围内没有符合条件的订单。可以切换“全部原始订单”查看记录。
        </div>
      )}
      <div className="middle-grid">
        <section className="panel trend-panel">
          <div className="panel-heading">
            <div>
              <h2>销售订单趋势</h2>
              <p>按订单创建日期统计 · {currency}</p>
            </div>
            <span className="legend">
              <i />
              订单金额
            </span>
          </div>
          <Trend daily={daily} />
          <details className="daily-details">
            <summary>查看每日数值</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th className="numeric">订单金额</th>
                    <th className="numeric">订单数</th>
                    <th className="numeric">采购企业数</th>
                  </tr>
                </thead>
                <tbody>
                  {daily.map((day) => (
                    <tr key={day.day}>
                      <td>{day.day}</td>
                      <td className="numeric">{money(day.order_amount, 4)}</td>
                      <td className="numeric">{day.order_count}</td>
                      <td className="numeric">{day.buyer_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
        <section className="panel status-panel">
          <div className="panel-heading">
            <div>
              <h2>原始订单状态</h2>
              <p>包含全部 {data.raw_summary.order_count} 张订单</p>
            </div>
            <span className="icon-box">
              <ClipboardList size={17} />
            </span>
          </div>
          <div className="status-list">
            {data.statuses.map((state) => {
              const included = data.operating.policy.included_statuses.includes(
                state.status,
              );
              return (
                <div key={state.status} className="status-item">
                  <span className={`state-dot ${included ? "included" : ""}`} />
                  <span>{state.status}</span>
                  <strong>
                    {state.order_count}
                    <small> 单</small>
                  </strong>
                </div>
              );
            })}
          </div>
          <div className="reconcile-box">
            <ShieldCheck size={20} />
            <div>
              <strong>
                {data.quality.sql_control_totals_match
                  ? "主明细金额对账一致"
                  : "金额对账待检查"}
              </strong>
              <p>已核对 {data.quality.reconciled_orders} 张原始订单</p>
            </div>
          </div>
          <button className="text-button" onClick={onOrders}>
            查看订单依据 <ArrowUpRight size={15} />
          </button>
        </section>
      </div>
      <div className="rank-grid">
        <Ranking
          title="采购企业排行"
          subtitle="按当前口径订单金额 · TOP 10"
          items={buyers}
          currency={currency}
        />
        <Ranking
          title="商品销售排行"
          subtitle="按当前口径明细金额 · TOP 10"
          items={products}
          currency={currency}
        />
      </div>
      {data.operating.unknown_statuses.length > 0 && (
        <div className="notice" role="alert">
          <Info size={17} />
          发现未识别状态：
          {data.operating.unknown_statuses
            .map((item) => `${item.status}（${item.order_count} 单）`)
            .join("、")}
          ，已从有效销售中排除，请后续核定。
        </div>
      )}
    </>
  );
}

function Trend({ daily }: { daily: Daily[] }) {
  const width = 700,
    height = 164,
    left = 58,
    top = 20;
  const values = daily.map((day) => Number(day.order_amount));
  const maximum = Math.max(1, ...values);
  const minimum = Math.min(0, ...values);
  const range = maximum - minimum;
  const x = (index: number) =>
    left +
    (daily.length <= 1 ? width / 2 : (index * width) / (daily.length - 1));
  const y = (value: number) =>
    top + height - ((value - minimum) / range) * height;
  const points = values
    .map((value, index) => `${x(index)},${y(value)}`)
    .join(" ");
  const baseline = y(0);
  return (
    <svg
      className="trend-chart"
      viewBox="0 0 780 220"
      role="img"
      aria-label="每日销售订单金额趋势，精确数值可在下方展开查看"
    >
      <defs>
        <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#2c8478" stopOpacity=".18" />
          <stop offset="100%" stopColor="#2c8478" stopOpacity=".01" />
        </linearGradient>
      </defs>
      {[0, 1, 2, 3].map((index) => {
        const value = minimum + (range * index) / 3;
        return (
          <g key={index}>
            <line
              x1={left}
              y1={y(value)}
              x2={758}
              y2={y(value)}
              stroke="#e8eeec"
              strokeDasharray="3 5"
            />
            <text x={left - 12} y={y(value) + 4} textAnchor="end">
              {new Intl.NumberFormat("zh-CN", {
                notation: "compact",
                maximumFractionDigits: 1,
              }).format(value)}
            </text>
          </g>
        );
      })}
      {daily.length > 0 && (
        <>
          <polygon
            points={`${x(0)},${baseline} ${points} ${x(daily.length - 1)},${baseline}`}
            fill="url(#chart-fill)"
          />
          <polyline
            points={points}
            fill="none"
            stroke="#2c8478"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
          {daily.map((day, index) => (
            <circle
              key={day.day}
              cx={x(index)}
              cy={y(values[index])}
              r={3}
              fill="#fff"
              stroke="#2c8478"
              strokeWidth="2"
            >
              <title>
                {day.day}：{money(day.order_amount, 4)}
              </title>
            </circle>
          ))}
        </>
      )}
      {daily
        .filter(
          (_, index) =>
            index === 0 ||
            index === daily.length - 1 ||
            index % Math.max(1, Math.ceil(daily.length / 6)) === 0,
        )
        .map((day) => (
          <text
            key={day.day}
            x={x(daily.indexOf(day))}
            y={211}
            textAnchor="middle"
          >
            {day.day.slice(5).replace("-", "/")}
          </text>
        ))}
    </svg>
  );
}

function Ranking({
  title,
  subtitle,
  items,
  currency,
}: {
  title: string;
  subtitle: string;
  items: Breakdown[];
  currency: string;
}) {
  const max = Math.max(1, ...items.map((item) => Number(item.order_amount)));
  return (
    <section className="panel rank-panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <span className="small-label">{currency}</span>
      </div>
      {items.length === 0 ? (
        <p className="empty-text">当前范围暂无排行数据</p>
      ) : (
        <ol className="ranking">
          {items.map((item, index) => (
            <li key={item.code}>
              <span className={`rank-number ${index < 3 ? "top" : ""}`}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="rank-content">
                <div>
                  <strong title={item.name || item.code}>
                    {item.name || item.code}
                  </strong>
                  <span>{money(item.order_amount)}</span>
                </div>
                <div className="rank-track">
                  <i
                    style={{
                      width: `${Math.max(0, (Number(item.order_amount) / max) * 100)}%`,
                    }}
                  />
                </div>
                <small>
                  {item.code} <span>{item.order_count} 单</span>
                </small>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Orders({ view, currency }: { view: View; currency: string }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<OrdersPage | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<Order | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    api<OrdersPage>(
      `/orders?page=${page}&page_size=10&view=${view}`,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "订单读取失败");
      });
    return () => controller.abort();
  }, [page, view, attempt]);
  return (
    <section className="panel orders-panel">
      <div className="panel-heading">
        <div>
          <h2>销售订单</h2>
          <p>
            {view === "operating" ? "当前有效销售口径" : "全部原始状态"} ·
            金额单位 {currency}（暂定）
          </p>
        </div>
        {data && <span className="small-label">共 {data.total} 张</span>}
      </div>
      {error ? (
        <div className="error-panel" role="alert">
          <p>{error}</p>
          <button className="button" onClick={() => setAttempt((n) => n + 1)}>
            重试订单查询
          </button>
        </div>
      ) : !data ? (
        <div className="empty-text" role="status">
          正在读取订单…
        </div>
      ) : (
        <>
          <div className="table-scroll">
            <table className="orders-table">
              <thead>
                <tr>
                  <th>订单编号</th>
                  <th>采购企业</th>
                  <th>创建日期</th>
                  <th>状态</th>
                  <th className="numeric">订单金额</th>
                  <th>
                    <span className="sr-only">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((order) => (
                  <tr key={order.order_id}>
                    <td className="order-number">{order.order_number}</td>
                    <td>
                      <strong>{order.buyer_name || order.buyer_code}</strong>
                      <small>{order.buyer_code}</small>
                    </td>
                    <td>{order.created_at.slice(0, 10)}</td>
                    <td>
                      <span className="status-chip">{order.status}</span>
                    </td>
                    <td className="numeric">{money(order.amount, 4)}</td>
                    <td>
                      <button
                        className="detail-button"
                        aria-label={`查看订单 ${order.order_number}`}
                        onClick={() => setSelected(order)}
                      >
                        明细 <ChevronRight size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.total === 0 && (
              <p className="empty-text">当前范围没有符合条件的订单。</p>
            )}
          </div>
          <div className="pagination">
            <span>
              第 {page} / {Math.max(1, Math.ceil(data.total / 10))} 页 · 每页 10
              条
            </span>
            <div>
              <button
                aria-label="上一页"
                disabled={page === 1}
                onClick={() => setPage((n) => n - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                aria-label="下一页"
                disabled={page * 10 >= data.total}
                onClick={() => setPage((n) => n + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </>
      )}
      {selected && (
        <OrderDialog
          order={selected}
          currency={currency}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  );
}

function OrderDialog({
  order,
  currency,
  onClose,
}: {
  order: Order;
  currency: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="order-dialog"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      aria-labelledby="order-title"
    >
      <div className="dialog-heading">
        <div>
          <div className="eyebrow">ORDER EVIDENCE</div>
          <h2 id="order-title">订单依据</h2>
          <p>{order.order_number}</p>
        </div>
        <button aria-label="关闭订单明细" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="order-meta">
        <div>
          <small>采购企业</small>
          <strong>{order.buyer_name || order.buyer_code}</strong>
          <span>{order.buyer_code}</span>
        </div>
        <div>
          <small>原始状态</small>
          <strong>{order.status}</strong>
        </div>
        <div>
          <small>订单总金额 · {currency}</small>
          <strong>{money(order.amount, 4)}</strong>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>商品 / 编码</th>
              <th className="numeric">数量</th>
              <th className="numeric">单价</th>
              <th className="numeric">原始行金额</th>
            </tr>
          </thead>
          <tbody>
            {order.lines.map((line) => (
              <tr key={line.line_id}>
                <td>
                  <strong>{line.product_name || line.product_code}</strong>
                  <small>
                    {line.product_code} · 第 {line.line_id} 行
                  </small>
                </td>
                <td className="numeric">{line.quantity}</td>
                <td className="numeric">{money(line.unit_price, 4)}</td>
                <td className="numeric">{money(line.amount, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="dialog-note">
        <Info size={14} />
        金额保留原始四位精度，不用数量 × 单价覆盖 ERP 金额。
      </p>
    </dialog>
  );
}

function Rules({ data }: { data: Dashboard }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const policy = data.operating.policy;
  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v1/business/assumptions", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("暂时无法下载业务台账，请重试。");
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "业务假设与待确认事项.md";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "下载失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rules-layout">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>当前经营规则</h2>
            <p>以下口径随本次快照保存，修改规则后需重新分析。</p>
          </div>
          <span className="rule-badge">
            {policy.provisional ? "暂定规则" : "已确认规则"}
          </span>
        </div>
        <div className="policy-grid">
          <div>
            <small>规则版本</small>
            <strong>{policy.policy_id}</strong>
          </div>
          <div>
            <small>货币单位</small>
            <strong>
              {policy.currency} <span>暂定</span>
            </strong>
          </div>
          <div>
            <small>统计日期</small>
            <strong>销售订单创建日期</strong>
          </div>
          <div>
            <small>业务时区</small>
            <strong>{policy.business_timezone}</strong>
          </div>
        </div>
        <div className="rules-table">
          <table>
            <thead>
              <tr>
                <th>原始订单状态</th>
                <th>统计方式</th>
                <th className="numeric">当前订单数</th>
              </tr>
            </thead>
            <tbody>
              {policy.known_statuses.map((state) => (
                <tr key={state}>
                  <td>{state}</td>
                  <td>
                    <span
                      className={
                        policy.included_statuses.includes(state)
                          ? "included-label"
                          : "excluded-label"
                      }
                    >
                      {policy.included_statuses.includes(state) ? (
                        <>
                          <Check size={14} />
                          纳入有效销售
                        </>
                      ) : (
                        "保留原单，暂不纳入"
                      )}
                    </span>
                  </td>
                  <td className="numeric">
                    {data.statuses.find((item) => item.status === state)
                      ?.order_count || 0}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="rule-impact">
          <strong>本次筛选影响</strong>
          <p>
            从 {data.raw_summary.order_count} 张原始订单中纳入{" "}
            {data.operating.summary.order_count} 张，排除{" "}
            {data.operating.excluded_order_count} 张；排除金额{" "}
            {money(data.operating.excluded_order_amount)} {policy.currency}。
          </p>
        </div>
      </section>
      <div>
        <section className="panel assumptions-card">
          <span className="document-icon">
            <FileText size={25} />
          </span>
          <h2>业务假设台账</h2>
          <p>
            集中记录状态、金额、支付退款、商家归属等 19
            项待确认细节，包含默认做法、影响范围和后续修改位置。
          </p>
          <button className="button" disabled={busy} onClick={download}>
            <ArrowDownToLine size={16} />
            {busy ? "正在下载…" : "下载完整业务台账"}
          </button>
          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}
          <small>可逐项确认，无需一次性整理完毕。</small>
        </section>
        <section className="panel boundary-card">
          <h3>当前指标边界</h3>
          <ul>
            <li>订单金额沿用 ERP，不另加减税费和优惠。</li>
            <li>订单状态不代表已收款；退货单不等于成功退款。</li>
            <li>商家归属确认前，暂不展示商家销售排行。</li>
            <li>备份当日可能不完整，不据此外推全天或计算环比。</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
