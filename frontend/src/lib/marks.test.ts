import { describe, expect, it } from "vitest";
import { marksProblem, parseMarks, stepMarks } from "./marks";

describe("marks rules (mirror the backend)", () => {
  it.each([["2", null], ["2.5", null], ["0", null], ["3", null]])("accepts %s of 3", (text, problem) => {
    expect(marksProblem(text, 3)).toBe(problem);
  });

  it.each([
    ["3.5", "At most 3 marks."],
    ["1.25", "Use whole or half marks (e.g. 2.5)."],
    ["-1", "Enter a number, e.g. 2 or 2.5."],
    ["two", "Enter a number, e.g. 2 or 2.5."],
    ["", "Enter a number, e.g. 2 or 2.5."],
  ])("rejects %s of 3", (text, problem) => {
    expect(marksProblem(text, 3)).toBe(problem);
  });

  it("steps by half a mark within 0..max", () => {
    expect(stepMarks(2, 0.5, 3)).toBe(2.5);
    expect(stepMarks(3, 0.5, 3)).toBe(3);
    expect(stepMarks(0, -0.5, 3)).toBe(0);
    expect(stepMarks(null, 0.5, 3)).toBe(0.5);
    expect(parseMarks(" 2.5 ")).toBe(2.5);
  });
});
