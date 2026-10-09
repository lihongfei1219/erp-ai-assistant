import { test, expect } from "@playwright/test";

test("销量增但毛利下降可以拆解到成本并追溯客户和证据", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("button", { name: "进入销量与毛利", exact: true })
    .click();
  await expect(page.getByLabel("毛利比较开始日期")).toHaveValue("2026-08-01");
  await page
    .getByRole("button", { name: "比较销量与毛利", exact: true })
    .click();
  const result = page.getByRole("article", { name: "销量毛利结果" });
  await expect(result).toContainText("品种销量与毛利");
  await expect(result.getByLabel("毛利概览")).toContainText("16.00");
  await expect(result.getByLabel("毛利概览")).toContainText("-34.00");
  await result.getByText("查看三因素拆解", { exact: true }).click();
  await expect(result).toContainText("销量影响 30.0000");
  await expect(result).toContainText("成本影响 -64.0000");
  await result.getByRole("button", { name: /销量增加但毛利未增/ }).click();
  await expect(result.locator("tbody tr")).toHaveCount(1);
  await result.screenshot({ path: "../.local/margin-analysis-desktop.png" });
  await result
    .getByRole("button", { name: "查看客户毛利", exact: true })
    .click();
  await expect(result).toContainText("客户销量与毛利");
  await result
    .getByRole("button", { name: "查看出库证据", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "出库证据" });
  await expect(dialog).toContainText("保存的采购单价");
  await expect(dialog).toContainText("0.9");
  await page.keyboard.press("Escape");
  const pending = page.waitForEvent("download");
  await result
    .getByRole("button", { name: "下载完整变化明细", exact: true })
    .click();
  expect((await pending).suggestedFilename()).toBe(
    "销量毛利完整明细-2026-08-01.json",
  );
  await result.getByRole("button", { name: "较去年同期", exact: true }).click();
  await expect(result).toContainText("2025-08-01");
});

test("手机毛利页与售价成本空间展示一致且缺失比较期不保留旧结果", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  const nav = page.getByRole("navigation", { name: "AI 数据分析子栏目" });
  await nav.getByRole("button", { name: "价格变化", exact: true }).click();
  await page.getByRole("button", { name: "比较售价变化", exact: true }).click();
  await expect(
    page.getByRole("article", { name: "售价变化结果" }),
  ).toContainText("空间变化 -0.4000");
  await nav.getByRole("button", { name: "销量与毛利", exact: true }).click();
  await page
    .getByRole("button", { name: "比较销量与毛利", exact: true })
    .click();
  const result = page.getByRole("article", { name: "销量毛利结果" });
  await expect(result).toContainText("采购成本口径毛利");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/margin-analysis-mobile.png",
    fullPage: true,
  });
  await page.getByLabel("毛利比较开始日期").fill("2026-07-01");
  await page.getByLabel("毛利比较结束日期").fill("2026-07-31");
  await page
    .getByRole("button", { name: "比较销量与毛利", exact: true })
    .click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(result).toHaveCount(0);
});
