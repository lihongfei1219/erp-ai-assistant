import { test, expect, type Page } from "@playwright/test";

async function enter(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  return page.getByRole("navigation", { name: "AI 数据分析子栏目" });
}

test("六个经营主题属于AI数据分析，规划页不执行查询", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const requests: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") requests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  const nav = await enter(page);
  await expect(page.locator(".rangebar")).toContainText("公司整体（全平台）");
  await expect(page.locator(".rangebar")).toContainText(
    "分析期间以下方查询条件为准",
  );
  await expect(page.locator(".rangebar")).toContainText("备份截至");
  await expect(page.locator(".rangebar")).not.toContainText("2026-09-01");
  await expect(page.locator(".analysis-topic-card")).toHaveCount(6);
  await expect(
    page.getByRole("heading", { name: "AI 数据分析", exact: true }),
  ).toBeVisible();
  await page
    .locator(".analytics-hub")
    .screenshot({ path: "../.local/analytics-hub-desktop.png" });
  const cases = [
    ["资金收益", "算上利息，还划不划算？", "企业确认的利率与计息规则"],
  ];
  for (const [title, question, data] of cases) {
    await nav.getByRole("button", { name: title, exact: true }).click();
    await expect(
      nav.getByRole("button", { name: title, exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.getByRole("heading", { name: question, exact: true }),
    ).toBeVisible();
    const design = page.getByRole("region", { name: `${title}展示方案` });
    await expect(design).toContainText("资金收益暂缓开发");
    await expect(design).toContainText(data);
    await expect(
      page.getByRole("button", { name: "运行分析", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByTestId("analysis-results")).toHaveCount(0);
    await design.getByText("计算前需要明确的口径", { exact: true }).click();
    if (title === "资金收益")
      await expect(design).toContainText("不等同于净利润");
  }
  await page
    .locator(".analytics-hub")
    .screenshot({ path: "../.local/analytics-capital-design.png" });
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});

test("切换主题保留品种结果与自由问数草稿，页面之间不混入结果", async ({
  page,
}) => {
  const nav = await enter(page);
  await page.getByRole("button", { name: "进入品种增长", exact: true }).click();
  await page.getByRole("button", { name: "比较品种变化", exact: true }).click();
  const growth = page.getByTestId("topic-growth");
  await expect(growth.getByTestId("analysis-results")).toBeVisible();
  await expect(growth.getByTestId("analysis-results")).toContainText(
    "2026-08-01",
  );
  await expect(page.locator(".rangebar")).not.toContainText("2026-09-01");
  await nav.getByRole("button", { name: "自由问数", exact: true }).click();
  await page
    .getByLabel("分析问题", { exact: true })
    .fill("继续比较指定客户的变化");
  await expect(growth).toBeHidden();
  await nav.getByRole("button", { name: "库存积压", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "哪些货压得久？", exact: true }),
  ).toBeVisible();
  await expect(growth.getByTestId("analysis-results")).toBeHidden();
  await nav.getByRole("button", { name: "品种增长", exact: true }).click();
  await expect(growth.getByTestId("analysis-results")).toBeVisible();
  await nav.getByRole("button", { name: "自由问数", exact: true }).click();
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "继续比较指定客户的变化",
  );
});

test("手机主题总览与规划页可操作且无横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const nav = await enter(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../.local/analytics-hub-mobile.png",
    fullPage: true,
  });
  const button = nav.getByRole("button", { name: "旺季备货", exact: true });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "旺季前要不要备货？", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await nav.getByRole("button", { name: "主题总览", exact: true }).click();
  await page.getByRole("button", { name: "打开自由问数", exact: true }).click();
  await expect(page.getByLabel("分析问题", { exact: true })).toBeVisible();
});
