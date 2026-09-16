export interface Summary {
  order_amount: string;
  order_count: number;
  buyer_count: number;
  average_order_amount: string | null;
  line_count: number;
  product_count: number;
}
export interface Metadata {
  source_as_of: string;
  generated_at: string;
  source_kind: string;
  window: { start: string; end: string };
  scope: { all_buyers: boolean; buyer_codes: string[] };
  warnings: string[];
  metric_version: string;
}
export interface Daily {
  day: string;
  order_amount: string;
  order_count: number;
  buyer_count: number;
}
export interface Breakdown {
  code: string;
  name: string | null;
  order_amount: string;
  order_count: number;
}
export interface Status {
  status: string;
  order_count: number;
  order_amount: string;
}
export interface Policy {
  policy_id: string;
  label: string;
  included_statuses: string[];
  known_statuses: string[];
  currency: string;
  business_timezone: string;
  provisional: boolean;
  assumption_ids: string[];
}
export interface Dashboard {
  metadata: Metadata;
  operating: {
    policy: Policy;
    policy_fingerprint: string;
    summary: Summary;
    daily: Daily[];
    buyers: Breakdown[];
    products: Breakdown[];
    excluded_order_count: number;
    excluded_order_amount: string;
    unknown_statuses: Status[];
  };
  raw_summary: Summary;
  statuses: Status[];
  quality: {
    reconciled_orders: number;
    price_quantity_mismatch_count: number;
    sql_control_totals_match: boolean;
  };
  buyer_group_count: number;
  product_group_count: number;
}
export interface Order {
  order_id: number;
  order_number: string;
  buyer_code: string;
  buyer_name: string | null;
  created_at: string;
  status: string;
  amount: string;
  lines: {
    line_id: number;
    product_code: string;
    product_name: string | null;
    quantity: string;
    unit_price: string;
    amount: string;
  }[];
}
export interface OrdersPage {
  metadata: Metadata;
  items: Order[];
  total: number;
  page: number;
  page_size: number;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  token: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      typeof payload?.detail === "string"
        ? payload.detail
        : "暂时无法读取数据，请重试。",
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

// Decimal strings are rounded for display with BigInt; financial totals never use JS floats.
export function money(value: string | null, precision = 2): string {
  if (value === null) return "—";
  if (!/^-?\d+(\.\d+)?$/.test(value)) return "—";
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace("-", "").split(".");
  const padded = fraction.padEnd(precision + 1, "0");
  const scale = 10n ** BigInt(precision);
  let units = BigInt(whole) * scale + BigInt(padded.slice(0, precision) || "0");
  if (Number(padded[precision]) >= 5) units += 1n;
  const integer = (units / scale)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative && units ? "-" : ""}${integer}${precision ? `.${(units % scale).toString().padStart(precision, "0")}` : ""}`;
}
export function timestamp(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
