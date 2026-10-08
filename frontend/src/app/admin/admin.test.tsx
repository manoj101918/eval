import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { BundleSummary, ExamDetail, UserOut } from "@/lib/admin";
import { mockFetch, renderWithClient, routerMock, setParams } from "@/test/utils";
import ExamPage from "./exams/[id]/page";
import NewExamPage from "./exams/new/page";
import TeachersPage from "./teachers/page";

const users: UserOut[] = [
  { id: 1, employee_id: "ADM1", name: "Exam Cell", role: "admin", active: true, must_change_password: false },
  { id: 2, employee_id: "T100", name: "Asha", role: "teacher", active: true, must_change_password: false },
  { id: 3, employee_id: "T200", name: "Ravi", role: "teacher", active: false, must_change_password: false },
];

const counts = { queued: 0, processing: 0, graded: 0, failed: 0, approved: 0 };

function bundleSummary(over: Partial<BundleSummary> = {}): BundleSummary {
  return { id: 5, code: "B1", teacher: { employee_id: "T100", name: "Asha" }, status: "ready",
    counts: { ...counts, graded: 2 }, total: 2, ...over };
}

function examDetail(over: Partial<ExamDetail> = {}): ExamDetail {
  return { id: 3, name: "Mid 1", subject: "Physics", class_section: "XII-A", max_marks: 4, status: "in_review",
    bundles: 1, scripts: 2, questions: [{ question: "1", max_marks: 1, qtype: "mcq", choice_group: null }],
    bundle_list: [bundleSummary()], exported_at: null, export_error: null, ...over };
}

describe("New exam", () => {
  it("lists marking-scheme row problems from the server", async () => {
    mockFetch([{ method: "POST", path: "/admin/exams", status: 422, body: { detail: {
      message: "The marking scheme has problems.", problems: ["row 2 (5): Max marks must be a number"] } } }]);
    renderWithClient(<NewExamPage />);
    await userEvent.type(screen.getByLabelText("Subject"), "Physics");
    await userEvent.type(screen.getByLabelText("Exam"), "Mid 1");
    await userEvent.type(screen.getByLabelText("Class / section"), "XII-A");
    await userEvent.upload(screen.getByLabelText("Marking scheme file"),
      new File(["x"], "scheme.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    await userEvent.click(screen.getByRole("button", { name: "Create exam" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The marking scheme has problems.");
    expect(alert).toHaveTextContent("row 2 (5): Max marks must be a number");
    expect(routerMock.push).not.toHaveBeenCalled();
  });

  it("offers the blank template", () => {
    mockFetch([]);
    renderWithClient(<NewExamPage />);
    expect(screen.getByRole("link", { name: /blank marking-scheme template/ }))
      .toHaveAttribute("href", "/api/admin/scheme-template");
  });
});

describe("Teachers", () => {
  it("shows a new teacher's one-time password once", async () => {
    const calls = mockFetch([
      { path: "/admin/users", body: users },
      { method: "POST", path: "/admin/users", status: 201, body: {
        user: { ...users[1], id: 9, employee_id: "T300", name: "Meena", must_change_password: true },
        temporary_password: "Xy7kPq2mWn4z" } },
    ]);
    renderWithClient(<TeachersPage />);
    await userEvent.type(await screen.findByLabelText("Employee ID"), "T300");
    await userEvent.type(screen.getByLabelText("Name"), "Meena");
    await userEvent.click(screen.getByRole("button", { name: "Add teacher" }));
    const dialog = await screen.findByRole("dialog", { name: "One-time password for T300" });
    expect(within(dialog).getByTestId("one-time-password")).toHaveTextContent("Xy7kPq2mWn4z");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ employee_id: "T300", name: "Meena" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("one-time-password")).not.toBeInTheDocument();
  });

  it("resets a password and deactivates a teacher", async () => {
    const calls = mockFetch([
      { path: "/admin/users", body: users },
      { method: "POST", path: "/admin/users/2/reset-password", body: { temporary_password: "NewPass12345" } },
      { method: "POST", path: "/admin/users/2/active?active=false", body: { ...users[1], active: false } },
    ]);
    renderWithClient(<TeachersPage />);
    const row = (await screen.findByText("T100")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Reset password" }));
    expect(await screen.findByTestId("one-time-password")).toHaveTextContent("NewPass12345");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await userEvent.click(within(row).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/admin/users/2/active?active=false")).toBe(true));
  });
});

describe("Exam page", () => {
  beforeEach(() => setParams({ id: "3" }));

  it("uploads PDFs to a bundle and reports rejected files", async () => {
    const calls = mockFetch([
      { path: "/admin/exams/3", body: examDetail() },
      { path: "/admin/users", body: users },
      { method: "POST", path: "/admin/bundles/5/scripts", body: {
        accepted: [{ id: 1, filename: "a.pdf", status: "queued", error: null, roll_number: null }],
        rejected: [{ filename: "notes.pdf", reason: "wrong file type" }] } },
    ]);
    renderWithClient(<ExamPage />);
    const input = await screen.findByLabelText("Upload PDFs for bundle 5");
    await userEvent.upload(input, [
      new File(["%PDF-"], "a.pdf", { type: "application/pdf" }),
      new File(["x"], "notes.pdf", { type: "application/pdf" }),
    ]);
    expect(await screen.findByText("1 uploaded; the AI is grading them.")).toBeInTheDocument();
    expect(screen.getByText("notes.pdf: wrong file type")).toBeInTheDocument();
    const form = calls.find((c) => c.method === "POST")?.body as FormData;
    expect(form.getAll("files")).toHaveLength(2);
  });

  it("only offers active teachers for assignment", async () => {
    mockFetch([{ path: "/admin/exams/3", body: examDetail() }, { path: "/admin/users", body: users }]);
    renderWithClient(<ExamPage />);
    const select = await screen.findByLabelText("Teacher for bundle B1");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    expect(within(select).queryByText(/Ravi/)).not.toBeInTheDocument();
  });

  it("shows why an export is blocked", async () => {
    mockFetch([
      { path: "/admin/exams/3", body: examDetail() },
      { path: "/admin/users", body: users },
      { method: "POST", path: "/admin/exams/3/export", status: 409, body: { detail: {
        message: "The marks cannot be exported yet.", problems: ["Bundle B1 is not submitted yet."] } } },
    ]);
    renderWithClient(<ExamPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Export marks" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Bundle B1 is not submitted yet.");
  });

  it("links to the exported mark sheet", async () => {
    mockFetch([
      { path: "/admin/exams/3", body: examDetail({ status: "exported", exported_at: "2026-10-08T10:00:00Z",
        bundle_list: [bundleSummary({ status: "submitted" })] }) },
      { path: "/admin/users", body: users },
    ]);
    renderWithClient(<ExamPage />);
    expect(await screen.findByRole("link", { name: "Download mark sheet" }))
      .toHaveAttribute("href", "/api/admin/exams/3/export/file");
    expect(screen.queryByLabelText("Upload PDFs for bundle 5")).not.toBeInTheDocument();
  });

  it("reopens a submitted bundle only with a reason", async () => {
    const calls = mockFetch([
      { path: "/admin/exams/3", body: examDetail({ bundle_list: [bundleSummary({ status: "submitted" })] }) },
      { path: "/admin/users", body: users },
      { method: "POST", path: "/admin/bundles/5/reopen", body: bundleSummary() },
    ]);
    renderWithClient(<ExamPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Reopen bundle" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Reopen" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Reason (recorded)"), "Q5 marked wrongly");
    await userEvent.click(confirm);
    await waitFor(() => expect(calls.find((c) => c.path === "/api/admin/bundles/5/reopen")?.body)
      .toEqual({ reason: "Q5 marked wrongly" }));
  });
});
