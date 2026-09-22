export type AnalysisKind =
  | "summary"
  | "trend"
  | "buyer_ranking"
  | "product_ranking"
  | "comparison"
  | "anomalies"
  | "list"
  | "existence";
export type BusinessDomain = "sales" | "returns" | "shipping" | "inventory";
export interface AnalysisStep {
  domain?: BusinessDomain;
  kind: AnalysisKind;
  start_date: string;
  end_date_exclusive: string;
  metric?: "amount" | "orders" | "quantity" | "stock";
  order?: "ascending" | "descending";
  top_n?: number;
  dimension?: "product" | "buyer";
  comparison_start_date?: string;
  comparison_end_date_exclusive?: string;
}
export interface AnalysisPlan {
  steps: AnalysisStep[];
}
export interface AnalysisCatalog {
  semantic_domains?: Record<string, { label: string; executable: boolean }>;
  available_start: string;
  available_end_exclusive: string;
  model_enabled: boolean;
  analyses: Record<AnalysisKind, string>;
  metrics: { id: string; label: string; definition: string }[];
}
export interface Figure {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
}
export interface AnalysisResult {
  domain?: BusinessDomain;
  kind: AnalysisKind;
  title: string;
  columns: Record<string, string>;
  rows: Record<string, string | number | number[] | null>[];
  totals: Record<string, string | number | null>;
  findings: string[];
  notes: string[];
  chart: Figure | null;
}
export interface AnalysisResponse {
  run_id: string;
  generated_at: string;
  plan: AnalysisPlan;
  provenance: {
    source_as_of: string;
    snapshot_generated_at: string;
    source_kind: string;
    scope: { all_buyers: boolean; buyer_codes: string[] };
    policy_id: string;
    policy_fingerprint: string;
    metric_version: string;
    currency: string;
    business_timezone: string;
    included_statuses: string[];
  };
  results: AnalysisResult[];
  warnings: string[];
  interpretation?: string[];
  conversation_token?: string | null;
}

export interface DialogueRequest {
  request_id?: string;
  question?: string;
  conversation_token: string | null;
  choice_id?: string;
  date_range?: { start: string; end_exclusive: string };
}
export interface DialogueChoice {
  id: string;
  label: string;
  action: { kind: string; intent_id?: string | null; field?: string | null };
}
export interface DialogueIntent {
  id: string;
  label: string;
  fields: Record<string, string | number | boolean | null>;
  constraints: { id: string; label: string }[];
  field_sources: Record<string, "explicit" | "inherited" | "default">;
}
export interface DialogueTurn {
  status: "result" | "needs_input" | "capability_gap" | "data_gap";
  understood_summary: string;
  draft: { intents: DialogueIntent[] };
  clarification: {
    id: string;
    intent_id: string | null;
    field: string;
    kind: string;
    question: string;
  } | null;
  choices: DialogueChoice[];
  applied_defaults: string[];
  allow_free_text: true;
  conversation_token: string | null;
  result: AnalysisResponse | null;
  repeated_clarification: boolean;
  available_dates: { start: string; end_exclusive: string };
}

export function shiftDay(value: string, offset: number) {
  const day = new Date(`${value}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

interface PlotlyAPI {
  newPlot(
    element: HTMLElement,
    data: Figure["data"],
    layout: Figure["layout"],
    config: Record<string, unknown>,
  ): Promise<unknown>;
  purge(element: HTMLElement): void;
  Plots: { resize(element: HTMLElement): void };
}
declare global {
  interface Window {
    Plotly?: PlotlyAPI;
  }
}
let plotlyLoading: Promise<PlotlyAPI> | undefined;
export function loadPlotly(): Promise<PlotlyAPI> {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  if (!plotlyLoading)
    plotlyLoading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/assets/analytics-plotly.js";
      script.onload = () => {
        if (window.Plotly) resolve(window.Plotly);
        else {
          plotlyLoading = undefined;
          script.remove();
          reject(new Error("图表组件未就绪"));
        }
      };
      script.onerror = () => {
        plotlyLoading = undefined;
        script.remove();
        reject(new Error("图表加载失败，可查看下方数据表"));
      };
      document.head.appendChild(script);
    });
  return plotlyLoading;
}
