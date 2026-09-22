import { useEffect, useState } from "react";
import { Check, Clock3, Info, RefreshCw, Send } from "lucide-react";
import { api, type Summary } from "./api";

interface DigestPreview {
  digest: {
    day: string;
    demo: boolean;
    partial: boolean;
    title: string;
    text: string;
    summary: Summary;
  };
  configured: boolean;
  configuration_message: string;
  schedule: string;
}

export function FeishuDaily() {
  const [demo, setDemo] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [preview, setPreview] = useState<DigestPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setPreview(null);
    setError("");
    setResult("");
    api<DigestPreview>(
      `/feishu/daily${demo ? "?day=2026-09-16&demo=true" : ""}`,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setPreview(data);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "无法生成日报预览");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [demo, attempt]);

  async function send() {
    if (!preview || sending) return;
    setSending(true);
    setError("");
    setResult("");
    try {
      const response = await fetch("/api/v1/feishu/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          day: preview.digest.day,
          demo: preview.digest.demo,
        }),
        cache: "no-store",
      });
      const payload = await response.json();
      if (!response.ok)
        throw new Error(payload.detail || "暂时无法发送，请先核实群内消息");
      setResult(payload.message);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "发送结果待核实，请先查看飞书群",
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="feishu-layout">
      <section className="panel feishu-main">
        <div className="panel-heading">
          <div>
            <h2>日报预览</h2>
            <p>预览与群消息使用同一份统计结果。</p>
          </div>
          <span className="rule-badge">{demo ? "演示模式" : "真实昨日"}</span>
        </div>
        <div className="feishu-controls">
          <label>
            <input
              type="checkbox"
              checked={demo}
              disabled={sending}
              onChange={(event) => setDemo(event.target.checked)}
            />
            将 2026-09-16 作为昨日演示
          </label>
          <button
            className="button secondary"
            disabled={loading || sending}
            onClick={() => setAttempt((value) => value + 1)}
          >
            <RefreshCw size={15} className={loading ? "spinning" : ""} />
            刷新预览
          </button>
        </div>
        {loading && (
          <p className="feishu-status" role="status">
            正在生成日报…
          </p>
        )}
        {error && (
          <p className="login-error feishu-status" role="alert">
            {error}
          </p>
        )}
        {preview && (
          <article
            className={`feishu-card ${preview.digest.partial ? "partial" : ""}`}
            data-testid="feishu-preview"
          >
            <header>
              <Send size={18} />
              <h3>{preview.digest.title}</h3>
            </header>
            <div className="feishu-message">{preview.digest.text}</div>
          </article>
        )}
        <div className="feishu-send">
          <button
            className="button"
            onClick={send}
            disabled={
              !preview?.configured || loading || sending || Boolean(result)
            }
          >
            <Send size={16} />
            {sending
              ? "正在发送…"
              : demo
                ? "发送演示日报到飞书群"
                : "发送昨日销售日报到飞书群"}
          </button>
          {result && (
            <p className="feishu-success" role="status">
              <Check size={16} />
              {result}
            </p>
          )}
          {!preview?.configured && !loading && (
            <small>
              {preview?.configuration_message ||
                "日报准备好且群机器人配置完成后可发送"}
            </small>
          )}
        </div>
      </section>
      <aside className="feishu-aside">
        <section className="panel assumptions-card">
          <span className="document-icon">
            <Clock3 size={25} />
          </span>
          <h2>每日定时推送</h2>
          <strong className="feishu-schedule">
            每天 08:00 · Asia/Shanghai
          </strong>
          <p>
            定时任务按真实日期推送昨天的销售情况。演示日期只用于本页预览与手动发送。
          </p>
          <p>
            需单独启动日报调度进程，并保持电脑运行。当前页面不会启动定时任务。
          </p>
          <small>
            数据未更新时暂停正式日报；成功发送后，同一天、同一范围不会重复推送。
          </small>
        </section>
        <section className="panel boundary-card">
          <h3>连接指定飞书群</h3>
          <ol>
            <li>在群设置中添加“自定义机器人”。</li>
            <li>
              将 Webhook 保存至本项目 <code>.local/feishu-webhook.txt</code>。
            </li>
            <li>
              如启用签名校验，将密钥保存至 <code>.local/feishu-secret.txt</code>
              。
            </li>
            <li>刷新预览后，即可发送到该机器人所在群。</li>
          </ol>
          <p>
            若设置了关键词，请使用“销售日报”。凭据仅保存在本机，不在页面展示。
          </p>
        </section>
        <section className="panel boundary-card">
          <h3>
            <Info size={16} /> 数据说明
          </h3>
          <p>
            9 月 16 日的备份只覆盖到
            14:33。演示日报展示已记录销售，并标注当日不完整，不计算全天环比。
          </p>
          <p>当前统计有效销售订单金额，不代表支付成交额。</p>
        </section>
      </aside>
    </div>
  );
}
