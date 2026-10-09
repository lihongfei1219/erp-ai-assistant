import { useState, type ComponentType } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  ChartNoAxesCombined,
  Check,
  ChevronRight,
  CircleDollarSign,
  Layers3,
  PackageSearch,
  Percent,
  Search,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Analytics } from "./Analytics";
import "./analytics-hub.css";

type Topic = {
  id: string;
  title: string;
  question: string;
  description: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  steps: { title: string; detail: string }[];
  fields: string[];
  data: string[];
  definition: string;
};

const topics: Topic[] = [
  {
    id: "growth",
    title: "品种增长",
    question: "哪些药卖得更好了？",
    icon: TrendingUp,
    description:
      "比较上期和去年同期，找出增长、下降的品种，再追溯变化来自哪些客户。",
    steps: [],
    fields: [],
    data: [],
    definition: "",
  },
  {
    id: "price",
    title: "价格变化",
    question: "价格到底降了多少？",
    icon: Percent,
    description:
      "比较同一品种的价格涨跌，定位涉及的客户，再结合进货成本观察利润空间。",
    steps: [
      {
        title: "找出价格变化",
        detail:
          "按相同编码、规格、厂家和单位比较售价，区分真实降价与销售结构变化。",
      },
      {
        title: "定位受影响客户",
        detail:
          "展开品种，查看哪些客户的成交价变化，以及对应的销售数量和金额。",
      },
      {
        title: "结合进货成本",
        detail: "对照对应成本，观察售价与成本之间的空间是否缩小。",
      },
    ],
    fields: [
      "品种／规格",
      "比较期单价",
      "本期单价",
      "价格变化",
      "涉及客户",
      "对应成本",
      "价差空间",
    ],
    data: [
      "销售明细金额与数量",
      "品种规格和计量单位",
      "客户成交记录",
      "可对应的进货成本",
    ],
    definition:
      "需确认含税或未税价格、退货处理、加权均价算法和成本匹配方式；同一品种保持规格、厂家和单位可比。",
  },
  {
    id: "margin",
    title: "销量与毛利",
    question: "卖得多，是否也赚得多？",
    icon: ChartNoAxesCombined,
    description:
      "把销量、售价和已售商品成本放在一起，分清增长是否真正带来了更多毛利。",
    steps: [
      {
        title: "销量和收入一起看",
        detail:
          "比较各品种卖出的数量和销售金额，找出多卖货却未增加收入的情况。",
      },
      {
        title: "核算对应毛利",
        detail: "将销售收入与已售商品成本对应，展示毛利额和毛利率的变化。",
      },
      {
        title: "拆解变化来源",
        detail: "区分销量、售价及成本变化的影响，再定位需要关注的品种和客户。",
      },
    ],
    fields: [
      "品种",
      "销量变化",
      "销售收入",
      "已售商品成本",
      "毛利额",
      "毛利率",
      "变化来源",
    ],
    data: [
      "销售数量与收入",
      "销售退货记录",
      "已售商品对应成本",
      "可比较的历史期间",
    ],
    definition:
      "毛利计算前需确认收入、退货、成本结转及税额口径；毛利不等同于扣除所有费用后的净利润。",
  },
  {
    id: "inventory",
    title: "库存积压",
    question: "哪些货压得久？",
    icon: PackageSearch,
    description:
      "从批次库龄、占用金额、近期销售速度和效期四个角度，找出需要关注的库存。",
    steps: [
      {
        title: "定位久放的批次",
        detail: "按品种与批次查看入库时间、现有数量和存放时长。",
      },
      {
        title: "衡量占用与可售天数",
        detail: "结合库存成本和近期销售速度，估计资金占用以及库存还能卖多久。",
      },
      {
        title: "提示效期风险",
        detail: "对照批次到期日，标出销售速度与剩余效期不匹配的库存。",
      },
    ],
    fields: [
      "品种／批次",
      "入库日期",
      "库存数量",
      "库龄",
      "占用金额",
      "预计可售天数",
      "到期日",
    ],
    data: [
      "批次库存与入库日期",
      "库存成本",
      "批号及有效期",
      "近期同品种销售记录",
    ],
    definition:
      "现有库存查询来自备份时点。库龄起算、无近期销售的处理和效期预警阈值需明确；预计可售天数是估算，不是销量承诺。",
  },
  {
    id: "stocking",
    title: "旺季备货",
    question: "旺季前要不要备货？",
    icon: CalendarClock,
    description:
      "参考去年同期，结合今年销售、当前库存、已订未到的货和供货时间，评估提前备货。",
    steps: [
      {
        title: "回看同期需求",
        detail: "查看去年同期的品种销售表现，并结合今年近期变化观察需求。",
      },
      {
        title: "盘点可用供给",
        detail: "同时考虑当前库存、采购在途和预计到货时间，避免重复备货。",
      },
      {
        title: "形成备货评估清单",
        detail:
          "结合供货周期，提示哪些品种需要提前评估，并列出依据和待确认条件。",
      },
    ],
    fields: [
      "品种",
      "去年同期销量",
      "今年销售变化",
      "当前库存",
      "已订未到",
      "供货周期",
      "备货关注项",
    ],
    data: [
      "去年同期和今年销售",
      "当前库存快照",
      "未到货采购订单",
      "供应商供货时间",
    ],
    definition:
      "需要确认旺季范围、供货周期及安全库存假设。页面提供备货评估依据，不自动生成或提交采购订单。",
  },
  {
    id: "capital",
    title: "资金收益",
    question: "算上利息，还划不划算？",
    icon: CircleDollarSign,
    description:
      "在确认资金成本算法和数据后，估算毛利扣除资金成本后的余额，辅助判断是否继续投入。",
    steps: [
      {
        title: "确认投入和占用时间",
        detail: "明确哪些资金需要计入，以及各笔资金的占用起止时间。",
      },
      {
        title: "估算资金成本",
        detail: "按企业确认的利率和计息规则计算，清楚展示假设与依据。",
      },
      {
        title: "观察剩余收益",
        detail: "对照毛利与资金成本，辅助评估哪些经营投入需要重新审视。",
      },
    ],
    fields: [
      "品种／经营范围",
      "毛利",
      "占用资金",
      "占用天数",
      "资金成本",
      "扣除后余额",
      "计算假设",
    ],
    data: [
      "可信的毛利结果",
      "资金占用金额",
      "占用起止时间",
      "企业确认的利率与计息规则",
    ],
    definition:
      "资金成本算法须由企业确认；毛利扣除资金成本后的余额仍不等同于净利润，还可能涉及其他费用。",
  },
];

export function AnalyticsHub() {
  const [active, setActive] = useState("overview");
  const [visited, setVisited] = useState<string[]>([]);
  function navigate(id: string) {
    setActive(id);
    if (
      id === "growth" ||
      id === "price" ||
      id === "margin" ||
      id === "inventory" ||
      id === "stocking" ||
      id === "general"
    )
      setVisited((items) => (items.includes(id) ? items : [...items, id]));
  }
  const topic = topics.find((item) => item.id === active);
  return (
    <div className="analytics-hub">
      <nav className="analysis-topic-nav" aria-label="AI 数据分析子栏目">
        <button
          aria-pressed={active === "overview"}
          onClick={() => navigate("overview")}
        >
          <Layers3 size={16} aria-hidden />
          主题总览
        </button>
        {topics.map((item) => (
          <button
            key={item.id}
            aria-pressed={active === item.id}
            onClick={() => navigate(item.id)}
          >
            <item.icon size={16} aria-hidden />
            {item.title}
          </button>
        ))}
        <button
          aria-pressed={active === "general"}
          onClick={() => navigate("general")}
        >
          <Search size={16} aria-hidden />
          自由问数
        </button>
      </nav>
      {active === "overview" && (
        <>
          <section className="analysis-hub-intro">
            <div>
              <span className="analysis-kicker">经营问题 · 主题分析</span>
              <h2>先选一个你关心的经营问题</h2>
              <p>
                看清哪些品种在增长，价格和毛利如何变化，再评估库存、备货与资金投入。
              </p>
            </div>
            <span className="analysis-theme-count">
              <strong>6</strong>个分析主题
            </span>
          </section>
          <div className="analysis-topic-grid">
            {topics.map((item, index) => (
              <button
                className={`analysis-topic-card ${["growth", "price", "margin", "inventory", "stocking"].includes(item.id) ? "available" : ""}`}
                key={item.id}
                onClick={() => navigate(item.id)}
                aria-label={`进入${item.title}`}
              >
                <span className="analysis-card-top">
                  <span className="analysis-topic-icon">
                    <item.icon size={23} aria-hidden />
                  </span>
                  <span
                    className={`analysis-topic-status ${["growth", "price", "margin", "inventory", "stocking"].includes(item.id) ? "ready" : ""}`}
                  >
                    {[
                      "growth",
                      "price",
                      "margin",
                      "inventory",
                      "stocking",
                    ].includes(item.id) ? (
                      <>
                        <Check size={12} aria-hidden />
                        已开放
                      </>
                    ) : (
                      "等待财务数据"
                    )}
                  </span>
                </span>
                <span className="analysis-card-category">
                  0{index + 1} / {item.title}
                </span>
                <strong>{item.question}</strong>
                <span className="analysis-card-description">
                  {item.description}
                </span>
                <span className="analysis-card-action">
                  {[
                    "growth",
                    "price",
                    "margin",
                    "inventory",
                    "stocking",
                  ].includes(item.id)
                    ? "开始分析"
                    : "查看分析设计"}
                  <ArrowRight size={16} aria-hidden />
                </span>
              </button>
            ))}
          </div>
          <section className="analysis-free-entry">
            <div>
              <Sparkles size={21} aria-hidden />
              <div>
                <h3>还有其他想了解的问题？</h3>
                <p>
                  使用自由问数，继续查询已支持的销售、退货、出库和库存数据。
                </p>
              </div>
            </div>
            <button
              className="button secondary"
              onClick={() => navigate("general")}
            >
              打开自由问数
              <ArrowRight size={15} aria-hidden />
            </button>
          </section>
        </>
      )}
      {active !== "overview" && (
        <header className="analysis-topic-heading">
          <div className="analysis-topic-breadcrumb">
            <button onClick={() => navigate("overview")}>
              <ArrowLeft size={14} aria-hidden />
              主题总览
            </button>
            <ChevronRight size={13} aria-hidden />
            <span>{topic?.title || "自由问数"}</span>
          </div>
          <div className="analysis-topic-title">
            <span className="analysis-topic-icon">
              {topic ? (
                <topic.icon size={25} aria-hidden />
              ) : (
                <Sparkles size={25} aria-hidden />
              )}
            </span>
            <div>
              <h2>{topic?.question || "自由问数"}</h2>
              <p>
                {topic?.description ||
                  "用自己的话提出问题，或通过手动分析选择日期、指标与范围。"}
              </p>
            </div>
          </div>
        </header>
      )}
      {topic &&
        !["growth", "price", "margin", "inventory", "stocking"].includes(
          topic.id,
        ) && (
          <section
            className="analysis-topic-design"
            aria-label={`${topic.title}展示方案`}
          >
            <div className="analysis-design-notice">
              <span className="analysis-topic-status">
                暂缓开发 · 等待财务数据
              </span>
              <p>
                资金收益暂缓开发，等财务数据补齐后再核算。以下保留展示设计，当前不提供利息或扣息收益测算。
              </p>
            </div>
            <section className="panel analysis-design-panel">
              <h3>这个主题将回答什么</h3>
              <div className="analysis-design-steps">
                {topic.steps.map((step, index) => (
                  <div key={step.title}>
                    <span>0{index + 1}</span>
                    <h4>{step.title}</h4>
                    <p>{step.detail}</p>
                  </div>
                ))}
              </div>
            </section>
            <div className="analysis-design-columns">
              <section className="panel analysis-design-panel">
                <h3>计划展示的明细</h3>
                <p>先看汇总变化，再按品种展开到客户、批次或对应依据。</p>
                <div className="analysis-field-list">
                  {topic.fields.map((field) => (
                    <span key={field}>{field}</span>
                  ))}
                </div>
                <small>此处仅展示字段规划，不代表已经生成分析数据。</small>
              </section>
              <section className="panel analysis-design-panel">
                <h3>分析需要的数据</h3>
                <ul className="analysis-data-list">
                  {topic.data.map((data) => (
                    <li key={data}>
                      <span aria-hidden />
                      {data}
                    </li>
                  ))}
                </ul>
                <small>是否可用及历史覆盖范围，以后续数据核验为准。</small>
              </section>
            </div>
            <details className="panel analysis-design-panel">
              <summary>计算前需要明确的口径</summary>
              <p>{topic.definition}</p>
            </details>
          </section>
        )}
      {visited.map((id) => (
        <div key={id} hidden={active !== id} data-testid={`topic-${id}`}>
          <Analytics
            mode={
              id === "growth"
                ? "growth"
                : id === "price"
                  ? "price"
                  : id === "margin"
                    ? "margin"
                    : id === "inventory"
                      ? "inventory"
                      : id === "stocking"
                        ? "stocking"
                        : "general"
            }
          />
        </div>
      ))}
    </div>
  );
}
