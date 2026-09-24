"use client";

import React from "react";
import { LucideIcon } from "lucide-react";

export type MetricTone =
  | "default"
  | "primary"
  | "success"
  | "warning"
  | "danger"
  | "intelligence";

interface MetricCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: LucideIcon;
  tone?: MetricTone;
  progressPercent?: number;
  onClick?: () => void;
  className?: string;
}

export default function MetricCard({
  label,
  value,
  subtext,
  icon: Icon,
  tone = "default",
  progressPercent,
  onClick,
  className = "",
}: MetricCardProps) {
  const toneMap: Record<MetricTone, { border: string; valueColor: string; iconBg: string; iconColor: string; barColor: string }> = {
    default: {
      border: "border-slate-200",
      valueColor: "text-slate-900",
      iconBg: "bg-slate-100",
      iconColor: "text-slate-600",
      barColor: "bg-slate-600",
    },
    primary: {
      border: "border-blue-200/80",
      valueColor: "text-blue-600",
      iconBg: "bg-blue-50",
      iconColor: "text-blue-600",
      barColor: "bg-blue-600",
    },
    success: {
      border: "border-emerald-200/80",
      valueColor: "text-emerald-600",
      iconBg: "bg-emerald-50",
      iconColor: "text-emerald-600",
      barColor: "bg-emerald-600",
    },
    warning: {
      border: "border-amber-200/80",
      valueColor: "text-amber-600",
      iconBg: "bg-amber-50",
      iconColor: "text-amber-600",
      barColor: "bg-amber-600",
    },
    danger: {
      border: "border-rose-200/80",
      valueColor: "text-rose-600",
      iconBg: "bg-rose-50",
      iconColor: "text-rose-600",
      barColor: "bg-rose-600",
    },
    intelligence: {
      border: "border-indigo-200/80",
      valueColor: "text-indigo-600",
      iconBg: "bg-indigo-50",
      iconColor: "text-indigo-600",
      barColor: "bg-indigo-600",
    },
  };

  const t = toneMap[tone];

  return (
    <div
      onClick={onClick}
      className={`rounded-lg border bg-white p-4 transition-all ${t.border} ${
        onClick ? "cursor-pointer hover:border-slate-300 hover:shadow-xs" : ""
      } ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </span>
        {Icon && (
          <div className={`flex h-7 w-7 items-center justify-center rounded-md ${t.iconBg} ${t.iconColor}`}>
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>

      <div className={`mt-2 text-2xl font-bold tracking-tight font-mono ${t.valueColor}`}>
        {value}
      </div>

      {progressPercent !== undefined && (
        <div className="mt-2.5 h-1.5 w-full rounded-full bg-slate-100 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${t.barColor}`}
            style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
          />
        </div>
      )}

      {subtext && (
        <div className="mt-1.5 text-xs text-slate-500 truncate">
          {subtext}
        </div>
      )}
    </div>
  );
}
