import { test, expect, type Page } from "@playwright/test";

async function open(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page.getByRole("button", { name: "进入旺季备货", exact: true }).click();
  await page.getByLabel("备货开始日期").fill("2026-09-17");
  await page.getByLabel("备货结束日期").fill("2026-09-23");
  await page.getByRole("button", { name: "查看备货依据", exact: true }).click();
  const result = page.getByRole("article", { name: "旺季备货结果" });
  await expect(result).toBeVisible();
  await result
    .getByRole("button", { name: "填写备货条件", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "品种备货情景", exact: true }),
  ).toBeFocused();
  return result;
}

test("备货按具体规格手填条件，晚到在途不减缺口，编辑后旧结果失效", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  const result = await open(page);
  await expect(page.getByLabel("在途数量")).toHaveValue("");
  await expect(page.getByLabel("供货周期")).toHaveValue("");
  await page.getByLabel("在途数量").fill("10");
  await page.getByLabel("供货周期").fill("3");
  await page.getByLabel("预计到货日").fill("2026-09-17");
  await page.getByRole("button", { name: "计算备货情景", exact: true }).click();
  const scenario = page.getByRole("region", {
    name: "备货测算结果",
    exact: true,
  });
  await expect(scenario).toContainText("31.00");
  await expect(scenario).toContainText("2026-09-14");
  await result.screenshot({ path: "../.local/stocking-desktop.png" });
  await page.getByLabel("预计到货日").fill("2026-09-18");
  await expect(scenario).toHaveCount(0);
  await page.getByRole("button", { name: "计算备货情景", exact: true }).click();
  await expect(scenario).toContainText("41.00");
  await expect(scenario).toContainText("晚到在途 10.00");
  const pending = page.waitForEvent("download");
  await result
    .getByRole("button", { name: "下载完整备货依据", exact: true })
    .click();
  expect((await pending).suggestedFilename()).toBe(
    "备货完整依据-2026-09-17.json",
  );
});

test("手机备货情景可填且财务主题明确暂缓", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  await page.getByLabel("在途数量").fill("0");
  await page.getByLabel("供货周期").fill("0");
  await page.getByLabel("目标需求").fill("20");
  await page.getByRole("button", { name: "计算备货情景", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "备货测算结果", exact: true }),
  ).toContainText("手动填写");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/stocking-mobile.png",
    fullPage: true,
  });
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "资金收益", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "资金收益展示方案" }),
  ).toContainText("资金收益暂缓开发");
});
