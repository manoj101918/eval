"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "@/lib/api";

/** Send the user where they need to be when the session ends or a password change is due.
 * Runs outside React (query cache callbacks), so a full page load is used; it also drops
 * any cached student data. */
export function handleAuthError(error: unknown): void {
  if (!(error instanceof ApiError) || typeof window === "undefined") return;
  const path = window.location.pathname;
  if (error.status === 401 && path !== "/login") {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- see above
    window.location.assign("/login");
  }
  if (error.code === "password_change_required" && path !== "/change-password") {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- see above
    window.location.assign("/change-password");
  }
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleAuthError }),
    mutationCache: new MutationCache({ onError: handleAuthError }),
    defaultOptions: {
      queries: {
        retry: (count, error) =>
          !(error instanceof ApiError && error.status < 500) && count < 2,
        refetchOnWindowFocus: false,
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
