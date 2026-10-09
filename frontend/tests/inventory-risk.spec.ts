import { test, expect } from "@playwright/test";

test("库存积压按快照计算并允许调整阈值、筛选和核对批次", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page.getByRole("button", { name: "进入库存积压", exact: true }).click();
  await expect(page.getByLabel("销售观察天数")).toHaveValue("30");
  await page.getByRole("button", { name: "分析库存积压", exact: true }).click();
  const result = page.getByRole("article", { name: "库存积压结果" });
  await expect(result).toContainText("70.00");
  await expect(result.locator("tbody tr")).toHaveCount(3);
  await expect(result).toContainText("累计售完约 30.00 天");
  await page.getByLabel("库存关注项").selectOption("expired");
  await expect(result.locator("tbody tr")).toHaveCount(1);
  await expect(result.locator("tbody")).toContainText("暂不估算售完时间");
  await result
    .getByRole("button", { name: "查看批次依据", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "批次库存依据" });
  await expect(dialog).toContainText("B3");
  await page.keyboard.press("Escape");
  await page.getByLabel("库存关注项").selectOption("all");
  await result.screenshot({ path: "../.local/inventory-risk-desktop.png" });
  const pending = page.waitForEvent("download");
  await result
    .getByRole("button", { name: "下载库存完整明细", exact: true })
    .click();
  expect((await pending).suggestedFilename()).toBe(
    "库存积压完整明细-2026-09-16.json",
  );
  await page.getByLabel("库龄关注阈值").fill("150");
  await page.getByRole("button", { name: "分析库存积压", exact: true }).click();
  await expect(result).toContainText("库龄 ≥150天");
  await page.getByLabel("库存关注项").selectOption("aged");
  await expect(result).toContainText("当前已展示分组没有匹配项");
});

test("手机库存页无整体溢出且缺少销售历史时不给估算结果", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page.getByRole("button", { name: "进入库存积压", exact: true }).click();
  await page.getByRole("button", { name: "分析库存积压", exact: true }).click();
  const result = page.getByRole("article", { name: "库存积压结果" });
  await expect(result).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/inventory-risk-mobile.png",
    fullPage: true,
  });
  await page.getByLabel("销售观察天数").fill("90");
  await page.getByRole("button", { name: "分析库存积压", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("完整出库数据未覆盖");
  await expect(result).toHaveCount(0);
});
