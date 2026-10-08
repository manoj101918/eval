import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";

export type Route = { method?: string; path: string; status?: number; body?: unknown };

/** Mock fetch: each call is matched against the routes by method and path (first match). */
export function mockFetch(routes: Route[]) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, path: url, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const route = routes.find((r) => (r.method ?? "GET") === method && url === `/api${r.path}`);
    if (!route) return new Response(JSON.stringify({ detail: `no mock for ${method} ${url}` }), { status: 599 });
    const status = route.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(route.body ?? {}), {
      status, headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

export const routerMock = { replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), back: vi.fn() };

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => (globalThis as { __params?: Record<string, string> }).__params ?? {},
}));

export function setParams(params: Record<string, string>) {
  (globalThis as { __params?: Record<string, string> }).__params = params;
}
