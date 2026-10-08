import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { mockFetch, renderWithClient, routerMock } from "@/test/utils";
import LoginPage from "./page";

const teacher = { employee_id: "T100", name: "Asha", role: "teacher", must_change_password: false };

async function signIn(password = "pass-word-1") {
  await userEvent.type(screen.getByLabelText("Employee ID"), " T100 ");
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage", () => {
  beforeEach(() => routerMock.replace.mockReset());

  it("signs a teacher in and goes to their bundles", async () => {
    const calls = mockFetch([{ method: "POST", path: "/auth/login", body: teacher }]);
    renderWithClient(<LoginPage />);
    await signIn();
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/bundles"));
    expect(calls[0].body).toEqual({ employee_id: "T100", password: "pass-word-1" });
  });

  it("sends admins to the admin area and first logins to change password", async () => {
    mockFetch([{ method: "POST", path: "/auth/login", body: { ...teacher, role: "admin", must_change_password: true } }]);
    renderWithClient(<LoginPage />);
    await signIn();
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/change-password"));
  });

  it("shows the server's message for a wrong password", async () => {
    mockFetch([{ method: "POST", path: "/auth/login", status: 401,
      body: { detail: "Invalid employee ID or password." } }]);
    renderWithClient(<LoginPage />);
    await signIn("wrong");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid employee ID or password.");
    expect(routerMock.replace).not.toHaveBeenCalled();
  });

  it("explains a locked account", async () => {
    mockFetch([{ method: "POST", path: "/auth/login", status: 423, body: { detail: "locked" } }]);
    renderWithClient(<LoginPage />);
    await signIn();
    expect(await screen.findByRole("alert")).toHaveTextContent("Wait 15 minutes");
  });
});
