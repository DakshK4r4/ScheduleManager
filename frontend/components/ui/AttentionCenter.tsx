"use client";

import React, { useState, useEffect, useRef } from "react";
import { Bell, AlertTriangle, AlertCircle, CheckCircle2, ChevronRight, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { fetchProjects, fetchReviewQueue, fetchProjectCPM } from "@/lib/api";

export interface AttentionItem {
  id: string;
  projectId: string;
  projectCode: string;
  title: string;
  description: string;
  severity: "high" | "medium" | "low";
  tabTarget: string;
}

export default function AttentionCenter() {
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  const loadAttentionItems = async () => {
    try {
      setLoading(true);
      const projects = await fetchProjects();
      const collected: AttentionItem[] = [];

      for (const p of projects.slice(0, 3)) {
        try {
          const qRes = await fetchReviewQueue(p.id);
          if (qRes.pending_count > 0) {
            collected.push({
              id: `queue-${p.id}`,
              projectId: p.id,
              projectCode: p.project_code,
              title: `${qRes.pending_count} Review ${qRes.pending_count === 1 ? "Item" : "Items"} Pending`,
              description: `Ambiguous field events and matches awaiting human verification.`,
              severity: "high",
              tabTarget: "reports",
            });
          }
        } catch {
          // ignore background errors
        }

        try {
          const cpm = await fetchProjectCPM(p.id);
          if (cpm.negative_float_activities && cpm.negative_float_activities.length > 0) {
            collected.push({
              id: `cpm-neg-${p.id}`,
              projectId: p.id,
              projectCode: p.project_code,
              title: `${cpm.negative_float_activities.length} Negative Float Activities`,
              description: `Schedule logic indicates delayed finish dates violating project targets.`,
              severity: "high",
              tabTarget: "activities",
            });
          }
          if (cpm.cycles_detected) {
            collected.push({
              id: `cpm-cycle-${p.id}`,
              projectId: p.id,
              projectCode: p.project_code,
              title: `Circular Dependency Detected`,
              description: `A relationship cycle is preventing valid critical path calculation.`,
              severity: "high",
              tabTarget: "overview",
            });
          }
        } catch {
          // ignore background errors
        }
      }

      setItems(collected);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAttentionItems();
    const interval = setInterval(loadAttentionItems, 30000);
    return () => clearInterval(interval);
  }, []);

  // Close on outside click
  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener("mousedown", handleOutsideClick);
    }
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [isOpen]);

  const handleItemClick = (item: AttentionItem) => {
    setIsOpen(false);
    router.push(`/projects/${item.projectId}?tab=${item.tabTarget}`);
  };

  const count = items.length;

  return (
    <div className="relative" ref={popoverRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        title="Attention Center"
        aria-label="Attention Center"
        className={`relative flex h-9 w-9 items-center justify-center rounded-md border transition-colors ${
          isOpen
            ? "border-blue-500 bg-blue-50 text-blue-700"
            : count > 0
            ? "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100"
            : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-800"
        }`}
      >
        <Bell className="h-4 w-4" />
        {count > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-rose-600 text-[10px] font-bold text-white shadow-xs">
            {count}
          </span>
        )}
      </button>

      {/* Popover Dropdown */}
      {isOpen && (
        <div className="absolute right-0 mt-2 w-80 sm:w-96 rounded-lg border border-slate-200 bg-white shadow-xl z-50 overflow-hidden animate-in fade-in zoom-in-95 duration-100">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 bg-slate-50">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Attention Center
              </span>
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-800">
                {count} {count === 1 ? "action required" : "actions required"}
              </span>
            </div>
            <button
              onClick={() => setIsOpen(false)}
              className="text-slate-400 hover:text-slate-600 p-0.5"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="max-h-80 overflow-y-auto divide-y divide-slate-100">
            {loading && items.length === 0 ? (
              <div className="p-6 text-center text-xs text-slate-400">
                Checking project telemetry...
              </div>
            ) : items.length === 0 ? (
              <div className="p-6 text-center space-y-2">
                <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-500" />
                <div className="text-xs font-semibold text-slate-800">All Schedules Healthy</div>
                <p className="text-[11px] text-slate-400">
                  No open reviews, negative float, or critical logic anomalies.
                </p>
              </div>
            ) : (
              items.map((item) => (
                <div
                  key={item.id}
                  onClick={() => handleItemClick(item)}
                  className="p-3.5 hover:bg-slate-50 cursor-pointer transition-colors flex items-start gap-3 group"
                >
                  <div className="mt-0.5 shrink-0">
                    {item.severity === "high" ? (
                      <AlertCircle className="h-4 w-4 text-rose-500" />
                    ) : (
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[10px] font-bold text-blue-600 bg-blue-50 px-1.5 py-0.2 rounded border border-blue-200">
                        {item.projectCode}
                      </span>
                      <span className="text-xs font-bold text-slate-800 truncate">
                        {item.title}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500 leading-normal line-clamp-2">
                      {item.description}
                    </p>
                  </div>
                  <ChevronRight className="h-4 w-4 text-slate-300 group-hover:text-slate-500 shrink-0 transition-transform group-hover:translate-x-0.5" />
                </div>
              ))
            )}
          </div>

          <div className="border-t border-slate-100 p-2.5 bg-slate-50 text-center">
            <span className="text-[11px] text-slate-400">
              Auto-refreshed against active Primavera database records
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
