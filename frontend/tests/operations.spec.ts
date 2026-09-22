import { test, expect } from "@playwright/test";

test("business results preserve units, titles and source evidence without sales links", async ({
  page,
  request,
}) => {
  const response = await request.post("/api/v1/analysis/run", {
    data: {
      steps: [
        {
          domain: "returns",
          kind: "summary",
          metric: "amount",
          start_date: "2026-09-15",
          end_date_exclusive: "2026-09-16",
        },
        {
          domain: "shipping",
          kind: "list",
          metric: "quantity",
          start_date: "2026-09-15",
          end_date_exclusive: "2026-09-16",
        },
        {
          domain: "inventory",
          kind: "product_ranking",
          metric: "stock",
          start_date: "2026-09-16",
          end_date_exclusive: "2026-09-17",
        },
      ],
    },
  });
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  await page.route("**/api/v1/analysis/catalog", async (route) => {
    const actual = await route.fetch();
    await route.fulfill({
      json: { ...(await actual.json()), model_enabled: true },
    });
  });
  await page.route("**/api/v1/analysis/converse", (route) =>
    route.fulfill({
      json: {
        status: "result",
        understood_summary: "退货、销售出库和库存分析",
        draft: { intents: [] },
        clarification: null,
        choices: [],
        applied_defaults: [],
        allow_free_text: true,
        conversation_token: null,
        result,
        repeated_clarification: false,
        available_dates: { start: "2026-09-01", end_exclusive: "2026-09-16" },
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await expect(
    page.getByText(
      "支持自由提问；当前快照可分析：销售、退货、销售出库、库存。",
    ),
  ).toBeVisible();
  await page
    .getByLabel("分析问题", { exact: true })
    .fill("看看退货、出库和库存");
  await page.getByRole("button", { name: "开始智能分析" }).click();
  const output = page.getByTestId("analysis-results");
  await expect(output).toContainText("退货概览");
  await expect(output).toContainText("销售出库明细");
  await expect(output).toContainText("库存商品排行");
  await expect(output).toContainText("12.0000");
  await expect(output).toContainText("盒");
  await expect(output).toContainText("不是实时或日末库存");
  await expect(output.getByRole("button", { name: "查看证据" })).toHaveCount(0);
  await expect(output).not.toContainText("无订单");
});
