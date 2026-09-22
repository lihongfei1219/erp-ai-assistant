import { test, expect, type Page } from "@playwright/test";

const pending = {
  status: "needs_input",
  understood_summary: "查看商品销售排行，按销售金额统计。",
  draft: {
    intents: [
      {
        id: "goal-1",
        label: "商品销售排行",
        fields: {
          domain: "sales",
          operation: "ranking",
          metric: "amount",
          order: "descending",
        },
        constraints: [{ id: "filter-1", label: "仅指定商品" }],
        field_sources: {
          domain: "explicit",
          operation: "explicit",
          metric: "default",
        },
      },
    ],
  },
  clarification: {
    id: "issue-date",
    intent_id: "goal-1",
    field: "time",
    kind: "missing",
    question: "你想查看哪段时间？",
  },
  choices: [
    {
      id: "choose-dates",
      label: "选择日期",
      action: { kind: "date_range", intent_id: "goal-1", field: "time" },
    },
  ],
  applied_defaults: ["未指定指标，先按销售金额统计。"],
  allow_free_text: true,
  conversation_token: "pending-token-1",
  result: null,
  repeated_clarification: false,
  available_dates: { start: "2026-09-01", end_exclusive: "2026-09-16" },
};

async function enter(page: Page) {
  await page.route("**/api/v1/analysis/catalog", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), model_enabled: true },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "AI 数据分析", exact: true }).click();
  await expect(page.getByLabel("开始日期", { exact: true })).toHaveValue(
    "2026-09-01",
  );
}

async function ask(page: Page, question: string) {
  await page.getByLabel("分析问题", { exact: true }).fill(question);
  await page.getByRole("button", { name: "开始智能分析" }).click();
}

test("guidance shows known conditions without an error and free replies keep current state", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        ...pending,
        conversation_token: `pending-token-${bodies.length}`,
      },
    });
  });
  await enter(page);
  await ask(page, "想知道哪些商品卖得比较好");
  const guide = page.getByRole("region", { name: "分析引导" });
  await expect(guide).toContainText("查看商品销售排行");
  await expect(guide).toContainText("你想查看哪段时间");
  await expect(guide).toContainText("先按销售金额");
  await expect(guide).toContainText("从高到低");
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "想知道哪些商品卖得比较好",
  );
  await expect(page.getByRole("alert")).toHaveCount(0);
  await guide.getByRole("button", { name: "修改条件：仅指定商品" }).click();
  await expect(page.getByLabel("分析问题", { exact: true })).toBeFocused();
  await ask(page, "改成九月五号，取消商品限制");
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual({
    request_id: expect.any(String),
    question: "改成九月五号，取消商品限制",
    conversation_token: "pending-token-1",
  });
  await page.getByRole("button", { name: "清除追问上下文" }).click();
  await expect(guide).toHaveCount(0);
  await ask(page, "看库存");
  await expect.poll(() => bodies.length).toBe(3);
  expect(bodies[2].conversation_token).toBeNull();
});

test("date selection sends the signed choice and exact exclusive end without rewriting the question", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({ json: pending });
  });
  await enter(page);
  await ask(page, "看商品排行");
  await page.getByRole("button", { name: "选择日期", exact: true }).click();
  await page.getByLabel("引导开始日期").fill("2026-09-05");
  await page.getByLabel("引导结束日期").fill("2026-09-07");
  await page.getByRole("button", { name: "应用日期" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual({
    request_id: expect.any(String),
    choice_id: "choose-dates",
    date_range: { start: "2026-09-05", end_exclusive: "2026-09-08" },
    conversation_token: "pending-token-1",
  });
});

test("expired context preserves the visible draft and recovers only after explicit action", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 2) {
      await route.fulfill({
        status: 409,
        json: {
          detail: {
            code: "context_expired",
            message: "会话已过期，请确认后恢复当前草稿。",
          },
        },
      });
    } else await route.fulfill({ json: pending });
  });
  await enter(page);
  await ask(page, "看商品排行");
  await ask(page, "九月五号");
  await expect(page.getByRole("region", { name: "分析引导" })).toContainText(
    "查看商品销售排行",
  );
  await expect(
    page.getByRole("button", { name: "恢复这份草稿" }),
  ).toBeVisible();
  expect(bodies).toHaveLength(2);
  await page.getByRole("button", { name: "恢复这份草稿" }).click();
  await expect.poll(() => bodies.length).toBe(3);
  expect(bodies[2].conversation_token).toBeNull();
  expect(bodies[2].question).toContain("查看商品销售排行");
  expect(bodies[2].question).toContain("仅指定商品");
  expect(bodies[2].question).toContain("九月五号");
});

test("cloud failure keeps input for retry and never renders a guidance card as an alert", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1)
      await route.fulfill({
        status: 503,
        json: { detail: "云端语义服务繁忙，请稍后重试。" },
      });
    else await route.fulfill({ json: pending });
  });
  await enter(page);
  await ask(page, "查看商品表现");
  await expect(page.getByRole("alert")).toContainText("云端语义服务繁忙");
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "查看商品表现",
  );
  await page.getByRole("button", { name: "重试本次请求" }).click();
  await expect(page.getByRole("region", { name: "分析引导" })).toBeVisible();
  expect(bodies[1].request_id).not.toBe(bodies[0].request_id);
  expect({ ...bodies[1], request_id: null }).toEqual({
    ...bodies[0],
    request_id: null,
  });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("分析问题", { exact: true })).toHaveValue(
    "查看商品表现",
  );
});

test("network failure retries the same request identity", async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) await route.abort("failed");
    else await route.fulfill({ json: pending });
  });
  await enter(page);
  await ask(page, "查看商品表现");
  await page.getByRole("button", { name: "重试本次请求" }).click();
  await expect(page.getByRole("region", { name: "分析引导" })).toBeVisible();
  expect(bodies).toHaveLength(2);
  expect(bodies[0].request_id).toBeTruthy();
  expect(bodies[1]).toEqual(bodies[0]);
});

test("recovering an expired date choice retains the user's selected range", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 2)
      await route.fulfill({
        status: 409,
        json: { detail: { code: "context_expired", message: "会话已过期。" } },
      });
    else await route.fulfill({ json: pending });
  });
  await enter(page);
  await ask(page, "看商品排行");
  await page.getByRole("button", { name: "选择日期", exact: true }).click();
  await page.getByLabel("引导开始日期").fill("2026-09-05");
  await page.getByLabel("引导结束日期").fill("2026-09-07");
  await page.getByRole("button", { name: "应用日期" }).click();
  await page.getByRole("button", { name: "恢复这份草稿" }).click();
  await expect.poll(() => bodies.length).toBe(3);
  expect(bodies[2].conversation_token).toBeNull();
  expect(bodies[2].question).toContain("2026-09-05");
  expect(bodies[2].question).toContain("2026-09-07");
});

test("current alternative submits its choice id while stale replies cannot restore cleared context", async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  let release: () => void = () => {};
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const alternative = {
    ...pending,
    status: "capability_gap",
    choices: [
      {
        id: "metric-orders",
        label: "改看订单数",
        action: { kind: "set_field", intent_id: "goal-1", field: "metric" },
      },
    ],
  };
  await page.route("**/api/v1/analysis/converse", async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 2) await delayed;
    await route.fulfill({ json: alternative });
  });
  await enter(page);
  await ask(page, "看商品销量");
  await page.getByRole("button", { name: "改看订单数" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual({
    request_id: expect.any(String),
    choice_id: "metric-orders",
    conversation_token: "pending-token-1",
  });
  await page.getByRole("button", { name: "清除追问上下文" }).click();
  release();
  await expect(page.getByRole("region", { name: "分析引导" })).toHaveCount(0);
  await expect(page.getByLabel("分析问题", { exact: true })).toBeEnabled();
  await ask(page, "重新看看销售");
  await expect.poll(() => bodies.length).toBe(3);
  expect(bodies[2].conversation_token).toBeNull();
});

test("mobile guidance keeps suggestions and known conditions within the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/v1/analysis/converse", (route) =>
    route.fulfill({ json: { ...pending, repeated_clarification: true } }),
  );
  await enter(page);
  await ask(page, "想看看哪些商品值得关注");
  const guide = page.getByRole("region", { name: "分析引导" });
  await expect(guide).toContainText("已有条件已保留");
  await expect(
    guide.getByRole("button", { name: "选择日期", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(391);
  await guide.screenshot({ path: "../.local/analysis-guidance-mobile.png" });
});
