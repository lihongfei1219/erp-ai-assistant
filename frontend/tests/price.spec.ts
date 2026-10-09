import { expect, test, type Page } from "@playwright/test";

async function run(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page.getByRole("button", { name: "进入价格变化", exact: true }).click();
  await expect(page.getByLabel("售价比较开始日期")).toHaveValue("2026-08-01");
  await expect(page.getByLabel("售价比较结束日期")).toHaveValue("2026-08-31");
  await page.getByRole("button", { name: "比较售价变化", exact: true }).click();
  const result = page.getByRole("article", { name: "售价变化结果" });
  await expect(result).toContainText("品种售价变化");
  return result;
}

test("售价比较可切换基准、搜索、下钻客户和核对证据", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const result = await run(page);
  await expect(result.locator("tbody tr")).toHaveCount(1);
  await expect(result.locator("tbody")).toContainText("1.0000 → 1.0000");
  await result.getByRole("button", { name: "较去年同期", exact: true }).click();
  await expect(result).toContainText("2025-08-01");
  await result.getByRole("button", { name: "较上期", exact: true }).click();
  await result.getByLabel("搜索售价结果").fill("没有这个品种");
  await expect(result).toContainText("当前筛选没有明细");
  await result
    .getByRole("button", { name: "查看全部方向", exact: true })
    .click();
  await result.screenshot({ path: "../.local/price-analysis-desktop.png" });
  await result
    .getByRole("button", { name: "查看客户售价", exact: true })
    .click();
  await expect(result).toContainText("客户售价变化");
  await expect(result.locator("tbody tr")).toHaveCount(3);
  await expect(result).toContainText("比较期无同规格出库，无法比较售价");
  await result.getByRole("button", { name: /均价持平/ }).click();
  await expect(result.locator("tbody tr")).toHaveCount(2);
  await result
    .getByRole("button", { name: "查看出库证据", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "出库证据" });
  await expect(dialog).toContainText("共1条");
  await page.keyboard.press("Escape");
  const pending = page.waitForEvent("download");
  await result
    .getByRole("button", { name: "下载完整变化明细", exact: true })
    .click();
  expect((await pending).suggestedFilename()).toBe(
    "售价变化完整明细-2026-08-01.json",
  );
  await result
    .getByRole("button", { name: "返回该品种售价", exact: true })
    .click();
  await expect(result).toContainText("品种售价变化");
  expect(errors).toEqual([]);
});

test("手机售价页可用键盘筛选，表格在容器内滚动", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const result = await run(page);
  const filter = result.getByRole("button", { name: /均价持平/ });
  await filter.focus();
  await page.keyboard.press("Enter");
  await expect(filter).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/price-analysis-mobile.png",
    fullPage: true,
  });
});

test("缺失历史给出错误且不替换日期，切换主题保留售价结果", async ({ page }) => {
  const result = await run(page);
  const nav = page.getByRole("navigation", { name: "AI 数据分析子栏目" });
  await nav.getByRole("button", { name: "品种增长", exact: true }).click();
  await expect(result).toBeHidden();
  await nav.getByRole("button", { name: "价格变化", exact: true }).click();
  await expect(result).toBeVisible();
  await page.getByLabel("售价比较开始日期").fill("2026-10-01");
  await page.getByLabel("售价比较结束日期").fill("2026-10-31");
  await page.getByRole("button", { name: "比较售价变化", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("完整出库数据未覆盖");
  await expect(page.getByLabel("售价比较开始日期")).toHaveValue("2026-10-01");
});
