"use client";

import React from "react";
import Link from "next/link";
import { ArrowLeft, ChevronRight, Home } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  href?: string;
  active?: boolean;
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[];
  backHref?: string;
  backLabel?: string;
  className?: string;
}

export default function Breadcrumbs({
  items,
  backHref = "/",
  backLabel = "Back to Projects",
  className = "",
}: BreadcrumbsProps) {
  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 text-xs ${className}`}>
      {/* Breadcrumb Path */}
      <nav aria-label="Breadcrumb" className="flex items-center space-x-1.5 text-slate-500">
        <Link
          href="/"
          className="flex items-center gap-1 hover:text-slate-900 transition-colors font-medium text-slate-600"
        >
          <Home className="h-3.5 w-3.5" />
          <span>Projects</span>
        </Link>

        {items.map((item, idx) => (
          <React.Fragment key={idx}>
            <ChevronRight className="h-3 w-3 text-slate-400 shrink-0" />
            {item.href && !item.active ? (
              <Link
                href={item.href}
                className="hover:text-slate-900 transition-colors font-medium text-slate-600 truncate max-w-[200px]"
              >
                {item.label}
              </Link>
            ) : (
              <span className={`font-semibold truncate max-w-[240px] ${item.active ? "text-slate-900" : "text-slate-500"}`}>
                {item.label}
              </span>
            )}
          </React.Fragment>
        ))}
      </nav>

      {/* Direct Back Link */}
      {backHref && (
        <Link
          href={backHref}
          className="inline-flex items-center gap-1.5 font-medium text-slate-500 hover:text-slate-900 transition-colors group"
        >
          <ArrowLeft className="h-3.5 w-3.5 transition-transform group-hover:-translate-x-0.5" />
          <span>{backLabel}</span>
        </Link>
      )}
    </div>
  );
}
