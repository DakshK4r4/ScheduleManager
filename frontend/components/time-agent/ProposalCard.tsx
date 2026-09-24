"use client";

import React, { useState } from "react";
import {
  Sparkles,
  ArrowRight,
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  FileText,
  Lock,
  Layers,
} from "lucide-react";
import { TimeAgentActionCard } from "@/lib/types";

interface ProposalCardProps {
  card: TimeAgentActionCard;
  onConfirm: (proposalId: string) => void;
  onReject: (proposalId: string) => void;
  actionLoading: string | null;
  conversationLanguage?: string | null;
}

export default function ProposalCard({
  card,
  onConfirm,
  onReject,
  actionLoading,
  conversationLanguage,
}: ProposalCardProps) {
  const [showReview, setShowReview] = useState(false);
  const proposalId = card.proposal_id;
  const status = card.proposal_status || "PENDING";
  const isLoading = actionLoading === proposalId;

  const isApplied = status === "APPLIED" || status === "CONSUMED";
  const isRejected = status === "REJECTED";
  const isExpired = status === "EXPIRED";
  const isPending = status === "PENDING";

  const lang = (conversationLanguage || "").toLowerCase();
  const isHindi = lang === "hi" || lang === "hindi";
  const isHinglish = lang === "hinglish";

  // Dynamic labels
  const confirmText = card.confirm_label || (isHindi ? "अपडेट की पुष्टि करें" : isHinglish ? "Update Confirm Karein" : "Confirm Update");
  const rejectText = card.reject_label || (isHindi ? "अस्वीकार करें" : isHinglish ? "Reject Karein" : "Reject");
  const reviewText = card.review_label || (
    showReview
      ? (isHindi ? "समीक्षा विवरण छुपाएं" : isHinglish ? "Details Chhupayein" : "Hide Verification Details")
      : (isHindi ? "समीक्षा करें" : isHinglish ? "Review Karein" : "Review Details & Audit Safety")
  );
  const loadingText = isHindi
    ? "शेड्यूल में अपडेट किया जा रहा है..."
    : isHinglish
    ? "Schedule update ho raha hai..."
    : "Committing to Schedule...";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-3 shadow-xs">
      {/* Header Bar */}
      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
        <span className="text-xs font-bold uppercase tracking-wider text-blue-700 flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5" /> Proposed Schedule Update
        </span>
        <div className="flex items-center gap-2">
          {card.execution_date && (
            <span className="text-[11px] text-slate-500 font-mono">
              Date: {card.execution_date}
            </span>
          )}

          {/* Authoritative Status Badge */}
          {isApplied && (
            <span className="px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1">
              <CheckCircle2 className="h-3 w-3 text-emerald-600" />
              Applied
            </span>
          )}
          {isRejected && (
            <span className="px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-rose-50 text-rose-700 border border-rose-200 flex items-center gap-1">
              <XCircle className="h-3 w-3 text-rose-600" />
              Rejected
            </span>
          )}
          {isExpired && (
            <span className="px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-slate-100 text-slate-600 border border-slate-200 flex items-center gap-1">
              <Clock className="h-3 w-3 text-slate-500" />
              Expired (5m TTL)
            </span>
          )}
          {isPending && (
            <span className="px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-blue-50 text-blue-700 border border-blue-200">
              Awaiting Approval
            </span>
          )}
        </div>
      </div>

      {/* Activity Details */}
      <div className="grid grid-cols-2 gap-2 text-xs bg-slate-50 p-3 rounded-lg border border-slate-200">
        <div>
          <span className="text-slate-500 block text-[10px] font-semibold uppercase tracking-wider">
            Target Activity
          </span>
          <span className="font-bold text-slate-900 font-mono text-sm block mt-0.5">
            {card.activity_code || "Unknown"}
          </span>
          <span className="text-slate-600 block text-xs truncate mt-0.5">
            {card.activity_name || "N/A"}
          </span>
        </div>
        <div>
          <span className="text-slate-500 block text-[10px] font-semibold uppercase tracking-wider">
            Progress Impact
          </span>
          <div className="flex items-center gap-1.5 font-bold text-sm mt-0.5">
            <span className="text-slate-600">{card.current_percent ?? 0.0}%</span>
            <ArrowRight className="h-3.5 w-3.5 text-blue-600" />
            <span className="text-emerald-700 font-mono">
              {card.proposed_percent ?? 0.0}%
            </span>
          </div>
          {card.incremental_quantity !== null &&
            card.incremental_quantity !== undefined && (
              <span className="text-[11px] text-blue-700 font-mono font-semibold block mt-0.5">
                +{card.incremental_quantity} {card.unit || ""}
              </span>
            )}
        </div>
      </div>

      {/* Review Details Toggle */}
      <div className="pt-0.5">
        <button
          type="button"
          onClick={() => setShowReview(!showReview)}
          className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 hover:text-blue-700 transition-colors"
        >
          <FileText className="h-3.5 w-3.5 text-slate-500" />
          <span>{reviewText}</span>
          {showReview ? (
            <ChevronUp className="h-3.5 w-3.5 ml-0.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 ml-0.5" />
          )}
        </button>

        {showReview && (
          <div className="mt-2 p-3 bg-slate-50 rounded-lg border border-slate-200 text-xs space-y-2 text-slate-700 animate-in fade-in duration-200">
            <div className="grid grid-cols-2 gap-2 pb-2 border-b border-slate-200 font-mono text-[10px]">
              <div>
                <span className="text-slate-400 block font-sans uppercase font-semibold">Proposal ID</span>
                <span className="text-slate-800 truncate block select-all">
                  {proposalId || "N/A"}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block font-sans uppercase font-semibold">Execution Event ID</span>
                <span className="text-slate-800 truncate block select-all">
                  {card.event_id || "N/A"}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block font-sans uppercase font-semibold">Activity ID</span>
                <span className="text-slate-800 truncate block select-all">
                  {card.activity_id || "N/A"}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block font-sans uppercase font-semibold">Target Reporting Date</span>
                <span className="text-slate-800 block">
                  {card.execution_date || "Current System Date"}
                </span>
              </div>
            </div>

            <div className="space-y-1.5 pt-1 text-[11px]">
              <div className="flex items-start gap-1.5">
                <Lock className="h-3.5 w-3.5 text-amber-600 mt-0.5 shrink-0" />
                <p>
                  <strong className="text-slate-900">Concurrency Guard:</strong> Schedule mutation executes via an ACID transaction with PostgreSQL row-level locking (<code>SELECT ... FOR UPDATE</code>).
                </p>
              </div>
              <div className="flex items-start gap-1.5">
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 mt-0.5 shrink-0" />
                <p>
                  <strong className="text-slate-900">Audit Ledger:</strong> Every confirmed update is permanently recorded in <code>actual_progress_ledger</code> and <code>schedule_audit_logs</code>.
                </p>
              </div>
              <div className="flex items-start gap-1.5">
                <Clock className="h-3.5 w-3.5 text-blue-600 mt-0.5 shrink-0" />
                <p>
                  <strong className="text-slate-900">5-Minute TTL:</strong> Unconfirmed proposals automatically expire after 5 minutes to prevent stale baseline overwrites.
                </p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Action Buttons */}
      {isPending && proposalId && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => onConfirm(proposalId)}
            disabled={isLoading}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs shadow-xs transition-colors disabled:opacity-50"
          >
            {isLoading ? (
              <>
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                <span>{loadingText}</span>
              </>
            ) : (
              <>
                <CheckCircle2 className="h-3.5 w-3.5" />
                <span>{confirmText}</span>
              </>
            )}
          </button>

          <button
            type="button"
            onClick={() => onReject(proposalId)}
            disabled={isLoading}
            className="py-2 px-3 rounded-lg border border-slate-300 hover:bg-slate-50 text-slate-700 font-semibold text-xs transition-colors disabled:opacity-50"
          >
            {rejectText}
          </button>
        </div>
      )}
    </div>
  );
}
