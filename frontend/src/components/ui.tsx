"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS = {
  primary: "bg-blue-700 text-white hover:bg-blue-800 disabled:bg-blue-300",
  secondary: "bg-white text-gray-800 border border-gray-300 hover:bg-gray-50 disabled:text-gray-400",
  danger: "bg-red-600 text-white hover:bg-red-700 disabled:bg-red-300",
  ghost: "text-gray-700 hover:bg-gray-100 disabled:text-gray-400",
} as const;

export function Button({
  variant = "primary", className = "", ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof VARIANTS }) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium
        disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg border border-gray-200 bg-white p-4 shadow-sm ${className}`}>{children}</div>;
}

const TONES = {
  gray: "bg-gray-100 text-gray-700",
  blue: "bg-blue-100 text-blue-800",
  green: "bg-green-100 text-green-800",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800",
} as const;

export function Badge({ children, tone = "gray" }: { children: ReactNode; tone?: keyof typeof TONES }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>;
}

const STATUS_TONE: Record<string, keyof typeof TONES> = {
  open: "gray", grading: "blue", ready: "amber", in_review: "amber", submitted: "green",
  queued: "gray", processing: "blue", graded: "amber", failed: "red", approved: "green",
  draft: "gray", completed: "green", exported: "green",
};

const STATUS_LABEL: Record<string, string> = {
  open: "No scripts", grading: "AI grading", ready: "Ready to review", in_review: "In review",
  submitted: "Submitted", queued: "Queued", processing: "AI grading", graded: "To review",
  failed: "Failed", approved: "Approved", draft: "Draft", completed: "All submitted",
  exported: "Marks exported",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "gray"}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-sm text-gray-600">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-700" />
      {label}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  const problems = (error as { problems?: string[] }).problems ?? [];
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <p>{message}</p>
      {problems.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
    </div>
  );
}

export function ProgressBar({ value, total, label }: { value: number; total: number; label: string }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600"><span>{label}</span><span>{value}/{total}</span></div>
      <div className="mt-1 h-2 rounded bg-gray-200" role="progressbar" aria-valuenow={value} aria-valuemax={total} aria-label={label}>
        <div className="h-2 rounded bg-blue-600" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-gray-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-gray-500">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600";

export function Dialog({ open, title, children, onClose }: {
  open: boolean; title: string; children: ReactNode; onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-3 text-lg font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}
