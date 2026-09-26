"use client";

import React from "react";
import { HelpCircle, ChevronRight, Layers, CheckCheck, X, ArrowRight, RefreshCw } from "lucide-react";
import { TimeAgentActionCard } from "@/lib/types";

interface ClarificationCardProps {
  card: TimeAgentActionCard;
  onSelectOption: (value: string) => void;
  onConfirmBulk?: (activityIds: string[], targetPercent: number) => void;
  onCancelBulk?: () => void;
  disabled?: boolean;
  actionLoading?: string | null;
}

export default function ClarificationCard({
  card,
  onSelectOption,
  onConfirmBulk,
  onCancelBulk,
  disabled = false,
  actionLoading = null,
}: ClarificationCardProps) {
  // 1. Bulk Scope Proposal Card
  if (card.type === "BULK_SCOPE_PROPOSAL") {
    const activities = card.bulk_activities || [];
    const count = card.bulk_count ?? activities.length;
    const targetPct = card.target_percent ?? 100.0;
    const isApplied = card.proposal_status === "APPLIED";
    const isCancelled = card.proposal_status === "CANCELLED";

    return (
      <div className="rounded-xl border border-indigo-200 bg-white p-4 space-y-3 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold text-indigo-700">
            <Layers className="h-4 w-4 shrink-0" />
            <span>{card.scope_label || "Bulk Work Package Scope"}</span>
          </div>
          <div className="flex items-center gap-2">
            {isApplied && (
              <span className="px-2.5 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-[10px] font-bold flex items-center gap-1">
                <CheckCheck className="h-3 w-3 text-emerald-600" />
                Applied
              </span>
            )}
            {isCancelled && (
              <span className="px-2.5 py-0.5 rounded-full bg-slate-100 border border-slate-200 text-slate-600 text-[10px] font-bold flex items-center gap-1">
                <X className="h-3 w-3" />
                Cancelled
              </span>
            )}
            <span className="px-2.5 py-0.5 rounded-full bg-indigo-50 border border-indigo-200 text-indigo-700 text-[10px] font-bold">
              {count} {count === 1 ? "Activity" : "Activities"} Found
            </span>
          </div>
        </div>

        {card.question && (
          <p className="text-xs text-slate-600 leading-relaxed">{card.question}</p>
        )}

        {/* Activity Preview List */}
        {activities.length > 0 && (
          <div className="rounded-lg border border-slate-200 bg-slate-50 divide-y divide-slate-200 overflow-hidden text-xs">
            {activities.map((act) => (
              <div
                key={act.activity_id}
                className="flex items-center justify-between px-3 py-2 hover:bg-slate-100/60 transition-colors"
              >
                <div className="min-w-0 flex items-center gap-2">
                  <span className="font-mono font-bold text-blue-700 shrink-0">
                    {act.activity_code}
                  </span>
                  <span className="text-slate-800 truncate max-w-[200px] sm:max-w-[280px]">
                    {act.activity_name}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0 text-[11px] font-medium">
                  <span className="text-slate-500">{act.current_percent}%</span>
                  <ArrowRight className="h-3 w-3 text-slate-400" />
                  <span className="text-emerald-700 font-semibold">{act.proposed_percent ?? targetPct}%</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Bulk Action Buttons */}
        {!isApplied && !isCancelled && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {activities.length > 0 && activities.length <= 6 && (
              <button
                type="button"
                onClick={() => {
                  if (disabled || actionLoading) return;
                  if (onConfirmBulk) {
                    onConfirmBulk(
                      activities.map((a) => a.activity_id),
                      targetPct
                    );
                  } else {
                    onSelectOption("CONFIRM_ALL_BULK");
                  }
                }}
                disabled={disabled || Boolean(actionLoading)}
                className="flex-1 sm:flex-initial px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs flex items-center justify-center gap-1.5 shadow-2xs transition-colors disabled:opacity-50 cursor-pointer"
              >
                {actionLoading === "bulk" ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    <span>Applying update...</span>
                  </>
                ) : (
                  <>
                    <CheckCheck className="h-3.5 w-3.5" />
                    <span>
                      {card.is_multi_activity
                        ? card.confirm_label || `Confirm All Updates (${count})`
                        : `Update All ${count} (${targetPct}%)`}
                    </span>
                  </>
                )}
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                if (disabled || actionLoading) return;
                if (onCancelBulk) {
                  onCancelBulk();
                } else {
                  onSelectOption("CANCEL");
                }
              }}
              disabled={disabled || Boolean(actionLoading)}
              className="px-3.5 py-2 rounded-lg bg-white hover:bg-slate-50 border border-slate-300 text-xs font-semibold text-slate-700 transition-colors flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
            >
              {actionLoading === "bulk-cancel" ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-slate-500" />
                  <span>Cancelling...</span>
                </>
              ) : (
                <>
                  <X className="h-3.5 w-3.5 text-slate-500" />
                  <span>{card.reject_label || "Cancel"}</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>
    );
  }

  // 2. Standard or Multi-Choice Clarification Choice Card
  if (!card.options || card.options.length === 0) return null;

  const isStructuredList =
    card.options.length >= 3 || card.options.some((o) => (o as any).activity_code || (o as any).confidence_score);

  return (
    <div className="rounded-xl border border-amber-200 bg-white p-3.5 space-y-2.5 shadow-xs">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-amber-700">
          <HelpCircle className="h-4 w-4 shrink-0" />
          <span>{card.is_resolution_question ? "Activity Identification" : "Clarification Required"}</span>
        </div>
        {card.is_resolution_question && (
          <span className="px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-800 text-[10px] font-semibold">
            Progressive Disambiguation
          </span>
        )}
      </div>

      {card.question && (
        <p className="text-xs text-slate-700 leading-relaxed font-medium">{card.question}</p>
      )}

      {isStructuredList ? (
        // Structured Vertical Stacked List
        <div className="rounded-lg border border-slate-200 bg-slate-50 divide-y divide-slate-200 overflow-hidden shadow-2xs">
          {card.options.map((opt, idx) => {
            const isNone = opt.value === "NONE_OF_THESE" || opt.label.toLowerCase() === "none of these";
            const score = (opt as any).confidence_score;
            const scorePct = score ? Math.round(score * 100) : null;

            return (
              <button
                key={idx}
                onClick={() => onSelectOption(opt.value || opt.label)}
                disabled={disabled}
                className={`w-full px-3.5 py-2.5 text-left text-xs transition-colors flex items-center justify-between group disabled:opacity-50 ${
                  isNone
                    ? "bg-slate-100/50 hover:bg-slate-200/50 text-slate-600"
                    : "hover:bg-blue-50 text-slate-800"
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  {(opt as any).activity_code && (
                    <span className="px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 font-mono font-bold text-[10px] text-blue-700 shrink-0">
                      {(opt as any).activity_code}
                    </span>
                  )}
                  <span className={`truncate ${isNone ? "italic text-slate-500" : "font-medium"}`}>
                    {(opt as any).activity_name || opt.label}
                  </span>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {scorePct && (
                    <span className="text-[10px] text-slate-500 font-mono group-hover:text-blue-700 transition-colors">
                      {scorePct}% match
                    </span>
                  )}
                  <ChevronRight className="h-3.5 w-3.5 text-slate-400 group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" />
                </div>
              </button>
            );
          })}
        </div>
      ) : (
        // Compact Chip Wrap for standard 2-choice options
        <div className="flex flex-wrap gap-1.5 pt-1">
          {card.options.map((opt, idx) => {
            const isNone = opt.value === "NONE_OF_THESE" || opt.label.toLowerCase() === "none of these";
            return (
              <button
                key={idx}
                onClick={() => onSelectOption(opt.value || opt.label)}
                disabled={disabled}
                className={`px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all flex items-center gap-1.5 shadow-2xs disabled:opacity-50 group ${
                  isNone
                    ? "bg-slate-100 border-slate-200 text-slate-600 hover:bg-slate-200"
                    : "bg-white hover:bg-blue-50 border-slate-300 hover:border-blue-300 text-slate-700 hover:text-blue-700"
                }`}
              >
                <span>{opt.label}</span>
                <ChevronRight className="h-3 w-3 text-slate-400 group-hover:text-blue-600 transition-colors" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
