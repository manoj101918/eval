"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { QuestionOut, ScriptOut } from "./teacher";

export const scriptKey = (id: number) => ["my", "script", id] as const;

export function useScript(id: number) {
  return useQuery({ queryKey: scriptKey(id), queryFn: () => api<ScriptOut>(`/my/scripts/${id}`) });
}

/** Every edit returns the updated script; put it in the cache and refresh the lists. */
function useScriptMutation<V>(id: number, call: (vars: V) => Promise<ScriptOut>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: (script) => {
      client.setQueryData(scriptKey(id), script);
      client.invalidateQueries({ queryKey: ["my", "bundles"] });
      client.invalidateQueries({ queryKey: ["my", "bundle", script.bundle_id] });
    },
  });
}

export function useEditQuestion(id: number) {
  return useScriptMutation(id, ({ qid, edit }: { qid: number; edit: object }) =>
    api<ScriptOut>(`/my/scripts/${id}/questions/${qid}`, { method: "PATCH", json: edit }));
}

export function useEditRoll(id: number) {
  return useScriptMutation(id, (roll: string) =>
    api<ScriptOut>(`/my/scripts/${id}`, { method: "PATCH", json: { roll_number: roll } }));
}

export function useApprove(id: number) {
  return useScriptMutation(id, () => api<ScriptOut>(`/my/scripts/${id}/approve`, { method: "POST" }));
}

export function useReopen(id: number) {
  return useScriptMutation(id, () => api<ScriptOut>(`/my/scripts/${id}/reopen`, { method: "POST" }));
}

export function isToCheck(q: QuestionOut): boolean {
  return q.needs_review && !q.confirmed && q.counted;
}

/** Flagged questions first (by the AI's flag, so cards do not jump when confirmed). */
export function reviewOrder(questions: QuestionOut[]): QuestionOut[] {
  return questions
    .map((q, i) => ({ q, i }))
    .sort((a, b) => Number(b.q.needs_review) - Number(a.q.needs_review) || a.i - b.i)
    .map(({ q }) => q);
}

export const ROLL_PATTERN = /^[A-Za-z0-9/-]{1,32}$/;
