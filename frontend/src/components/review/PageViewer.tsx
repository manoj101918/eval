"use client";

import { pageUrl } from "@/lib/api";
import { Button } from "../ui";

const ZOOMS = [{ label: "Fit", width: "100%" }, { label: "150%", width: "150%" }, { label: "200%", width: "200%" }];

export function PageViewer({ scriptId, pageCount, page, onPage, zoom, onZoom }: {
  scriptId: number; pageCount: number; page: number; onPage: (p: number) => void;
  zoom: number; onZoom: (z: number) => void;
}) {
  const pages = Array.from({ length: pageCount }, (_, i) => i + 1);
  return (
    <div className="flex h-full min-h-0 gap-2">
      <div className="flex w-20 shrink-0 flex-col gap-2 overflow-y-auto pr-1" aria-label="Pages">
        {pages.map((p) => (
          <button key={p} type="button" onClick={() => onPage(p)} aria-label={`Page ${p}`}
            aria-current={p === page ? "page" : undefined}
            className={`rounded border-2 ${p === page ? "border-blue-600" : "border-transparent"}`}>
            {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
            <img src={pageUrl(scriptId, p, true)} alt="" loading="lazy" className="w-full" />
            <span className="text-xs text-gray-600">{p}</span>
          </button>
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="mb-2 flex items-center gap-2 text-sm">
          <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</Button>
          <span>Page {page} of {pageCount}</span>
          <Button variant="secondary" disabled={page >= pageCount} onClick={() => onPage(page + 1)}>Next ›</Button>
          <span className="ml-auto flex gap-1">
            {ZOOMS.map((z, i) => (
              <Button key={z.label} variant={i === zoom ? "primary" : "secondary"} onClick={() => onZoom(i)}>
                {z.label}
              </Button>
            ))}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto rounded border border-gray-200 bg-gray-100">
          {pageCount > 0 ? (
            // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
            <img src={pageUrl(scriptId, page)} alt={`Answer script page ${page}`}
              style={{ width: ZOOMS[zoom].width, maxWidth: "none" }} />
          ) : <p className="p-4 text-sm text-gray-600">No page images.</p>}
        </div>
      </div>
    </div>
  );
}
