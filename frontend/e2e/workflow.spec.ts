import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const ROOT = path.resolve(__dirname, "..", "..");
const SAMPLES = path.join(ROOT, "data", "e2e");
const PYTHON = process.platform === "win32"
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");

async function login(page: Page, employeeId: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Employee ID").fill(employeeId);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function logout(page: Page) {
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
}

test("exam cell to teacher to Excel: the whole workflow in the browser", async ({ page }) => {
  // --- Exam cell: add a teacher, create the exam, a bundle, upload a script ---------------
  await login(page, "ADM1", "demo-admin-1");
  await expect(page).toHaveURL(/\/admin$/);

  await page.getByRole("link", { name: "Teachers" }).click();
  await page.getByLabel("Employee ID").fill("T300");
  await page.getByLabel("Name").fill("Meena");
  await page.getByRole("button", { name: "Add teacher" }).click();
  const oneTime = (await page.getByTestId("one-time-password").textContent())!.trim();
  expect(oneTime).toHaveLength(12);
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("link", { name: "Exams" }).click();
  await page.getByRole("link", { name: "New exam" }).click();
  await page.getByLabel("Subject").fill("Physics");
  await page.getByLabel("Exam").fill("Mid-term 1");
  await page.getByLabel("Class / section").fill("XII-A");
  await page.getByLabel("Marking scheme file").setInputFiles(path.join(SAMPLES, "sample_scheme.xlsx"));
  await page.getByRole("button", { name: "Create exam" }).click();
  await expect(page.getByRole("heading", { name: "Physics · Mid-term 1" })).toBeVisible();
  const examUrl = page.url();

  await page.getByLabel("New bundle code").fill("B1");
  await page.getByLabel("Teacher for the new bundle").selectOption("T300");
  await page.getByRole("button", { name: "Add bundle" }).click();
  await expect(page.getByRole("heading", { name: "Bundle B1" })).toBeVisible();
  await page.getByLabel(/Upload PDFs for bundle/).setInputFiles(path.join(SAMPLES, "sample_script.pdf"));
  await expect(page.getByText("1 uploaded; the AI is grading them.")).toBeVisible();
  await expect(page.getByText("1 to review")).toBeVisible(); // background grading finished
  await logout(page);

  // --- Teacher: first login, review, approve, submit ---------------------------------------
  await login(page, "T300", oneTime);
  await expect(page).toHaveURL(/\/change-password$/);
  await page.getByLabel("Current (one-time) password").fill(oneTime);
  await page.getByLabel(/^New password/).fill("meena-pass-9"); // the label also carries its hint
  await page.getByLabel("Repeat new password").fill("meena-pass-9");
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("heading", { name: "My bundles" })).toBeVisible();

  await page.getByRole("link", { name: "Review scripts →" }).click();
  await expect(page.getByRole("heading", { name: /bundle B1$/ })).toBeVisible();
  await page.getByRole("link", { name: "Review", exact: true }).click();
  await expect(page.getByText("Enter the roll number.")).toBeVisible();
  await expect(page.getByAltText("Answer script page 1")).toBeVisible();

  await page.getByLabel("Roll number").fill("21CS045");
  await page.getByRole("button", { name: "Save roll number" }).click();
  await expect(page.getByText("entered by you")).toBeVisible();

  // Q3 was flagged (AI took a mark off with medium confidence): the teacher gives full marks.
  const q3 = page.getByLabel("Question 3");
  await expect(q3.getByText("Check this")).toBeVisible();
  await q3.getByRole("button", { name: "Half a mark more" }).click();
  await expect(q3.getByText("Changed from AI (2)")).toBeVisible();
  await q3.getByRole("button", { name: "Half a mark more" }).click();
  await expect(q3.getByLabel("Marks")).toHaveValue("3");
  await expect(page.getByText("Ready to approve.")).toBeVisible();
  await page.getByRole("button", { name: "Approve & next script" }).click();

  await expect(page.getByText("Every script is approved.")).toBeVisible(); // back on the bundle
  await page.getByRole("link", { name: "← My bundles" }).click();
  // wait for the refreshed list (the cached one still says 0 approved) before opening the dialog
  await expect(page.getByRole("progressbar", { name: "Approved by you" })).toHaveAttribute("aria-valuenow", "1");
  await page.getByRole("button", { name: "Submit bundle" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Total marks: 6 (AI proposed 5)")).toBeVisible();
  await dialog.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(dialog.getByText(/written to the Excel sheet/)).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();
  await logout(page);

  // --- Another teacher cannot open this script ---------------------------------------------
  await login(page, "T200", "demo-teacher-2");
  await expect(page.getByText("No bundles are assigned to you yet.")).toBeVisible();
  await page.goto("/scripts/1");
  await expect(page.getByRole("alert").filter({ hasText: "Script not found." })).toBeVisible();
  await logout(page);

  // --- Exam cell downloads the mark sheet ------------------------------------------------------
  await login(page, "ADM1", "demo-admin-1");
  await page.goto(examUrl);
  await expect(page.getByText(/^Exported /)).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Download mark sheet" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
  const file = test.info().outputPath("marks.xlsx"); // openpyxl needs the extension
  await download.saveAs(file);
  const rows = execFileSync(PYTHON, ["-c",
    "import sys, json; from openpyxl import load_workbook; "
    + "print(json.dumps([list(r) for r in load_workbook(sys.argv[1]).active.iter_rows(values_only=True)]))",
    file], { encoding: "utf8" });
  expect(JSON.parse(rows)).toEqual([
    ["Roll No", "1", "2", "3", "Total"],
    ["Max marks", 1, 2, 3, 6],
    ["21CS045", 1, 2, 3, 6],
  ]);
});
