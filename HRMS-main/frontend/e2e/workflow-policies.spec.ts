// HR policy lifecycle end to end (endpoints/hr_policies.py): MANAGER submits (SUBMITTED, hidden from others) -> ADMIN
// publishes in the UI (hr_policy_events trail) -> the audience can list/download; others get 404. Indexing uses the
// application's own mechanism (scripts/ingest_policies.py inside the E2E api container), and the Copilot's policy
// retrieval applies the same audience: proven without a model by the no-answer refusal vs. a retrieval hit.
// Synthetic policy text only.
import { apiContext, call, classify, errorCode, type ApiResult } from "./support/api";
import { contextWithToken, expectPageTitle } from "./support/session";
import { expect, test } from "./support/suite";
import { createPerson, items, reindexPolicies, runTag } from "./support/workflow";

async function listedIds(api: import("@playwright/test").APIRequestContext, token: string): Promise<number[]> {
  const ids: number[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = items(await call(api, token, "GET", `/api/v1/hr-policies?limit=100&offset=${offset}`));
    ids.push(...page.map((policy) => policy.id));
    if (page.length < 100) return ids;
  }
}

async function upload(api: import("@playwright/test").APIRequestContext, token: string, title: string, audience: string,
                      text: string, filename = `${title.replace(/\W+/g, "-").toLowerCase()}.md`): Promise<ApiResult> {
  return call(api, token, "POST", "/api/v1/hr-policies/upload", { multipart: {
    title, category: "E2E", audience, file: { name: filename, mimeType: "text/markdown", buffer: Buffer.from(text) } } });
}

test.describe("HR policy lifecycle", () => {
  test("POL-A manager submits in the UI, ADMIN publishes in the UI, the audience gets access, the trail records both",
    async ({ browser, tokens, data }) => {
      const api = await apiContext();
      const tag = runTag();
      const filename = `e2e-managers-policy-${tag}.md`;
      const manager = await (await contextWithToken(browser, tokens.MANAGER, "MANAGER")).newPage();
      await manager.goto("/hr-policies");
      await expectPageTitle(manager, "HR Policies");
      await manager.getByPlaceholder("Policy title").fill(`E2E Managers Policy ${tag}`);
      await manager.getByPlaceholder("Category (e.g. LEAVE, SECURITY, BENEFITS)").fill("E2E");
      await manager.locator("input[type=file]").setInputFiles({ name: filename, mimeType: "text/markdown",
        buffer: Buffer.from(`# E2E Managers Policy ${tag}\n\nSynthetic guidance for managers only.`) });
      await manager.getByRole("combobox", { name: "Readers" }).selectOption("MANAGERS");
      const uploaded = manager.waitForResponse((response) => response.url().endsWith("/api/v1/hr-policies/upload"));
      await manager.getByRole("button", { name: "Upload Policy" }).click();
      const body = await (await uploaded).json();
      const id: number = body.data.id;
      expect([body.data.status, body.data.audience]).toEqual(["SUBMITTED", "MANAGERS"]);
      await expect(manager.getByText(filename)).toBeVisible();
      await expect(manager.getByText("Awaiting admin review").first()).toBeVisible();

      // Until published: invisible to employees and to other managers, not downloadable.
      const otherManager = await createPerson(api, tokens.ADMIN, "MANAGER", "PolicyManager");
      expect(await listedIds(api, tokens.EMPLOYEE)).not.toContain(id);
      expect(await listedIds(api, otherManager.token)).not.toContain(id);
      expect(classify((await call(api, otherManager.token, "GET", `/api/v1/hr-policies/${id}/download`)).status)).toBe("hidden");
      expect(classify((await call(api, tokens.MANAGER, "POST", `/api/v1/hr-policies/${id}/publish`)).status)).toBe("deny");

      const admin = await (await contextWithToken(browser, tokens.ADMIN, "ADMIN")).newPage();
      await admin.goto("/hr-policies");
      await expectPageTitle(admin, "HR Policies");
      const entry = admin.locator("li, article, div.rounded-md").filter({ hasText: filename }).filter({
        has: admin.getByRole("button", { name: "Publish" }) }).last();
      await entry.getByRole("button", { name: "Publish" }).click();
      await expect.poll(async () => items(await call(api, tokens.ADMIN, "GET", "/api/v1/hr-policies?limit=100&status=PUBLISHED"))
        .concat(items(await call(api, tokens.ADMIN, "GET", "/api/v1/hr-policies?limit=100&offset=100&status=PUBLISHED")))
        .some((policy) => policy.id === id)).toBe(true);

      const events = (await call(api, tokens.ADMIN, "GET", `/api/v1/hr-policies/${id}/events`)).body.data;
      expect(events.map((event: any) => [event.to_status, event.actor_employee_id]))
        .toEqual([["SUBMITTED", data.ids.manager], ["PUBLISHED", data.ids.admin]]);
      expect(classify((await call(api, tokens.MANAGER, "GET", `/api/v1/hr-policies/${id}/events`)).status)).toBe("deny");

      // Published to MANAGERS: managers (including the unrelated one) and ADMIN read it; EMPLOYEE gets 404.
      expect(await listedIds(api, otherManager.token)).toContain(id);
      expect({
        manager: classify((await call(api, tokens.MANAGER, "GET", `/api/v1/hr-policies/${id}/download`)).status),
        otherManager: classify((await call(api, otherManager.token, "GET", `/api/v1/hr-policies/${id}/download`)).status),
        admin: classify((await call(api, tokens.ADMIN, "GET", `/api/v1/hr-policies/${id}/download`)).status),
        employee: classify((await call(api, tokens.EMPLOYEE, "GET", `/api/v1/hr-policies/${id}/download`)).status),
      }).toEqual({ manager: "ok", otherManager: "ok", admin: "ok", employee: "hidden" });
      expect(await listedIds(api, tokens.EMPLOYEE)).not.toContain(id);
      await api.dispose();
    });

  test("POL-B indexing, Copilot audience filter and a new version (model-free evidence)", async ({ tokens }) => {
    const api = await apiContext();
    const tag = runTag();
    const title = `E2E Quokka Allowance ${tag}`;
    const ask = async (token: string, question: string) => call(api, token, "POST", "/api/v1/chat/policy", { data: { question } });
    const first = await upload(api, tokens.ADMIN, title, "ADMINS",
      `# ${title}\n\nZephyrine quokka allowance: every quokka handler receives seven zephyr tokens per month.`);
    expect(first.status).toBe(200);
    expect(first.body.data.status).toBe("PUBLISHED");
    const question = "What is the zephyrine quokka allowance for quokka handlers?";

    const indexed = reindexPolicies();
    test.skip(indexed === null, "NOT_TESTED: could not run scripts/ingest_policies.py in the E2E api container");
    // EMPLOYEE: the ADMINS-only document is filtered out before scoring -> the model-free no-answer refusal.
    const employeeAnswer = await ask(tokens.EMPLOYEE, question);
    expect([employeeAnswer.status, employeeAnswer.body?.data?.grounded, employeeAnswer.body?.data?.sources?.length ?? 0])
      .toEqual([200, false, 0]);
    // ADMIN: retrieval finds it, so the pipeline reaches the model, which is the rule-less fake here -> AI_INVALID_OUTPUT.
    const adminAnswer = await ask(tokens.ADMIN, question);
    expect([adminAnswer.status, errorCode(adminAnswer)]).toEqual([502, "AI_INVALID_OUTPUT"]);

    // New version (same title): the old one is archived; after re-indexing the old wording no longer retrieves.
    const second = await upload(api, tokens.ADMIN, title, "ADMINS",
      `# ${title}\n\nXylophonic marmot stipend: every marmot keeper receives three xylo credits per quarter.`);
    expect(second.status).toBe(200);
    const trail = (await call(api, tokens.ADMIN, "GET", `/api/v1/hr-policies/${first.body.data.id}/events`)).body.data;
    expect(trail.at(-1)).toMatchObject({ action: "ARCHIVED", to_status: "ARCHIVED" }); // superseded by the new version
    expect(second.body.data.status).toBe("PUBLISHED");
    expect(reindexPolicies()).not.toBeNull();
    const oldWording = await ask(tokens.ADMIN, question);
    expect([oldWording.status, oldWording.body?.data?.grounded]).toEqual([200, false]);
    const newWording = await ask(tokens.ADMIN, "What is the xylophonic marmot stipend for marmot keepers?");
    expect([newWording.status, errorCode(newWording)]).toEqual([502, "AI_INVALID_OUTPUT"]);
    await api.dispose();
  });

  test("POL-C malformed uploads are refused and leave no policy behind", async ({ tokens }) => {
    const api = await apiContext();
    const before = (await listedIds(api, tokens.ADMIN)).length;
    const attempts = {
      executable: await call(api, tokens.ADMIN, "POST", "/api/v1/hr-policies/upload", { multipart: {
        title: "E2E Executable", category: "E2E", file: { name: "tool.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") } } }),
      empty: await call(api, tokens.ADMIN, "POST", "/api/v1/hr-policies/upload", { multipart: {
        title: "E2E Empty", category: "E2E", file: { name: "empty.md", mimeType: "text/markdown", buffer: Buffer.alloc(0) } } }),
      noTitle: await call(api, tokens.ADMIN, "POST", "/api/v1/hr-policies/upload", { multipart: {
        category: "E2E", file: { name: "untitled.md", mimeType: "text/markdown", buffer: Buffer.from("# x\n\ny") } } }),
      badAudience: await call(api, tokens.ADMIN, "POST", "/api/v1/hr-policies/upload", { multipart: {
        title: "E2E Bad Audience", category: "E2E", audience: "EVERYBODY", file: { name: "a.md", mimeType: "text/markdown", buffer: Buffer.from("# a\n\nb") } } }),
      notUtf8: await call(api, tokens.ADMIN, "POST", "/api/v1/hr-policies/upload", { multipart: {
        title: "E2E Binary Text", category: "E2E", file: { name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from([0xff, 0xfe, 0xfd]) } } }),
    };
    expect(Object.fromEntries(Object.entries(attempts).map(([key, result]) => [key, classify(result.status)]))).toEqual(
      { executable: "rejected", empty: "rejected", noTitle: "rejected", badAudience: "rejected", notUtf8: "rejected" });
    expect(errorCode(attempts.badAudience)).toBe("INVALID_POLICY_AUDIENCE");
    expect((await listedIds(api, tokens.ADMIN)).length).toBe(before);
    await api.dispose();
  });
});
