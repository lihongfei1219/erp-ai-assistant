import { test, expect, type Page } from "@playwright/test";

function turn(result: unknown, overrides: Record<string, unknown> = {}) {
  return {
    status: "result",
    understood_summary: "按指定日期分析销售数据。",
    draft: { intents: [] },
    clarification: null,
    choices: [],
    applied_defaults: [],
    allow_free_text: true,
    conversation_token: "result-context",
    result,
    repeated_clarification: false,
    available_dates: { start: "2026-09-01", end_exclusive: "2026-09-16" },
    ...overrides,
  };
}

async function enter(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await expect(page.getByLabel("开始日期", { exact: true })).toHaveValue(
    "2026-09-01",
  );
}

test("manual analysis renders exact values, local Plotly chart and evidence", async ({
  page,
}) => {
  await enter(page);
  await page.getByRole("button", { name: "运行分析", exact: true }).click();
  const result = page.getByTestId("analysis-results");
  await expect(result).toContainText("1200.0000");
  await page.getByLabel("分析类型").selectOption("product_ranking");
  await page.getByRole("button", { name: "运行分析", exact: true }).click();
  await expect(result.locator(".js-plotly-plot")).toBeVisible();
  await result.getByRole("button", { name: "查看证据" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("DEMO-");
  await page.getByRole("button", { name: "关闭分析证据" }).click();
  await page.screenshot({
    path: "../.local/analysis-dashboard.png",
    fullPage: true,
  });
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载分析结果" }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/^analysis-.*\.json$/);
});

test("comparison and failed request do not retain stale success", async ({
  page,
}) => {
  await enter(page);
  await page.getByLabel("分析类型").selectOption("comparison");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-09-08");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-09-14");
  await page.getByLabel("比较开始日期").fill("2026-09-01");
  await page.getByLabel("比较结束日期").fill("2026-09-07");
  await page.getByRole("button", { name: "运行分析", exact: true }).click();
  await expect(page.getByTestId("analysis-results")).toContainText("-200.0000");
  await page.route("**/api/v1/analysis/run", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ detail: "数据未覆盖完整区间" }),
    }),
  );
  await page.getByRole("button", { name: "运行分析", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("数据未覆盖完整区间");
  await expect(page.getByTestId("analysis-results")).toHaveCount(0);
});

test("question, follow-up and clarification preserve the expected context", async ({
  page,
}) => {
  await page.route("**/api/v1/analysis/catalog", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), model_enabled: true },
    });
  });
  let calls = 0;
  await page.route("**/api/v1/analysis/converse", async (route) => {
    const body = route.request().postDataJSON();
    calls += 1;
    if (calls === 1) expect(body.conversation_token).toBeNull();
    else expect(body.conversation_token).toBe("result-context");
    if (calls === 3) {
      await route.fulfill({
        json: turn(null, {
          status: "capability_gap",
          understood_summary: "希望继续分析利润。",
          clarification: {
            id: "profit",
            intent_id: null,
            field: "metric",
            kind: "unsupported",
            question: "缺少成本数据，无法计算利润。是否改看销售金额？",
          },
        }),
      });
      return;
    }
    const response = await page.request.post("/api/v1/analysis/run", {
      data: {
        steps: [
          {
            kind: calls === 1 ? "summary" : "buyer_ranking",
            start_date: "2026-09-01",
            end_date_exclusive: "2026-09-08",
          },
        ],
      },
    });
    await route.fulfill({ json: turn(await response.json()) });
  });
  await enter(page);
  await page
    .getByLabel("分析问题", { exact: true })
    .fill("9月1日至7日销售概览");
  await page.getByRole("button", { name: "开始智能分析" }).click();
  await expect(page.getByTestId("analysis-results")).toContainText("700.0000");
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "9月1日至7日销售概览",
  );
  await page.getByLabel("分析问题", { exact: true }).fill("再看客户排行");
  await page.getByRole("button", { name: "开始智能分析" }).click();
  await expect(
    page
      .getByTestId("analysis-results")
      .getByRole("heading", { name: "客户排行" }),
  ).toBeVisible();
  await page.getByLabel("分析问题", { exact: true }).fill("再看利润");
  await page.getByRole("button", { name: "开始智能分析" }).click();
  await expect(page.getByRole("region", { name: "分析引导" })).toContainText(
    "缺少成本数据",
  );
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByTestId("analysis-results")).toHaveCount(0);
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "再看利润",
  );
  await page.getByRole("button", { name: "清除追问上下文" }).click();
  await expect(
    page.getByRole("button", { name: "清除追问上下文" }),
  ).toHaveCount(0);
});

test("mobile analysis remains within viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enter(page);
  await page.getByLabel("分析类型").selectOption("trend");
  const navigationHeight = await page
    .getByRole("navigation", { name: "主导航" })
    .evaluate((el) => el.getBoundingClientRect().height);
  expect(navigationHeight).toBeLessThan(65);
  await page.getByRole("button", { name: "运行分析", exact: true }).click();
  await expect(page.locator(".js-plotly-plot")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391);
  await page.screenshot({
    path: "../.local/analysis-mobile.png",
    fullPage: true,
  });
});

test("colloquial ranking shows understood dates, metric and product scope", async ({
  page,
}) => {
  await page.route("**/api/v1/analysis/catalog", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), model_enabled: true },
    });
  });
  await page.route("**/api/v1/analysis/converse", async (route) => {
    expect(route.request().postDataJSON().question).toBe(
      "分析2026年9月1日至5号的销售数据，哪些药品卖的好",
    );
    const response = await page.request.post("/api/v1/analysis/run", {
      data: {
        steps: [
          {
            kind: "product_ranking",
            start_date: "2026-09-01",
            end_date_exclusive: "2026-09-06",
          },
        ],
      },
    });
    await route.fulfill({
      json: turn({
        ...(await response.json()),
        interpretation: [
          "商品排行：2026-09-01 至 2026-09-05（含首尾两天）；按销售金额（有效订单口径）统计，从高到低展示前 10 名。",
          "“药品”按当前商品范围理解：本次未按药品类别筛选，可能包含器械、保健品等其他商品。",
        ],
      }),
    });
  });
  await enter(page);
  await page
    .getByLabel("分析问题", { exact: true })
    .fill("分析2026年9月1日至5号的销售数据，哪些药品卖的好");
  await page.getByRole("button", { name: "开始智能分析" }).click();
  const understood = page.getByRole("note", { name: "本次问题理解" });
  await expect(understood).toContainText("2026-09-05（含首尾两天）");
  await expect(understood).toContainText("销售金额");
  await expect(understood).toContainText("前 10 名");
  await expect(understood).toContainText("未按药品类别筛选");
  await expect(page.locator(".js-plotly-plot")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.screenshot({
    path: "../.local/analysis-natural-language.png",
    fullPage: true,
  });
});
