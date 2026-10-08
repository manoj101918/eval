// Sample API payloads shaped like the backend's response models.
import type { BundleScripts, MyBundle, QuestionOut, ScriptItem, ScriptOut } from "@/lib/teacher";

export const exam = { id: 1, name: "Mid 1", subject: "Physics", class_section: "XII-A", max_marks: 6 };

export function bundle(over: Partial<MyBundle> = {}): MyBundle {
  return { id: 7, code: "B1", exam, status: "in_review", total: 2, graded: 1, approved: 1, pending: 0,
    failed: 0, can_submit: false, ...over };
}

export function scriptItem(over: Partial<ScriptItem> = {}): ScriptItem {
  return { id: 11, roll_number: "21CS045", status: "graded", error: null, ai_total: 4, final_total: 4,
    to_check: 1, ...over };
}

export function bundleScripts(scripts: ScriptItem[], over: Partial<BundleScripts> = {}): BundleScripts {
  return { ...bundle(), scripts, ...over };
}

export function question(over: Partial<QuestionOut> = {}): QuestionOut {
  return {
    id: 101, question: "5", qtype: "long", max_marks: 3, choice_group: null, counted: true,
    ai_status: "graded", ai_marks: 2, ai_reason: "Two of three points.", ai_confidence: "medium",
    points_met: ["a", "b"], points_missing: ["c"], needs_review: true,
    review_notes: ["marks deducted with less than high confidence"], pages: [2],
    final_marks: 2, teacher_comment: "", confirmed: false, changed: false, ...over,
  };
}

export function script(over: Partial<ScriptOut> = {}): ScriptOut {
  return {
    id: 11, bundle_id: 7, exam, status: "graded", editable: true, roll_number: "21CS045",
    roll_number_source: "qr", page_count: 3, failed_pages: [],
    questions: [
      question({ id: 100, question: "1", qtype: "mcq", max_marks: 1, ai_marks: 1, final_marks: 1,
        needs_review: false, review_notes: [], ai_confidence: "high", ai_reason: "Chose (c), the correct option." }),
      question(),
    ],
    ai_total: 3, final_total: 3, max_marks: 4, leftovers: [],
    problems: ["Check question 5 (flagged for review)."], previous_id: null, next_id: 12, ...over,
  };
}
