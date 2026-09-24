"use client";

import React from "react";
import { ActivityStatus } from "@/lib/types";

export type SemanticStatus =
  | ActivityStatus
  | "CRITICAL"
  | "NEAR_CRITICAL"
  | "NEGATIVE_FLOAT"
  | "ACTIVE"
  | "ON_TIME"
  | "DELAYED"
  | "APPROVED"
  | "REJECTED"
  | "REASSIGNED"
  | "PENDING";

interface StatusBadgeProps {
  status: SemanticStatus | string;
  size?: "xs" | "sm" | "md";
  showDot?: boolean;
  className?: string;
}

export default function StatusBadge({
  status,
  size = "sm",
  showDot = true,
  className = "",
}: StatusBadgeProps) {
  const normStatus = (status || "").toUpperCase();

  let styles = "bg-slate-100 text-slate-700 border-slate-200";
  let dotColor = "bg-slate-400";
  let label = status;

  switch (normStatus) {
    case "COMPLETED":
    case "APPROVED":
    case "ON_TIME":
      styles = "bg-emerald-50 text-emerald-700 border-emerald-200/80";
      dotColor = "bg-emerald-500";
      label = normStatus === "COMPLETED" ? "Completed" : normStatus === "APPROVED" ? "Approved" : "On Time";
      break;

    case "IN_PROGRESS":
    case "ACTIVE":
      styles = "bg-blue-50 text-blue-700 border-blue-200/80";
      dotColor = "bg-blue-500";
      label = normStatus === "IN_PROGRESS" ? "In Progress" : "Active";
      break;

    case "CRITICAL":
    case "NEGATIVE_FLOAT":
    case "REJECTED":
    case "DELAYED":
      styles = "bg-rose-50 text-rose-700 border-rose-200/80";
      dotColor = "bg-rose-500";
      label =
        normStatus === "CRITICAL"
          ? "Critical"
          : normStatus === "NEGATIVE_FLOAT"
          ? "Negative Float"
          : normStatus === "REJECTED"
          ? "Rejected"
          : "Delayed";
      break;

    case "NEAR_CRITICAL":
    case "PENDING":
    case "REASSIGNED":
      styles = "bg-amber-50 text-amber-700 border-amber-200/80";
      dotColor = "bg-amber-500";
      label =
        normStatus === "NEAR_CRITICAL"
          ? "Near Critical"
          : normStatus === "REASSIGNED"
          ? "Reassigned"
          : "Pending";
      break;

    case "NOT_STARTED":
    default:
      styles = "bg-slate-100 text-slate-700 border-slate-200";
      dotColor = "bg-slate-400";
      label = normStatus === "NOT_STARTED" ? "Not Started" : status;
      break;
  }

  const sizeClasses = {
    xs: "px-2 py-0.5 text-[11px]",
    sm: "px-2.5 py-0.5 text-xs",
    md: "px-3 py-1 text-xs",
  }[size];

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium rounded-full border ${styles} ${sizeClasses} ${className} whitespace-nowrap select-none`}
    >
      {showDot && <span className={`h-1.5 w-1.5 rounded-full ${dotColor} shrink-0`} />}
      <span>{label}</span>
    </span>
  );
}
