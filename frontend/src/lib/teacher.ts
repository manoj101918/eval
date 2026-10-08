"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas } from "./api";

export type MyBundle = Schemas["MyBundle"];
export type BundleScripts = Schemas["BundleScripts"];
export type ScriptItem = Schemas["ScriptItem"];
export type ScriptOut = Schemas["ScriptOut"];
export type QuestionOut = Schemas["QuestionOut"];
export type SubmitResult = Schemas["SubmitResult"];

const POLL_MS = 5000;

export function useMyBundles() {
  return useQuery({
    queryKey: ["my", "bundles"],
    queryFn: () => api<MyBundle[]>("/my/bundles"),
    // keep polling while the AI is still grading something
    refetchInterval: (q) => (q.state.data?.some((b) => b.pending > 0) ? POLL_MS : false),
  });
}

export function useBundleScripts(bundleId: number) {
  return useQuery({
    queryKey: ["my", "bundle", bundleId],
    queryFn: () => api<BundleScripts>(`/my/bundles/${bundleId}`),
    refetchInterval: (q) => (q.state.data && q.state.data.pending > 0 ? POLL_MS : false),
  });
}

export function useSubmitBundle(bundleId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<SubmitResult>(`/my/bundles/${bundleId}/submit`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["my"] }),
  });
}

/** Plain-language reasons for scripts the AI could not grade. */
export function failureText(code: string | null | undefined): string {
  if (!code) return "Grading failed.";
  if (code === "quota") return "The AI service's usage limit was reached. The exam cell can retry it later.";
  if (code === "unreadable_pdf") return "The PDF could not be read. The exam cell should rescan it.";
  if (code === "api_key") return "The AI service rejected its key. Contact the exam cell.";
  return "Grading failed. The exam cell can retry it.";
}

/** Summary shown before a bundle is submitted. */
export function submitSummary(scripts: ScriptItem[]) {
  const changed = scripts.filter((s) => s.ai_total !== null && s.final_total !== null && s.ai_total !== s.final_total);
  const sum = (key: "ai_total" | "final_total") => scripts.reduce((t, s) => t + (s[key] ?? 0), 0);
  return {
    scripts: scripts.length,
    approved: scripts.filter((s) => s.status === "approved").length,
    changedScripts: changed.length,
    aiTotal: sum("ai_total"),
    finalTotal: sum("final_total"),
  };
}
