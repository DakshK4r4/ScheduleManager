"use client";

import React, { useState, useEffect } from "react";
import {
  X,
  Save,
  Trash2,
  Plus,
  AlertCircle,
  Link as LinkIcon,
  Check,
} from "lucide-react";
import {
  updateActivity,
  createActivity,
  fetchProjectWbs,
  fetchProjectRelationships,
  createRelationship,
  deleteRelationship,
  fetchActivities,
  ApiError,
} from "@/lib/api";
import {
  Activity,
  ActivityStatus,
  Relationship,
  RelationshipType,
  ValidationErrorDetail,
  WBSNode,
} from "@/lib/types";

interface ActivityEditorModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  activity: Activity | null; // null means create new activity
  projectId: string;
}

export default function ActivityEditorModal({
  isOpen,
  onClose,
  onSaved,
  activity,
  projectId,
}: ActivityEditorModalProps) {
  const isEditing = !!activity;

  // Form fields
  const [activityCode, setActivityCode] = useState("");
  const [name, setName] = useState("");
  const [wbsId, setWbsId] = useState<string>("");
  const [status, setStatus] = useState<ActivityStatus>("NOT_STARTED");
  const [plannedStart, setPlannedStart] = useState("");
  const [plannedFinish, setPlannedFinish] = useState("");
  const [actualStart, setActualStart] = useState("");
  const [actualFinish, setActualFinish] = useState("");
  const [duration, setDuration] = useState<string>("");
  const [percentComplete, setPercentComplete] = useState<string>("0");

  // Relationships
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [allActivities, setAllActivities] = useState<Activity[]>([]);
  const [showAddRel, setShowAddRel] = useState(false);
  const [relTargetId, setRelTargetId] = useState("");
  const [relIsPredecessor, setRelIsPredecessor] = useState(true);
  const [relType, setRelType] = useState<RelationshipType>("FS");
  const [relLag, setRelLag] = useState("0");

  // Options
  const [wbsOptions, setWbsOptions] = useState<WBSNode[]>([]);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<ValidationErrorDetail[]>([]);

  // Format ISO to YYYY-MM-DD for date inputs
  const toDateInput = (val: string | null) => {
    if (!val) return "";
    try {
      return val.split("T")[0];
    } catch {
      return "";
    }
  };

  useEffect(() => {
    if (isOpen) {
      setErrorMessage(null);
      setFieldErrors([]);
      setShowAddRel(false);

      fetchProjectWbs(projectId)
        .then(setWbsOptions)
        .catch(console.error);

      fetchActivities(projectId, { page_size: 1000 })
        .then((res) => setAllActivities(res.items))
        .catch(console.error);

      if (activity) {
        setActivityCode(activity.activity_code);
        setName(activity.name);
        setWbsId(activity.wbs_id || "");
        setStatus(activity.status);
        setPlannedStart(toDateInput(activity.planned_start));
        setPlannedFinish(toDateInput(activity.planned_finish));
        setActualStart(toDateInput(activity.actual_start));
        setActualFinish(toDateInput(activity.actual_finish));
        setDuration(activity.original_duration !== null ? String(activity.original_duration) : "");
        setPercentComplete(String(activity.percent_complete ?? 0));

        // Load relationships
        loadRelationships();
      } else {
        setActivityCode("");
        setName("");
        setWbsId("");
        setStatus("NOT_STARTED");
        setPlannedStart("");
        setPlannedFinish("");
        setActualStart("");
        setActualFinish("");
        setDuration("10");
        setPercentComplete("0");
        setRelationships([]);
      }
    }
  }, [isOpen, activity, projectId]);

  const loadRelationships = async () => {
    try {
      const allRels = await fetchProjectRelationships(projectId);
      if (activity) {
        // Filter relationships involving current activity
        const relevant = allRels.filter(
          (r) => r.predecessor_id === activity.id || r.successor_id === activity.id
        );
        setRelationships(relevant);
      }
    } catch (err) {
      console.error("Failed to load relationships:", err);
    }
  };

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setErrorMessage(null);
    setFieldErrors([]);

    const payload: any = {
      activity_code: activityCode.trim(),
      name: name.trim(),
      wbs_id: wbsId || null,
      status,
      planned_start: plannedStart ? `${plannedStart}T08:00:00` : null,
      planned_finish: plannedFinish ? `${plannedFinish}T17:00:00` : null,
      actual_start: actualStart ? `${actualStart}T08:00:00` : null,
      actual_finish: actualFinish ? `${actualFinish}T17:00:00` : null,
      original_duration: duration ? parseFloat(duration) : null,
      percent_complete: percentComplete ? parseFloat(percentComplete) : 0,
    };

    try {
      if (isEditing && activity) {
        await updateActivity(activity.id, payload);
      } else {
        await createActivity(projectId, payload);
      }
      onSaved();
      onClose();
    } catch (err: any) {
      if (err instanceof ApiError && err.validation) {
        setErrorMessage(err.validation.error);
        setFieldErrors(err.validation.errors || []);
      } else {
        setErrorMessage(err.message || "Failed to save activity changes.");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleAddRelationship = async () => {
    if (!activity || !relTargetId) return;

    try {
      await createRelationship(projectId, {
        predecessor_id: relIsPredecessor ? relTargetId : activity.id,
        successor_id: relIsPredecessor ? activity.id : relTargetId,
        relationship_type: relType,
        lag: parseFloat(relLag) || 0,
      });
      setShowAddRel(false);
      setRelTargetId("");
      loadRelationships();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to create relationship.");
    }
  };

  const handleDeleteRel = async (relId: string) => {
    try {
      await deleteRelationship(relId);
      loadRelationships();
    } catch (err: any) {
      setErrorMessage(err.message || "Failed to delete relationship.");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-2xl rounded-xl bg-white shadow-2xl border border-slate-200 overflow-hidden my-8">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <div>
            <h3 className="text-lg font-bold text-slate-900">
              {isEditing ? `Edit Activity: ${activity?.activity_code}` : "New Activity"}
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Update schedule data directly in PostgreSQL
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-6">
          {/* Error Banner */}
          {errorMessage && (
            <div className="rounded-lg bg-red-50 border border-red-200 p-4">
              <div className="flex items-start gap-3">
                <AlertCircle className="h-5 w-5 text-red-600 mt-0.5 flex-shrink-0" />
                <div>
                  <h4 className="text-sm font-semibold text-red-900">{errorMessage}</h4>
                  {fieldErrors.length > 0 && (
                    <ul className="mt-2 list-disc list-inside text-xs text-red-700 space-y-1">
                      {fieldErrors.map((fe, idx) => (
                        <li key={idx}>
                          {fe.field ? `${fe.field}: ` : ""}
                          {fe.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Basic Fields */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Activity ID / Code *
              </label>
              <input
                type="text"
                required
                value={activityCode}
                onChange={(e) => setActivityCode(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Activity Name *
              </label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                WBS Element
              </label>
              <select
                value={wbsId}
                onChange={(e) => setWbsId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">(None / Project Root)</option>
                {wbsOptions.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.code} - {w.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                Status
              </label>
              <select
                value={status}
                onChange={(e) => {
                  const s = e.target.value as ActivityStatus;
                  setStatus(s);
                  if (s === "COMPLETED") setPercentComplete("100");
                  else if (s === "NOT_STARTED") setPercentComplete("0");
                }}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="NOT_STARTED">Not Started</option>
                <option value="IN_PROGRESS">In Progress</option>
                <option value="COMPLETED">Completed</option>
              </select>
            </div>
          </div>

          {/* Dates & Durations */}
          <div className="border-t border-slate-100 pt-4">
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
              Schedule Dates &amp; Progress
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Planned Start
                </label>
                <input
                  type="date"
                  value={plannedStart}
                  onChange={(e) => setPlannedStart(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Planned Finish
                </label>
                <input
                  type="date"
                  value={plannedFinish}
                  onChange={(e) => setPlannedFinish(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Original Duration (Days)
                </label>
                <input
                  type="number"
                  step="0.1"
                  value={duration}
                  onChange={(e) => setDuration(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Percent Complete (%)
                </label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  value={percentComplete}
                  onChange={(e) => setPercentComplete(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Actual Start
                </label>
                <input
                  type="date"
                  value={actualStart}
                  onChange={(e) => setActualStart(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">
                  Actual Finish
                </label>
                <input
                  type="date"
                  value={actualFinish}
                  onChange={(e) => setActualFinish(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Relationships Section (Only in Edit mode) */}
          {isEditing && (
            <div className="border-t border-slate-100 pt-4">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  Logic Relationships ({relationships.length})
                </h4>
                <button
                  type="button"
                  onClick={() => setShowAddRel(!showAddRel)}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {showAddRel ? "Cancel" : "Add Link"}
                </button>
              </div>

              {/* Add Relationship Form */}
              {showAddRel && (
                <div className="mb-4 rounded-lg bg-slate-50 border border-slate-200 p-3 space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                    <div>
                      <label className="block font-medium text-slate-600 mb-1">Relation Type</label>
                      <select
                        value={relIsPredecessor ? "pred" : "succ"}
                        onChange={(e) => setRelIsPredecessor(e.target.value === "pred")}
                        className="w-full rounded border border-slate-300 p-1.5 text-xs bg-white"
                      >
                        <option value="pred">Predecessor (comes before this activity)</option>
                        <option value="succ">Successor (comes after this activity)</option>
                      </select>
                    </div>

                    <div>
                      <label className="block font-medium text-slate-600 mb-1">Target Activity</label>
                      <select
                        value={relTargetId}
                        onChange={(e) => setRelTargetId(e.target.value)}
                        className="w-full rounded border border-slate-300 p-1.5 text-xs bg-white"
                      >
                        <option value="">Select activity...</option>
                        {allActivities
                          .filter((a) => a.id !== activity?.id)
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.activity_code} - {a.name}
                            </option>
                          ))}
                      </select>
                    </div>

                    <div>
                      <label className="block font-medium text-slate-600 mb-1">Link Type</label>
                      <select
                        value={relType}
                        onChange={(e) => setRelType(e.target.value as RelationshipType)}
                        className="w-full rounded border border-slate-300 p-1.5 text-xs bg-white"
                      >
                        <option value="FS">Finish to Start (FS)</option>
                        <option value="SS">Start to Start (SS)</option>
                        <option value="FF">Finish to Finish (FF)</option>
                        <option value="SF">Start to Finish (SF)</option>
                      </select>
                    </div>

                    <div>
                      <label className="block font-medium text-slate-600 mb-1">Lag (Days)</label>
                      <input
                        type="number"
                        step="0.5"
                        value={relLag}
                        onChange={(e) => setRelLag(e.target.value)}
                        className="w-full rounded border border-slate-300 p-1.5 text-xs bg-white"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      disabled={!relTargetId}
                      onClick={handleAddRelationship}
                      className="rounded bg-blue-600 px-3 py-1 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
                    >
                      Save Relationship
                    </button>
                  </div>
                </div>
              )}

              {/* Relationship List */}
              {relationships.length === 0 ? (
                <p className="text-xs text-slate-400 italic">No logic links defined for this activity.</p>
              ) : (
                <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 overflow-hidden text-xs">
                  {relationships.map((r) => {
                    const isPred = r.successor_id === activity?.id;
                    const otherCode = isPred ? r.predecessor_code : r.successor_code;
                    const otherName = isPred ? r.predecessor_name : r.successor_name;

                    return (
                      <div
                        key={r.id}
                        className="flex items-center justify-between p-2.5 hover:bg-slate-50"
                      >
                        <div className="flex items-center gap-2">
                          <LinkIcon className="h-3.5 w-3.5 text-slate-400" />
                          <span className="font-semibold text-slate-700">
                            {isPred ? "Predecessor:" : "Successor:"}
                          </span>
                          <span className="font-mono text-blue-600 font-medium">{otherCode}</span>
                          <span className="text-slate-500 truncate max-w-[180px]">{otherName}</span>
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                            {r.relationship_type}
                            {r.lag ? ` +${r.lag}d` : ""}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDeleteRel(r.id)}
                          className="text-slate-400 hover:text-red-600 p-1 rounded transition-colors"
                          title="Delete link"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Footer Actions */}
          <div className="flex items-center justify-end gap-3 border-t border-slate-100 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-50 transition-colors"
            >
              <Save className="h-4 w-4" />
              {saving ? "Saving..." : isEditing ? "Save Changes" : "Create Activity"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
