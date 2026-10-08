import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { submitSummary } from "@/lib/teacher";
import { bundle, bundleScripts, scriptItem } from "@/test/data";
import { mockFetch, renderWithClient, setParams } from "@/test/utils";
import BundlePage from "./[id]/page";
import MyBundlesPage from "./page";

describe("submitSummary", () => {
  it("counts approvals and scripts whose marks the teacher changed", () => {
    const s = submitSummary([
      scriptItem({ status: "approved", ai_total: 4, final_total: 5 }),
      scriptItem({ id: 12, status: "approved", ai_total: 3, final_total: 3 }),
    ]);
    expect(s).toEqual({ scripts: 2, approved: 2, changedScripts: 1, aiTotal: 7, finalTotal: 8 });
  });
});

describe("My bundles", () => {
  it("shows each bundle with progress and a disabled submit until all are approved", async () => {
    mockFetch([{ path: "/my/bundles", body: [bundle()] }]);
    renderWithClient(<MyBundlesPage />);
    expect(await screen.findByText("Physics · XII-A")).toBeInTheDocument();
    expect(screen.getByText("Mid 1 · bundle B1 · max 6 marks")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Approved by you" })).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getByRole("button", { name: "Submit bundle" })).toBeDisabled();
  });

  it("shows an empty state", async () => {
    mockFetch([{ path: "/my/bundles", body: [] }]);
    renderWithClient(<MyBundlesPage />);
    expect(await screen.findByText("No bundles are assigned to you yet.")).toBeInTheDocument();
  });

  it("submits after showing a summary and reports the Excel export", async () => {
    const approved = [scriptItem({ status: "approved", ai_total: 4, final_total: 5, to_check: 0 })];
    const calls = mockFetch([
      { path: "/my/bundles", body: [bundle({ can_submit: true, approved: 1, total: 1 })] },
      { path: "/my/bundles/7", body: bundleScripts(approved) },
      { method: "POST", path: "/my/bundles/7/submit",
        body: { bundle: bundle({ status: "submitted" }), exam_completed: true, exported: true } },
    ]);
    renderWithClient(<MyBundlesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Submit bundle" }));
    const dialog = await screen.findByRole("dialog", { name: "Submit bundle B1?" });
    expect(await within(dialog).findByText("1 of 1 scripts approved")).toBeInTheDocument();
    expect(within(dialog).getByText("Total marks: 5 (AI proposed 4)")).toBeInTheDocument();
    expect(within(dialog).getByText("You changed the AI's marks on 1 script(s)")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Submit" }));
    expect(await within(dialog).findByText(/written to the Excel sheet/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/my/bundles/7/submit")).toBe(true);
  });

  it("shows why submitting failed", async () => {
    mockFetch([
      { path: "/my/bundles", body: [bundle({ can_submit: true })] },
      { path: "/my/bundles/7", body: bundleScripts([scriptItem({ status: "approved" })]) },
      { method: "POST", path: "/my/bundles/7/submit", status: 409,
        body: { detail: { message: "Approve every script before submitting." } } },
    ]);
    renderWithClient(<MyBundlesPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Submit bundle" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Submit" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Approve every script before submitting.");
  });
});

describe("Bundle page", () => {
  it("lists scripts needing attention first and can show all", async () => {
    setParams({ id: "7" });
    mockFetch([{ path: "/my/bundles/7", body: bundleScripts([
      scriptItem({ id: 11, roll_number: null }),
      scriptItem({ id: 12, roll_number: "21CS046", status: "approved", to_check: 0 }),
      scriptItem({ id: 13, roll_number: "21CS047", status: "failed", error: "quota", ai_total: null, final_total: null }),
    ]) }]);
    renderWithClient(<BundlePage />);
    expect(await screen.findByText("missing")).toBeInTheDocument();
    expect(screen.queryByText("21CS046")).not.toBeInTheDocument();
    expect(screen.getByText(/usage limit was reached/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/scripts/11");
    await userEvent.click(screen.getByRole("button", { name: "All (3)" }));
    await waitFor(() => expect(screen.getByText("21CS046")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "View" })).toHaveAttribute("href", "/scripts/12");
  });
});
