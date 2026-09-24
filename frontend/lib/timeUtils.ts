/**
 * Timezone-aware date parsing and formatting utilities.
 * Ensures server UTC timestamps are parsed correctly as UTC in client browsers,
 * preventing local timezone offsets (e.g. +05:30 in India) from causing
 * newly created chats to appear as "5h ago".
 */

export function parseUtcDate(dateStr?: string | null): Date {
  if (!dateStr) return new Date();
  let str = String(dateStr).trim();
  if (!str) return new Date();

  // If format is "YYYY-MM-DD HH:mm:ss", convert space to "T"
  if (str.includes(" ") && !str.includes("T")) {
    str = str.replace(" ", "T");
  }

  // If ISO string lacks timezone indicator (no trailing Z and no +/- offset),
  // append "Z" so JavaScript Date parser treats it as UTC instead of local time.
  if (!str.endsWith("Z") && !/[+-]\d{2}(:?\d{2})?$/.test(str)) {
    str += "Z";
  }

  const d = new Date(str);
  return isNaN(d.getTime()) ? new Date() : d;
}

export function formatRelativeTime(dateStr?: string | null): string {
  if (!dateStr) return "";
  try {
    const d = parseUtcDate(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();

    // If future due to slight clock drift or under 1 minute, show "Just now"
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;

    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;

    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;

    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

export function formatMessageTime(dateStr?: string | null): string {
  if (!dateStr) return "";
  try {
    const d = parseUtcDate(dateStr);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}
