"use client";

import React from "react";
import { LucideIcon, Inbox } from "lucide-react";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
    icon?: LucideIcon;
  };
  className?: string;
}

export default function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center space-y-3 ${className}`}
    >
      <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <h4 className="text-sm font-bold text-slate-800">{title}</h4>
        {description && (
          <p className="mt-1 text-xs text-slate-500 max-w-sm mx-auto leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {action && (
        <div className="pt-1">
          <button
            onClick={action.onClick}
            className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-2xs hover:bg-blue-500 transition-colors"
          >
            {action.icon && <action.icon className="h-3.5 w-3.5" />}
            <span>{action.label}</span>
          </button>
        </div>
      )}
    </div>
  );
}
