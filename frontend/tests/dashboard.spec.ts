import { test, expect, type Page } from "@playwright/test";
import { money } from "../src/api";

async function openWorkspace(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "经营概览", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("kpi-0")).toContainText("1,200.00");
}

test("decimal display preserves cents beyond floating point precision", () => {
  expect(money("99999999999999.9999")).toBe("100,000,000,000,000.00");
  expect(money("0.1050")).toBe("0.11");
  expect(money("-0.1050")).toBe("-0.11");
  expect(money("100.0001", 4)).toBe("100.0001");
  expect(money(null)).toBe("—");
});

test("direct entry, effective vs raw totals, evidence pagination and refresh", async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole("button", { name: "全部原始订单", exact: true }).click();
  await expect(page.getByTestId("kpi-0")).toContainText("1,290.00");
  await page
    .getByRole("button", { name: "有效销售（暂定）", exact: true })
    .click();
  await expect(page.getByTestId("kpi-0")).toContainText("1,200.00");
  await page.getByRole("button", { name: "订单明细", exact: true }).click();
  await expect(page.getByText("共 11 张")).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByText("DEMO-011", { exact: true })).toBeVisible();
  await expect(page.getByText("DEMO-012", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "查看订单 DEMO-011" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText("200.0000");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "全部原始订单", exact: true }).click();
  await expect(page.getByText("共 12 张")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("kpi-0")).toContainText("1,200.00");
  await expect(page.getByLabel("访问令牌", { exact: true })).toHaveCount(0);
});

test("rule explanation and central assumptions download", async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole("button", { name: "业务口径", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "当前经营规则" }),
  ).toBeVisible();
  await expect(page.getByText("排除 1 张", { exact: false })).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载完整业务台账" }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("业务假设与待确认事项.md");
});

test("old browser token is removed and requests need no Authorization header", async ({
  page,
}) => {
  await page.addInitScript(() =>
    sessionStorage.setItem("erp-access-token", "obsolete-local-token"),
  );
  const credentials: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/") && request.headers().authorization)
      credentials.push(request.headers().authorization);
  });
  await openWorkspace(page);
  await expect(page.getByLabel("访问令牌", { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(() => sessionStorage.getItem("erp-access-token")),
  ).toBeNull();
  expect(credentials).toEqual([]);
});

test("snapshot failure offers retry and never renders zero metrics", async ({
  page,
}) => {
  await page.route("**/api/v1/dashboard/operating", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "分析快照未就绪" }),
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("分析快照未就绪");
  await expect(page.getByTestId("kpi-0")).toHaveCount(0);
  await page.unroute("**/api/v1/dashboard/operating");
  await page.getByRole("button", { name: "重新尝试" }).click();
  await expect(page.getByTestId("kpi-0")).toContainText("1,200.00");
});

test("empty effective data is explicit", async ({ page }) => {
  await page.route("**/api/v1/dashboard/operating", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.operating.summary = {
      order_amount: "0.0000",
      order_count: 0,
      buyer_count: 0,
      average_order_amount: null,
      line_count: 0,
      product_count: 0,
    };
    body.operating.buyers = [];
    body.operating.products = [];
    body.operating.daily = [];
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await expect(
    page.getByText("当前统计范围内没有符合条件的订单。", { exact: false }),
  ).toBeVisible();
  await expect(page.getByTestId("kpi-3")).toContainText("—");
});

test("mobile layout keeps navigation usable without page overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openWorkspace(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "业务口径", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "当前经营规则" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.local/phase2-mobile.png",
    fullPage: true,
  });
});

test("desktop renders cleanly with no runtime exceptions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1080 });
  await openWorkspace(page);
  await page.screenshot({
    path: "../.local/phase2-dashboard.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("Feishu demo previews September 16 and does not send without configuration", async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole("button", { name: "飞书日报", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "飞书销售日报", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("feishu-preview")).toContainText("2026-09-16");
  await expect(page.getByTestId("feishu-preview")).toContainText(
    "当日数据不完整",
  );
  await expect(page.getByTestId("feishu-preview")).toContainText("演示");
  await expect(
    page.getByRole("button", { name: "发送演示日报到飞书群" }),
  ).toBeDisabled();
  await expect(
    page.getByText("每天 08:00 · Asia/Shanghai", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.local/feishu-preview-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../.local/feishu-preview-mobile.png",
    fullPage: true,
  });
});
