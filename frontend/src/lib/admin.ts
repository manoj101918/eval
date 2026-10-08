"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas } from "./api";

export type ExamSummary = Schemas["ExamSummary"];
export type ExamDetail = Schemas["ExamDetail"];
export type BundleSummary = Schemas["BundleSummary"];
export type BundleDetail = Schemas["BundleDetail"];
export type UserOut = Schemas["UserOut"];
export type NewUserOut = Schemas["NewUserOut"];
export type TempPassword = Schemas["TempPassword"];
export type UploadResult = Schemas["UploadResult"];

const POLL_MS = 5000;

export function useExams() {
  return useQuery({ queryKey: ["admin", "exams"], queryFn: () => api<ExamSummary[]>("/admin/exams") });
}

export function useExam(id: number) {
  return useQuery({
    queryKey: ["admin", "exam", id],
    queryFn: () => api<ExamDetail>(`/admin/exams/${id}`),
    refetchInterval: (q) =>
      q.state.data?.bundle_list.some((b) => b.counts.queued + b.counts.processing > 0) ? POLL_MS : false,
  });
}

export function useBundleDetail(id: number, enabled: boolean) {
  return useQuery({
    queryKey: ["admin", "bundle", id],
    queryFn: () => api<BundleDetail>(`/admin/bundles/${id}`),
    enabled,
  });
}

export function useUsers() {
  return useQuery({ queryKey: ["admin", "users"], queryFn: () => api<UserOut[]>("/admin/users") });
}

export function activeTeachers(users: UserOut[] | undefined): UserOut[] {
  return (users ?? []).filter((u) => u.role === "teacher" && u.active);
}

/** Mutation that refreshes every admin view afterwards. */
export function useAdminMutation<V, R>(call: (vars: V) => Promise<R>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: call,
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export function uploadScripts(bundleId: number, files: File[]): Promise<UploadResult> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return api<UploadResult>(`/admin/bundles/${bundleId}/scripts`, { form });
}
