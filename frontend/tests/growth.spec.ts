import { test, expect } from "@playwright/test";

test("品种比较可以追溯客户、切换证据期间并下载完整明细", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "品种增长", exact: true })
    .click();
  await expect(page.getByLabel("品种比较开始日期")).toHaveValue("2026-08-01");
  await expect(page.getByLabel("品种比较结束日期")).toHaveValue("2026-08-31");
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  const output = page.getByTestId("analysis-results");
  await expect(output).toContainText("品种增长与下降");
  await expect(output.getByLabel("变化概览")).toContainText("+60.00");
  await output
    .getByRole("button", { name: "比较去年同期", exact: true })
    .click();
  await expect(output.getByLabel("变化概览")).toContainText("-40.00");
  await output.getByRole("button", { name: "比较上期", exact: true }).click();
  await output.getByLabel("增长贡献榜").getByRole("button").first().click();
  await output.getByRole("button", { name: "查看出库证据" }).first().click();
  const evidence = page.getByRole("dialog", { name: "出库证据" });
  await expect(evidence).toContainText("共3条");
  await page.getByLabel("证据期间").selectOption("previous");
  await expect(evidence).toContainText("共2条");
  await page.keyboard.press("Escape");
  await expect(evidence).toHaveCount(0);
  await output.getByRole("button", { name: "查看客户贡献" }).first().click();
  await expect(output).toContainText("品种内客户贡献");
  await output
    .getByLabel("增长贡献榜")
    .getByRole("button")
    .filter({ hasText: "C" })
    .click();
  await expect(output).toContainText("占增加部分");
  await expect(output).toContainText("比较期无出库");
  const download = page.waitForEvent("download");
  await output.getByRole("button", { name: "下载完整变化明细" }).click();
  expect((await download).suggestedFilename()).toBe(
    "品种变化完整明细-2026-08-01.json",
  );
});

test("榜内搜索、键盘选中与精确字段展开不改变汇总", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "品种增长", exact: true })
    .click();
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  const output = page.getByTestId("analysis-results");
  await expect(output.getByLabel("变化概览")).toContainText("160.00");
  await page.getByLabel("搜索当前榜单").fill("不存在的品种");
  await expect(output.getByLabel("增长贡献榜")).toContainText("没有匹配项");
  await expect(output.getByLabel("变化概览")).toContainText("160.00");
  await page.getByLabel("搜索当前榜单").fill("P");
  const row = output.getByLabel("增长贡献榜").getByRole("button").first();
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(output.getByLabel("选中条目详情")).toBeFocused();
  await expect(output.getByLabel("选中条目详情")).toContainText("100.00%");
  await output.getByText("展开完整字段与精确数值", { exact: false }).click();
  await expect(output.locator("table")).toBeVisible();
  await expect(output.locator("table")).toContainText("60.0000");
  await output
    .getByRole("button", { name: "比较去年同期", exact: true })
    .click();
  await expect(
    output.getByRole("button", { name: "查看客户贡献" }),
  ).toHaveCount(0);
});

test("手机下结果卡与证据弹窗保持在视口内", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "品种增长", exact: true })
    .click();
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  const output = page.getByTestId("analysis-results");
  await expect(output.getByLabel("变化概览")).toContainText("160.00");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await output
    .locator(".growth-result")
    .screenshot({ path: "../.local/growth-ui-mobile.png" });
  await output.getByLabel("增长贡献榜").getByRole("button").first().click();
  await output.getByRole("button", { name: "查看出库证据" }).click();
  const dialog = page.getByRole("dialog", { name: "出库证据" });
  await expect(dialog).toContainText("共3条");
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
});

test("数量结果按单位切换，汇总和条目同步", async ({ page }) => {
  await page.route("**/api/v1/analysis/run", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const result = body.results[0];
    // Add a second synthetic unit with a distinct total to detect mixed-unit displays.
    result.totals.groups.push(
      ...result.totals.groups.map((group: Record<string, unknown>) => ({
        ...group,
        unit: "瓶",
        current_quantity: "7.0000",
        previous_quantity: "2.0000",
        delta: "5.0000",
        positive: "5.0000",
        negative: "0.0000",
      })),
    );
    result.rows.push(
      ...result.rows
        .filter((row: Record<string, unknown>) => row.basis === "previous")
        .map((row: Record<string, unknown>) => ({
          ...row,
          unit: "瓶",
          name: "合成瓶装商品",
          code: "BOTTLE",
          current_quantity: "7.0000",
          previous_quantity: "2.0000",
          delta: "5.0000",
          change_percent: "250.00%",
        })),
    );
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "品种增长", exact: true })
    .click();
  await page.getByLabel("品种比较指标").selectOption("quantity");
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  const output = page.getByTestId("analysis-results");
  await expect(output.getByLabel("变化概览")).toContainText("160.00");
  await page.getByLabel("结果数量单位").selectOption("瓶");
  await expect(output.getByLabel("变化概览")).toContainText("7.00");
  await expect(output.getByLabel("变化概览")).not.toContainText("160.00");
  await expect(output.getByLabel("增长贡献榜")).toContainText("合成瓶装商品");
  await expect(output.getByLabel("增长贡献榜")).toContainText("+5.00");
});

test("缺失去年同期不能变成零出库", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await page
    .getByRole("navigation", { name: "AI 数据分析子栏目" })
    .getByRole("button", { name: "品种增长", exact: true })
    .click();
  await page.getByLabel("品种比较开始日期").fill("2026-07-01");
  await page.getByLabel("品种比较结束日期").fill("2026-07-31");
  await page.getByLabel("品种比较基准").selectOption("year_over_year");
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("完整出库数据未覆盖");
  await expect(page.getByTestId("analysis-results")).toHaveCount(0);
});
