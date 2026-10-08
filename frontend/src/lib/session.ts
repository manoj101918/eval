"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas } from "./api";

export type Me = Schemas["Me"];

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), retry: false });
}

export function homeFor(me: Pick<Me, "role" | "must_change_password">): string {
  if (me.must_change_password) return "/change-password";
  return me.role === "admin" ? "/admin" : "/bundles";
}

export function useLogout() {
  const client = useQueryClient();
  return async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    client.clear();
    // A full page load (not router navigation) so no student data stays in memory.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- see above
    window.location.assign("/login");
  };
}
