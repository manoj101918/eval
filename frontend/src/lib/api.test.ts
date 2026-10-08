import { describe, expect, it } from "vitest";
import { mockFetch } from "@/test/utils";
import { api, ApiError, errorFromBody } from "./api";

describe("errorFromBody", () => {
  it("uses a plain detail string", () => {
    const e = errorFromBody(409, { detail: "This bundle is already submitted." });
    expect(e.message).toBe("This bundle is already submitted.");
    expect(e.problems).toEqual([]);
  });

  it("keeps itemised problems (scheme rows, approval blockers)", () => {
    const e = errorFromBody(409, {
      detail: { message: "This script cannot be approved yet.", problems: ["Enter the roll number."] },
    });
    expect(e.message).toBe("This script cannot be approved yet.");
    expect(e.problems).toEqual(["Enter the roll number."]);
  });

  it("turns request validation errors into readable fields", () => {
    const e = errorFromBody(422, {
      detail: [{ loc: ["body", "employee_id"], msg: "String should match pattern" }],
    });
    expect(e.problems).toEqual(["employee_id: String should match pattern"]);
  });

  it("recognises the forced password change", () => {
    expect(errorFromBody(403, { detail: "password_change_required" }).code).toBe("password_change_required");
    expect(errorFromBody(403, { detail: "Admins only." }).code).toBeUndefined();
  });

  it("copes with an empty or non-JSON body", () => {
    expect(errorFromBody(502, null).message).toBe("Request failed (502).");
  });
});

describe("api", () => {
  it("sends the CSRF header and JSON body on writes", async () => {
    const calls = mockFetch([{ method: "POST", path: "/auth/login", body: { role: "teacher" } }]);
    await api("/auth/login", { json: { employee_id: "T1", password: "x" } });
    expect(calls[0].headers["X-Requested-With"]).toBe("aise");
    expect(calls[0].headers["Content-Type"]).toBe("application/json");
    expect(calls[0].body).toEqual({ employee_id: "T1", password: "x" });
  });

  it("returns undefined for 204 and throws ApiError on failure", async () => {
    mockFetch([
      { method: "POST", path: "/auth/logout", status: 204 },
      { path: "/auth/me", status: 401, body: { detail: "Please log in." } },
    ]);
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
    const error = (await api("/auth/me").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
  });
});
