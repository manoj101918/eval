import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { question, script } from "@/test/data";
import { mockFetch, renderWithClient, routerMock, setParams } from "@/test/utils";
import ReviewScriptPage from "./page";

const q5 = () => screen.getByRole("generic", { name: "Question 5" });

describe("Review screen", () => {
  beforeEach(() => {
    setParams({ id: "11" });
    routerMock.push.mockReset();
  });

  it("shows flagged questions first with the AI's reasoning and page links", async () => {
    mockFetch([{ path: "/my/scripts/11", body: script() }]);
    renderWithClient(<ReviewScriptPage />);
    const cards = await screen.findAllByRole("generic", { name: /^Question / });
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Question 5", "Question 1"]);
    expect(within(q5()).getByText("Two of three points.")).toBeInTheDocument();
    expect(within(q5()).getByText("✗ c")).toBeInTheDocument();
    expect(within(q5()).getByText("Check this")).toBeInTheDocument();
    await userEvent.click(within(q5()).getByRole("button", { name: "p.2" }));
    expect(screen.getByRole("img", { name: "Answer script page 2" })).toHaveAttribute(
      "src", "/api/my/scripts/11/pages/2");
  });

  it("blocks approval until flagged questions are checked", async () => {
    mockFetch([{ path: "/my/scripts/11", body: script() }]);
    renderWithClient(<ReviewScriptPage />);
    expect(await screen.findByText("Check question 5 (flagged for review).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve & next script" })).toBeDisabled();
  });

  it("confirms a flagged question with 'Looks right'", async () => {
    const confirmed = script({
      questions: [script().questions[0], question({ confirmed: true })], problems: [],
    });
    const calls = mockFetch([
      { path: "/my/scripts/11", body: script() },
      { method: "PATCH", path: "/my/scripts/11/questions/101", body: confirmed },
    ]);
    renderWithClient(<ReviewScriptPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Looks right" }));
    expect(await screen.findByText("Ready to approve.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ confirmed: true });
    expect(screen.getByRole("button", { name: "Approve & next script" })).toBeEnabled();
  });

  it("explains invalid marks instead of sending them, and saves valid ones", async () => {
    const changed = script({ questions: [script().questions[0],
      question({ final_marks: 2.5, confirmed: true, changed: true })], final_total: 3.5, problems: [] });
    const calls = mockFetch([
      { path: "/my/scripts/11", body: script() },
      { method: "PATCH", path: "/my/scripts/11/questions/101", body: changed },
    ]);
    renderWithClient(<ReviewScriptPage />);
    const input = await within(await screen.findByRole("generic", { name: "Question 5" }))
      .findByLabelText("Marks");
    await userEvent.clear(input);
    await userEvent.type(input, "3.5");
    fireEvent.blur(input);
    expect(within(q5()).getByText("At most 3 marks.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);

    await userEvent.clear(input);
    await userEvent.type(input, "2.5{Enter}");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ final_marks: 2.5 }));
    expect(await screen.findByText("Changed from AI (2)")).toBeInTheDocument();
  });

  it("approves and moves to the next script", async () => {
    const ready = script({ questions: [script().questions[0], question({ confirmed: true })], problems: [] });
    mockFetch([
      { path: "/my/scripts/11", body: ready },
      { method: "POST", path: "/my/scripts/11/approve", body: { ...ready, status: "approved", editable: false } },
    ]);
    renderWithClient(<ReviewScriptPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve & next script" }));
    await waitFor(() => expect(routerMock.push).toHaveBeenCalledWith("/scripts/12"));
  });

  it("is read-only after the bundle is submitted", async () => {
    mockFetch([{ path: "/my/scripts/11", body: script({
      status: "approved", editable: false, bundle_submitted: true, problems: [] }) }]);
    renderWithClient(<ReviewScriptPage />);
    expect(await screen.findByText(/marks are locked/)).toBeInTheDocument();
    expect(within(q5()).getByLabelText("Marks")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Reopen script" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve & next script" })).not.toBeInTheDocument();
  });

  it("lets an approved script be reopened before submitting", async () => {
    const approved = script({ status: "approved", editable: false, problems: [] });
    const calls = mockFetch([
      { path: "/my/scripts/11", body: approved },
      { method: "POST", path: "/my/scripts/11/reopen", body: script() },
    ]);
    renderWithClient(<ReviewScriptPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Reopen script" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/my/scripts/11/reopen")).toBe(true));
    expect(await screen.findByRole("button", { name: "Approve & next script" })).toBeInTheDocument();
  });

  it("asks for a missing roll number and saves it", async () => {
    const calls = mockFetch([
      { path: "/my/scripts/11", body: script({ roll_number: null, roll_number_source: null,
        problems: ["Enter the roll number."] }) },
      { method: "PATCH", path: "/my/scripts/11", body: script({ roll_number: "21CS099",
        roll_number_source: "teacher" }) },
    ]);
    renderWithClient(<ReviewScriptPage />);
    expect(await screen.findByText("not found on the cover")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Roll number"), "21cs099");
    await userEvent.click(screen.getByRole("button", { name: "Save roll number" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ roll_number: "21cs099" }));
    expect(await screen.findByText("entered by you")).toBeInTheDocument();
  });

  it("supports keyboard review: Enter confirms the active flagged question, n goes next", async () => {
    const calls = mockFetch([
      { path: "/my/scripts/11", body: script() },
      { method: "PATCH", path: "/my/scripts/11/questions/101", body: script() },
    ]);
    renderWithClient(<ReviewScriptPage />);
    await screen.findAllByRole("generic", { name: /^Question / });
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ confirmed: true }));
    await userEvent.keyboard("n");
    expect(routerMock.push).toHaveBeenCalledWith("/scripts/12");
  });
});
