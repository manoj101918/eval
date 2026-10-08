// Mirrors backend/app/marks.valid_marks so mistakes are explained before anything is sent.
// The backend still checks every value.

export function parseMarks(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "" || !/^\d+(\.\d+)?$/.test(trimmed)) return null;
  return Number(trimmed);
}

export function marksProblem(text: string, max: number): string | null {
  const value = parseMarks(text);
  if (value === null) return "Enter a number, e.g. 2 or 2.5.";
  if (value > max) return `At most ${max} marks.`;
  if ((value * 2) % 1 !== 0) return "Use whole or half marks (e.g. 2.5).";
  return null;
}

/** Step by half a mark within 0..max. */
export function stepMarks(value: number | null, delta: number, max: number): number {
  const next = (value ?? 0) + delta;
  return Math.min(max, Math.max(0, Math.round(next * 2) / 2));
}

export function fmtMarks(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
