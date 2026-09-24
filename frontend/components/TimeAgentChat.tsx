"use client";

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Send,
  Paperclip,
  Bot,
  User,
  CheckCircle2,
  Clock,
  AlertCircle,
  Sparkles,
  ArrowRight,
  RefreshCw,
  FileText,
  Menu,
  Plus,
  Globe,
  X,
} from "lucide-react";
import {
  startOrGetConversation,
  fetchAgentConversations,
  getAgentConversation,
  pinAgentConversation,
  deleteAgentConversation,
  sendAgentMessage,
  sendAgentVoiceMessage,
  uploadAgentAttachment,
  confirmUpdateProposal,
  confirmTimeAgentBulkProposal,
  fetchProject,
} from "@/lib/api";
import {
  TimeAgentConversation,
  TimeAgentConversationSummary,
  TimeAgentMessage,
  TimeAgentActionCard,
  PendingAction,
} from "@/lib/types";
import ConversationSidebar from "./time-agent/ConversationSidebar";
import ProposalCard from "./time-agent/ProposalCard";
import ClarificationCard from "./time-agent/ClarificationCard";
import VoiceInputButton from "./time-agent/VoiceInputButton";
import FormattedMessage from "./time-agent/FormattedMessage";
import MessageAudioPlayer from "./time-agent/MessageAudioPlayer";
import { formatMessageTime } from "@/lib/timeUtils";

function sanitizeErrorMessage(rawError: any): string {
  if (!rawError) return "An unexpected error occurred. Please try again.";
  const errStr = typeof rawError === "string" ? rawError : rawError.message || String(rawError);
  if (
    errStr.includes("Traceback") ||
    errStr.includes("sqlalchemy") ||
    errStr.includes("psycopg") ||
    errStr.includes("SELECT ") ||
    errStr.includes("password") ||
    errStr.includes("/app/")
  ) {
    return "Unable to process schedule request at this moment. The system logged the issue; please try again.";
  }
  return errStr;
}

function getLanguageDisplayName(lang?: string | null, style?: string | null): string {
  if (!lang) return "English";
  const lower = lang.toLowerCase();
  const lowerStyle = (style || "").toLowerCase();
  if (lowerStyle === "hinglish" || lower === "hinglish") return "Hinglish";
  if (lower === "hi" || lower === "hi-in") return "हिंदी (Hindi)";
  if (lower === "ta" || lower === "ta-in") return "தமிழ் (Tamil)";
  if (lower === "te" || lower === "te-in") return "తెలుగు (Telugu)";
  if (lower === "bn" || lower === "bn-in") return "বাংলা (Bengali)";
  if (lower === "mr" || lower === "mr-in") return "मराठी (Marathi)";
  if (lower === "gu" || lower === "gu-in") return "ગુજરાતી (Gujarati)";
  if (lower === "kn" || lower === "kn-in") return "ಕನ್ನಡ (Kannada)";
  if (lower === "ml" || lower === "ml-in") return "മലയാളം (Malayalam)";
  if (lower === "pa" || lower === "pa-in") return "ਪੰਜਾਬੀ (Punjabi)";
  if (lower === "od" || lower === "od-in") return "ଓଡ଼ିଆ (Odia)";
  if (lower === "en" || lower === "en-in" || lower === "en-us") return "English";
  return lang.toUpperCase();
}

interface TimeAgentChatProps {
  projectId: string;
  projectName?: string;
  initialActivityId?: string | null;
  onScheduleUpdated?: () => void;
}

export default function TimeAgentChat({
  projectId,
  projectName: initialProjectName,
  initialActivityId,
  onScheduleUpdated,
}: TimeAgentChatProps) {
  const [conversations, setConversations] = useState<TimeAgentConversationSummary[]>([]);
  const [activeConversation, setActiveConversation] = useState<TimeAgentConversation | null>(null);
  const [messages, setMessages] = useState<TimeAgentMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [projectName, setProjectName] = useState<string>(initialProjectName || "");

  const [loadingList, setLoadingList] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState(false);

  // Delete Confirmation Modal State
  const [deletingConversation, setDeletingConversation] = useState<TimeAgentConversationSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Active TTS playback coordination across messages
  const [activeAudioMessageId, setActiveAudioMessageId] = useState<string | null>(null);
  const stopActiveAudioRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    // Stop playing audio when switching conversations
    if (stopActiveAudioRef.current) {
      stopActiveAudioRef.current();
      stopActiveAudioRef.current = null;
    }
    setActiveAudioMessageId(null);
  }, [activeConversation?.conversation_id]);

  // Identify if there is an active proposal or bulk card awaiting user decision in messages
  const pendingActionCard = useMemo(() => {
    if (!messages || messages.length === 0) return null;
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.sender === "AGENT" && msg.message_metadata) {
        const card = msg.message_metadata;
        if (card.type === "BULK_SCOPE_PROPOSAL") {
          const isResolved =
            card.proposal_status === "APPLIED" ||
            card.proposal_status === "CANCELLED" ||
            card.proposal_status === "REJECTED";
          if (!isResolved) {
            return { card, messageId: msg.id, type: "BULK_SCOPE_PROPOSAL" as const };
          }
        }
        if (card.type === "PROPOSAL_CONFIRMATION") {
          const isResolved =
            card.proposal_status === "APPLIED" ||
            card.proposal_status === "REJECTED" ||
            card.proposal_status === "CONSUMED" ||
            card.proposal_status === "EXPIRED" ||
            card.proposal_status === "CANCELLED";
          if (!isResolved) {
            return { card, messageId: msg.id, type: "PROPOSAL_CONFIRMATION" as const };
          }
        }
        break;
      }
    }
    return null;
  }, [messages]);

  // Explicit structured pending action: combines message history and authoritative persisted backend state
  const currentPendingAction = useMemo<PendingAction | null>(() => {
    if (pendingActionCard) {
      if (pendingActionCard.type === "BULK_SCOPE_PROPOSAL") {
        return {
          type: "BULK_UPDATE_CONFIRMATION",
          proposal_id: pendingActionCard.card.proposal_id,
          status: "PENDING",
          activity_count:
            pendingActionCard.card.bulk_count ||
            (pendingActionCard.card.bulk_activities?.length ?? 0),
          target_percent: pendingActionCard.card.target_percent ?? 100,
          bulk_activities: pendingActionCard.card.bulk_activities,
        };
      }
      if (pendingActionCard.type === "PROPOSAL_CONFIRMATION") {
        return {
          type: "PROPOSAL_CONFIRMATION",
          proposal_id: pendingActionCard.card.proposal_id,
          status: "PENDING",
        };
      }
    }
    // Fall back to authoritative backend state on fresh page load or navigation
    if (
      activeConversation?.pending_action &&
      activeConversation.pending_action.status === "PENDING"
    ) {
      return activeConversation.pending_action;
    }
    return null;
  }, [pendingActionCard, activeConversation?.pending_action]);

  const isBulkConfirmationPending =
    currentPendingAction?.type === "BULK_UPDATE_CONFIRMATION" &&
    currentPendingAction.status === "PENDING";

  const hasPendingActionCard = Boolean(pendingActionCard || currentPendingAction);
  const isChatLocked = Boolean(isBulkConfirmationPending || hasPendingActionCard);

  const pendingBulkActivities =
    currentPendingAction?.bulk_activities ||
    pendingActionCard?.card.bulk_activities ||
    activeConversation?.pending_action?.bulk_activities ||
    [];
  const pendingBulkCount =
    currentPendingAction?.activity_count ||
    pendingActionCard?.card.bulk_count ||
    pendingBulkActivities.length;
  const pendingBulkTargetPct =
    currentPendingAction?.target_percent ??
    pendingActionCard?.card.target_percent ??
    100;

  // Local storage key strictly scoped to current project
  const storageKey = `time_agent_selected_conv_${projectId}`;

  // Fetch project name if not passed in props
  useEffect(() => {
    if (!projectName && projectId) {
      fetchProject(projectId)
        .then((p) => setProjectName(p.name || p.project_code || ""))
        .catch(() => {});
    }
  }, [projectId, projectName]);

  // Load conversation summaries for current project
  const loadConversationList = useCallback(
    async (query?: string) => {
      if (!projectId) return;
      try {
        setLoadingList(true);
        const list = await fetchAgentConversations(projectId, query);
        setConversations(list);
      } catch (err: any) {
        console.error("Failed to fetch conversation list:", err);
      } finally {
        setLoadingList(false);
      }
    },
    [projectId]
  );

  // Debounced search query
  useEffect(() => {
    const timer = setTimeout(() => {
      loadConversationList(searchQuery);
    }, 250);
    return () => clearTimeout(timer);
  }, [searchQuery, loadConversationList]);

  // Load a specific conversation by ID
  const selectConversation = useCallback(
    async (convId: string) => {
      if (!projectId || !convId) return;
      try {
        setLoadingMessages(true);
        setError(null);
        const fullConv = await getAgentConversation(projectId, convId);
        setActiveConversation(fullConv);
        setMessages(fullConv.history || []);
        // Remember in UI-local storage for browser refresh
        try {
          localStorage.setItem(storageKey, convId);
        } catch {}
      } catch (err: any) {
        console.error("Failed to load conversation:", err);
        setError(err.message || "Failed to load conversation history.");
      } finally {
        setLoadingMessages(false);
      }
    },
    [projectId, storageKey]
  );

  // Initialize workspace on project mount
  useEffect(() => {
    let isMounted = true;

    async function initWorkspace() {
      if (!projectId) return;
      try {
        setLoadingMessages(true);
        const list = await fetchAgentConversations(projectId);
        if (!isMounted) return;
        setConversations(list);

        // Check if there was a previously remembered conversation for this project
        let targetConvId: string | null = null;
        try {
          targetConvId = localStorage.getItem(storageKey);
        } catch {}

        // Verify saved convId actually belongs to this project's list
        const existsInProject = list.some((c) => c.id === targetConvId);

        if (targetConvId && existsInProject) {
          await selectConversation(targetConvId);
        } else if (list.length > 0) {
          // Select most recently updated conversation
          await selectConversation(list[0].id);
        } else {
          // If no conversations exist yet, create or retrieve initial conversation
          const newConv = await startOrGetConversation(projectId, initialActivityId || undefined);
          if (isMounted) {
            setActiveConversation(newConv);
            setMessages(newConv.history || []);
            try {
              localStorage.setItem(storageKey, newConv.conversation_id);
            } catch {}
            // Refresh conversation list
            const updatedList = await fetchAgentConversations(projectId);
            if (isMounted) setConversations(updatedList);
          }
        }
      } catch (err: any) {
        if (isMounted) {
          setError(sanitizeErrorMessage(err));
        }
      } finally {
        if (isMounted) setLoadingMessages(false);
      }
    }

    initWorkspace();

    return () => {
      isMounted = false;
    };
  }, [projectId, storageKey, initialActivityId, selectConversation]);

  // Scroll to bottom when messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loadingMessages, sendingMessage]);

  // Handle Voice Recording via Sarvam STT
  const handleVoiceAudioRecorded = async (audioBlob: Blob, signal?: AbortSignal) => {
    if (!projectId || isChatLocked) return;

    let targetConvId = activeConversation?.conversation_id;
    if (!targetConvId) {
      const newConv = await startOrGetConversation(projectId, initialActivityId || undefined);
      setActiveConversation(newConv);
      targetConvId = newConv.conversation_id;
    }

    setSendingMessage(true);
    setError(null);
    try {
      const res = await sendAgentVoiceMessage(
        projectId,
        targetConvId,
        audioBlob,
        "voice_recording.webm",
        "site-supervisor",
        signal
      );

      // If aborted/cancelled during request, do not update message or conversation state
      if (signal?.aborted) return;

      // 1. Add transcribed user message
      const userMsg: TimeAgentMessage = {
        id: `voice-user-${Date.now()}`,
        sender: "USER",
        content: res.transcription || res.transcript,
        created_at: new Date().toISOString(),
      };

      // 2. Add authoritative agent reply message
      const agentMsg: TimeAgentMessage = {
        id: res.message_id,
        sender: res.sender as "AGENT",
        content: res.reply_text,
        message_metadata: res.action_card || null,
        created_at: res.created_at || new Date().toISOString(),
      };

      setMessages((prev) => [...prev, userMsg, agentMsg]);

      // 3. Update active conversation with locked language state
      setActiveConversation((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          language: res.conversation_language || prev.language,
          conversation_language: res.conversation_language || prev.conversation_language,
          conversation_style: res.conversation_style || prev.conversation_style,
          language_locked: res.language_locked ?? prev.language_locked,
        };
      });

      await loadConversationList();
    } catch (err: any) {
      if (err?.name === "AbortError" || signal?.aborted) {
        // User cancelled transcription: silently return without error or side-effects
        return;
      }
      console.error("Failed to process voice message:", err);
      const msg = sanitizeErrorMessage(err);
      setError(msg || "Couldn't transcribe the audio. Please try again.");
      throw err;
    } finally {
      setSendingMessage(false);
    }
  };

  // Handle "+ New Chat"
  const handleNewChat = async () => {
    if (!projectId || sendingMessage) return;
    try {
      setLoadingMessages(true);
      setError(null);
      setSuccessBanner(null);

      // Force creation of a brand new clean conversation under CURRENT project
      const newConv = await startOrGetConversation(projectId, undefined, true);
      setActiveConversation(newConv);
      setMessages(newConv.history || []);
      try {
        localStorage.setItem(storageKey, newConv.conversation_id);
      } catch {}

      // Refresh list to show the new chat
      await loadConversationList();
    } catch (err: any) {
      setError(sanitizeErrorMessage(err));
    } finally {
      setLoadingMessages(false);
    }
  };

  // Handle Pin / Unpin Conversation
  const handlePinConversation = async (convId: string, isPinned: boolean) => {
    try {
      // Optimistic local state update
      setConversations((prev) =>
        prev.map((c) => (c.id === convId ? { ...c, is_pinned: isPinned } : c))
      );
      if (activeConversation && activeConversation.conversation_id === convId) {
        setActiveConversation({ ...activeConversation, is_pinned: isPinned });
      }

      await pinAgentConversation(projectId, convId, isPinned);
      setSuccessBanner(isPinned ? "Conversation pinned." : "Conversation unpinned.");
      // Reload list to sync authoritative ordering
      loadConversationList(searchQuery);
    } catch (err: any) {
      setError(
        isPinned
          ? "Could not pin this conversation. Please try again."
          : "Could not unpin this conversation. Please try again."
      );
      loadConversationList(searchQuery);
    }
  };

  // Trigger Delete Confirmation Modal
  const handleRequestDelete = (conv: TimeAgentConversationSummary) => {
    setDeletingConversation(conv);
  };

  // Confirm Delete Action
  const handleConfirmDelete = async () => {
    if (!deletingConversation) return;
    try {
      setIsDeleting(true);
      setError(null);
      await deleteAgentConversation(projectId, deletingConversation.id);

      const deletedId = deletingConversation.id;
      const updatedList = conversations.filter((c) => c.id !== deletedId);
      setConversations(updatedList);
      setSuccessBanner("Conversation deleted.");

      // If deleted conversation is currently open, switch to next available or empty state
      if (activeConversation?.conversation_id === deletedId) {
        if (updatedList.length > 0) {
          selectConversation(updatedList[0].id);
        } else {
          setActiveConversation(null);
          setMessages([]);
          try {
            localStorage.removeItem(storageKey);
          } catch {}
        }
      }
      setDeletingConversation(null);
    } catch (err: any) {
      setError("Could not delete this conversation. Please try again.");
    } finally {
      setIsDeleting(false);
    }
  };

  // Handle Send Text Message
  const handleSendMessage = async (textToSend?: string) => {
    const text = textToSend || inputText;
    if (!text.trim() || !activeConversation || sendingMessage || (isChatLocked && !textToSend)) {
      if (isBulkConfirmationPending && !textToSend) {
        setError("Please confirm or cancel the pending bulk update first.");
      }
      return;
    }

    const userText = text.trim();
    if (!textToSend) {
      setInputText("");
    }

    const tempUserMsg: TimeAgentMessage = {
      id: `temp-${Date.now()}`,
      sender: "USER",
      content: userText,
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, tempUserMsg]);
    setSendingMessage(true);
    setError(null);
    setSuccessBanner(null);

    try {
      const resp = await sendAgentMessage(
        projectId,
        activeConversation.conversation_id,
        userText
      );

      const agentMsg: TimeAgentMessage = {
        id: resp.message_id,
        sender: resp.sender as "AGENT" | "SYSTEM",
        content: resp.reply_text,
        message_metadata: resp.action_card || null,
        created_at: resp.created_at || new Date().toISOString(),
      };

      setMessages((prev) => [...prev, agentMsg]);

      // If progress update was directly applied or scheduled, notify parent workspace
      if (
        resp.action_card?.type === "PROPOSAL_CONFIRMATION" &&
        resp.action_card.proposal_status === "APPLIED" &&
        onScheduleUpdated
      ) {
        onScheduleUpdated();
      }

      // Refresh sidebar list to update timestamps and auto-generated titles
      try {
        const list = await fetchAgentConversations(projectId, searchQuery);
        setConversations(list);
        const currentSummary = list.find((c) => c.id === activeConversation.conversation_id);
        if (currentSummary?.language && currentSummary.language !== activeConversation.language) {
          setActiveConversation((prev) => (prev ? { ...prev, language: currentSummary.language } : prev));
        }
      } catch {
        loadConversationList();
      }
    } catch (err: any) {
      console.error("Failed to send agent message:", err);
      setError(sanitizeErrorMessage(err));
    } finally {
      setSendingMessage(false);
    }
  };

  // Handle Clarification Option Selection
  const handleSelectClarificationOption = async (optionValue: string) => {
    await handleSendMessage(optionValue);
  };

  // Handle Bulk Scope Confirmation
  const handleConfirmBulkProposal = async (activityIds?: string[], targetPercent?: number) => {
    if (!activeConversation || Boolean(actionLoading)) return;
    try {
      setActionLoading("bulk");
      setError(null);

      const targetPct = targetPercent ?? pendingBulkTargetPct ?? 100;
      const ids =
        activityIds && activityIds.length > 0
          ? activityIds
          : pendingBulkActivities.map((a: any) => a.activity_id);

      if (!ids || ids.length === 0) {
        setError("No activities found for bulk confirmation.");
        return;
      }

      const res = await confirmTimeAgentBulkProposal(
        projectId,
        activeConversation.conversation_id,
        ids,
        "CONFIRM",
        targetPct
      );

      setSuccessBanner(
        `Bulk update confirmed! Updated ${res.updated_count} activities to ${targetPct}%.`
      );

      setMessages((prev) =>
        prev.map((m) => {
          if (m.message_metadata?.type === "BULK_SCOPE_PROPOSAL") {
            return {
              ...m,
              message_metadata: {
                ...m.message_metadata,
                proposal_status: "APPLIED",
              },
            };
          }
          return m;
        })
      );

      setActiveConversation((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          status: "ACTIVE",
          pending_action: null,
        };
      });

      if (onScheduleUpdated) {
        onScheduleUpdated();
      }

      selectConversation(activeConversation.conversation_id);
    } catch (err: any) {
      console.error("Failed to confirm bulk proposal:", err);
      setError(sanitizeErrorMessage(err));
    } finally {
      setActionLoading(null);
    }
  };

  // Handle Bulk Scope Cancellation (Fast Path)
  const handleCancelBulkProposal = async () => {
    if (!activeConversation || Boolean(actionLoading)) return;
    try {
      setActionLoading("bulk-cancel");
      setError(null);

      // Optimistically update message state so proposal_status becomes CANCELLED and input unlocks immediately
      setMessages((prev) =>
        prev.map((m) => {
          if (m.message_metadata?.type === "BULK_SCOPE_PROPOSAL") {
            return {
              ...m,
              message_metadata: {
                ...m.message_metadata,
                proposal_status: "CANCELLED",
              },
            };
          }
          return m;
        })
      );

      // Optimistically clear activeConversation pending_action and reset status
      setActiveConversation((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          status: "ACTIVE",
          pending_action: null,
        };
      });

      await confirmTimeAgentBulkProposal(
        projectId,
        activeConversation.conversation_id,
        [],
        "CANCEL"
      );

      setSuccessBanner("Bulk update cancelled. No changes applied.");
      const isHindi = activeConversation.language === "hi" || activeConversation.language === "hi-IN";
      const isHinglish = activeConversation.language === "hinglish";
      const cancelAgentMsg: TimeAgentMessage = {
        id: `cancel-bulk-${Date.now()}`,
        sender: "AGENT",
        content: isHindi
          ? "Bulk update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
          : isHinglish
          ? "Bulk update cancel kar diya gaya hai. Authoritative schedule mein koi change nahi hua."
          : "Bulk update was cancelled. No changes were applied to the schedule.",
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, cancelAgentMsg]);
    } catch (err: any) {
      console.error("Failed to cancel bulk proposal:", err);
      setError(sanitizeErrorMessage(err));
      selectConversation(activeConversation.conversation_id);
    } finally {
      setActionLoading(null);
    }
  };

  // Handle Proposal Confirmation
  const handleConfirmProposal = async (proposalId: string) => {
    if (!activeConversation) return;
    try {
      setActionLoading(proposalId);
      setError(null);
      const res = await confirmUpdateProposal(
        projectId,
        activeConversation.conversation_id,
        proposalId,
        "CONFIRM"
      );

      setSuccessBanner(
        `Update confirmed! ${res.activity_code} advanced from ${res.previous_percent}% to ${res.new_percent}%.`
      );

      setMessages((prev) =>
        prev.map((m) => {
          if (m.message_metadata?.proposal_id === proposalId) {
            return {
              ...m,
              message_metadata: {
                ...m.message_metadata,
                proposal_status: "APPLIED",
              },
            };
          }
          return m;
        })
      );

      if (onScheduleUpdated) {
        onScheduleUpdated();
      }

      selectConversation(activeConversation.conversation_id);
    } catch (err: any) {
      setError(sanitizeErrorMessage(err));
    } finally {
      setActionLoading(null);
    }
  };

  // Handle Proposal Rejection
  const handleRejectProposal = async (proposalId: string) => {
    if (!activeConversation) return;
    try {
      setActionLoading(proposalId);
      setError(null);
      // Optimistic state update so input immediately unlocks and card shows Rejected
      setMessages((prev) =>
        prev.map((m) => {
          if (m.message_metadata?.proposal_id === proposalId) {
            return {
              ...m,
              message_metadata: {
                ...m.message_metadata,
                proposal_status: "REJECTED",
              },
            };
          }
          return m;
        })
      );

      await confirmUpdateProposal(
        projectId,
        activeConversation.conversation_id,
        proposalId,
        "REJECT"
      );

      setSuccessBanner("Proposal was rejected.");
      const isHindi = activeConversation.language === "hi" || activeConversation.language === "hi-IN";
      const isHinglish = activeConversation.language === "hinglish";
      const rejectNoticeMsg: TimeAgentMessage = {
        id: `reject-${Date.now()}`,
        sender: "AGENT",
        content: isHindi
          ? "Update cancel कर दिया गया है। Authoritative schedule में कोई बदलाव नहीं किया गया।"
          : isHinglish
          ? "Update cancel kar diya gaya hai. Authoritative schedule mein koi changes apply nahi hue."
          : "Update was rejected. No changes were applied to the schedule.",
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, rejectNoticeMsg]);
      selectConversation(activeConversation.conversation_id);
    } catch (err: any) {
      setError(sanitizeErrorMessage(err));
      selectConversation(activeConversation.conversation_id);
    } finally {
      setActionLoading(null);
    }
  };

  // Handle File Upload Attachment
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !activeConversation) return;

    try {
      setUploading(true);
      setError(null);
      setSuccessBanner(null);

      const userNoticeMsg: TimeAgentMessage = {
        id: `upload-${Date.now()}`,
        sender: "USER",
        content: `Uploaded shift report document: ${file.name}`,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userNoticeMsg]);

      const resp = await uploadAgentAttachment(
        projectId,
        activeConversation.conversation_id,
        file
      );

      const agentMsg: TimeAgentMessage = {
        id: `agent-attach-${Date.now()}`,
        sender: "AGENT",
        content: resp.agent_message,
        message_metadata: resp.action_card || null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, agentMsg]);

      setSuccessBanner(
        `File processed: extracted ${resp.extracted_events_count} site execution events.`
      );

      loadConversationList();
    } catch (err: any) {
      setError(sanitizeErrorMessage(err));
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const suggestionChips = [
    {
      label: "Check project progress",
      prompt: "What is the overall progress of this project?",
    },
    {
      label: "Report today's concrete pour",
      prompt: "We poured 35 m3 concrete today for F-204",
    },
    {
      label: "Report electrical progress",
      prompt: "Installed 18 meters of cable tray today",
    },
    {
      label: "Find delayed activities",
      prompt: "Which activities in this schedule are currently delayed?",
    },
    {
      label: "Check past productivity",
      prompt: "What was our concrete pouring productivity rate in previous projects?",
    },
  ];

  const multilingualExamples = [
    {
      lang: "English",
      flag: "🇬🇧",
      text: "We poured 35 m3 concrete today for F-204",
    },
    {
      lang: "Hindi (हिन्दी)",
      flag: "🇮🇳",
      text: "आज F-204 में 35 क्यूबिक मीटर कंक्रीट डाला है",
    },
    {
      lang: "Hinglish",
      flag: "🇮🇳",
      text: "Aaj F-204 mein 35 cubic meter concrete dala hai",
    },
    {
      lang: "Status Query",
      flag: "🔍",
      text: "F-204 ka current progress kya hai?",
    },
    {
      lang: "Historical Query",
      flag: "🏛️",
      text: "Pichle project mein concrete ki average productivity kya thi?",
    },
  ];

  return (
    <div className="flex h-[780px] bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm text-slate-900">
      {/* 1. ScheduleManager Native Conversation Sidebar */}
      <ConversationSidebar
        conversations={conversations}
        activeConversationId={activeConversation?.conversation_id || null}
        onSelectConversation={selectConversation}
        onNewChat={handleNewChat}
        onPinConversation={handlePinConversation}
        onDeleteConversation={handleRequestDelete}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        loading={loadingList}
        projectName={projectName}
        isMobileDrawerOpen={isMobileDrawerOpen}
        onCloseMobileDrawer={() => setIsMobileDrawerOpen(false)}
      />

      {/* 2. Main Chat Panel */}
      <div className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Project Context Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-slate-200 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            {/* Mobile Hamburger Toggle */}
            <button
              onClick={() => setIsMobileDrawerOpen(true)}
              className="md:hidden p-1.5 rounded-lg border border-slate-200 text-slate-600 hover:text-slate-900 hover:bg-slate-50"
              aria-label="Open conversation history"
            >
              <Menu className="h-4 w-4" />
            </button>

            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-slate-900 tracking-tight">
                  Time Agent
                </h2>
                <span className="hidden sm:inline-block px-2 py-0.5 text-[10px] font-bold rounded-full bg-blue-50 text-blue-700 border border-blue-100">
                  PostgreSQL Governed
                </span>
                {isBulkConfirmationPending && (
                  <span
                    className="inline-flex items-center gap-1 px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-900 border border-amber-300 animate-pulse shadow-2xs"
                    title="Bulk update requires your confirmation before chatting"
                  >
                    <AlertCircle className="h-3 w-3 text-amber-700" />
                    <span>Waiting for confirmation</span>
                  </span>
                )}
                {activeConversation?.language && (
                  <span
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium rounded-full bg-slate-100 text-slate-700 border border-slate-200"
                    title={`Locked conversation language: ${activeConversation.language}`}
                  >
                    <Globe className="h-3 w-3 text-slate-500" />
                    <span>
                      {getLanguageDisplayName(activeConversation.language, activeConversation.conversation_style)}
                    </span>
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                Construction scheduling assistant
              </p>
              <div className="flex items-center gap-1.5 text-xs text-slate-600 mt-0.5">
                <span className="font-semibold text-slate-700">Project:</span>
                <span className="font-mono text-blue-700 font-semibold bg-blue-50 px-1.5 py-0.2 rounded border border-blue-100 text-[11px]">
                  {projectName || projectId}
                </span>
                {activeConversation?.active_activity && (
                  <>
                    <span className="text-slate-400">•</span>
                    <span className="text-slate-500">Anchor:</span>
                    <span className="font-mono text-slate-800 font-medium">
                      {activeConversation.active_activity.activity_code}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleNewChat}
              className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-xs font-semibold text-white shadow-xs transition-colors"
              title="Start a new chat in this schedule"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>New Chat</span>
            </button>
          </div>
        </div>

        {/* Success Notification Banner */}
        {successBanner && (
          <div className="flex items-center justify-between px-5 py-2.5 bg-emerald-50 border-b border-emerald-200 text-emerald-800 text-xs animate-in fade-in slide-in-from-top-2 shrink-0">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
              <span>{successBanner}</span>
            </div>
            <button
              onClick={() => setSuccessBanner(null)}
              className="text-emerald-600 hover:text-emerald-800 font-bold"
            >
              ×
            </button>
          </div>
        )}

        {/* Error Notification Banner */}
        {error && (
          <div className="flex items-center justify-between px-5 py-2.5 bg-rose-50 border-b border-rose-200 text-rose-800 text-xs animate-in fade-in slide-in-from-top-2 shrink-0">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-rose-600 shrink-0" />
              <span>{error}</span>
            </div>
            <button
              onClick={() => setError(null)}
              className="text-rose-600 hover:text-rose-800 font-bold"
            >
              ×
            </button>
          </div>
        )}

        {/* Chat Messages Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4 bg-slate-50/50">
          {loadingMessages ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-3">
              <RefreshCw className="h-6 w-6 text-blue-600 animate-spin" />
              <p className="text-xs text-slate-500 font-medium">Loading conversation history...</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center min-h-[500px] text-center max-w-xl mx-auto py-6 px-4 space-y-5 bg-white border border-slate-200 rounded-xl shadow-xs">
              <div className="h-12 w-12 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center text-blue-600 shadow-2xs">
                <Sparkles className="h-6 w-6" />
              </div>
              <div className="space-y-1.5">
                <h3 className="font-bold text-base text-slate-900 tracking-tight">
                  Time Agent — Your Construction Scheduling Assistant
                </h3>
                <p className="text-xs text-slate-500 leading-relaxed max-w-md mx-auto">
                  Report site execution directly in <strong className="text-slate-800">English</strong>,{" "}
                  <strong className="text-slate-800">Hindi (हिंदी)</strong>, or{" "}
                  <strong className="text-slate-800">Hinglish</strong> using text or speech.
                  Time Agent deterministically matches activities, verifies quantities, and stages updates for supervisor confirmation.
                </p>
              </div>

              {/* 5 Primary Suggestion Chips */}
              <div className="w-full space-y-2">
                <div className="text-[10px] uppercase font-bold text-slate-400 tracking-wider text-left">
                  Quick Actions
                </div>
                <div className="flex flex-wrap gap-2">
                  {suggestionChips.map((chip, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSendMessage(chip.prompt)}
                      className="px-3 py-1.5 rounded-lg bg-slate-50 hover:bg-blue-50 text-slate-700 hover:text-blue-700 border border-slate-200 hover:border-blue-200 text-xs font-medium transition-all flex items-center gap-1.5 group shadow-2xs"
                    >
                      <span>{chip.label}</span>
                      <ArrowRight className="h-3 w-3 text-slate-400 group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all" />
                    </button>
                  ))}
                </div>
              </div>

              {/* Multilingual Voice & Text Examples */}
              <div className="w-full space-y-2 pt-2 border-t border-slate-100">
                <div className="text-[10px] uppercase font-bold text-slate-400 tracking-wider text-left flex items-center justify-between">
                  <span>Multilingual Prompts (Text or Voice)</span>
                  <span className="text-slate-400 font-normal normal-case">Click to try</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-left">
                  {multilingualExamples.map((ex, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSendMessage(ex.text)}
                      className="p-2.5 rounded-lg bg-slate-50 hover:bg-white text-left border border-slate-200 hover:border-slate-300 transition-all hover:shadow-2xs group flex flex-col justify-between"
                    >
                      <div className="flex items-center justify-between w-full mb-1">
                        <span className="text-[10px] font-semibold text-blue-700 flex items-center gap-1">
                          <span>{ex.flag}</span>
                          <span>{ex.lang}</span>
                        </span>
                        <ArrowRight className="h-3 w-3 text-slate-400 group-hover:text-blue-600 transition-colors" />
                      </div>
                      <span className="text-xs text-slate-600 group-hover:text-slate-900 transition-colors line-clamp-2">
                        "{ex.text}"
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex gap-3 ${
                  msg.sender === "USER" ? "justify-end" : "justify-start"
                }`}
              >
                {msg.sender !== "USER" && (
                  <div className="h-7 w-7 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-700 shrink-0 text-xs font-bold mt-4.5">
                    {msg.sender === "SYSTEM" ? "SYS" : <Bot className="h-4 w-4" />}
                  </div>
                )}

                <div className="max-w-[85%] space-y-1">
                  <div className={`flex items-center gap-1.5 text-[11px] font-semibold ${msg.sender === "USER" ? "justify-end text-slate-500" : "text-slate-700"}`}>
                    {msg.sender === "USER" ? (
                      <>
                        <span>You</span>
                        {msg.created_at && <span className="font-mono text-slate-400 font-normal">• {formatMessageTime(msg.created_at)}</span>}
                      </>
                    ) : (
                      <>
                        <span className="text-blue-700 font-bold">Time Agent</span>
                        {msg.created_at && <span className="font-mono text-slate-400 font-normal">• {formatMessageTime(msg.created_at)}</span>}
                      </>
                    )}
                  </div>

                  <div
                    className={`p-3.5 rounded-lg text-xs leading-relaxed ${
                      msg.sender === "USER"
                        ? "bg-blue-600 text-white shadow-2xs"
                        : msg.sender === "SYSTEM"
                        ? "bg-slate-100 text-slate-700 border border-slate-200 font-mono text-[11px]"
                        : "bg-white text-slate-800 border border-slate-200/90 shadow-2xs"
                    }`}
                  >
                    {msg.sender === "AGENT" && (
                      <div className="mb-2 flex items-center justify-start">
                        <MessageAudioPlayer
                          messageId={msg.id}
                          text={msg.content}
                          messageLanguage={msg.message_metadata?.language || activeConversation?.language}
                          conversationLanguage={activeConversation?.language}
                          activePlayingId={activeAudioMessageId}
                          onAudioStarted={(id, pauseCb) => {
                            if (stopActiveAudioRef.current && activeAudioMessageId !== id) {
                              stopActiveAudioRef.current();
                            }
                            setActiveAudioMessageId(id);
                            stopActiveAudioRef.current = pauseCb;
                          }}
                          onAudioEnded={(id) => {
                            if (activeAudioMessageId === id) {
                              setActiveAudioMessageId(null);
                              stopActiveAudioRef.current = null;
                            }
                          }}
                        />
                      </div>
                    )}
                    <FormattedMessage content={msg.content} sender={msg.sender} />
                  </div>

                  {/* Proposal Confirmation Card */}
                  {msg.message_metadata &&
                    msg.message_metadata.type === "PROPOSAL_CONFIRMATION" && (
                      <ProposalCard
                        card={msg.message_metadata}
                        onConfirm={handleConfirmProposal}
                        onReject={handleRejectProposal}
                        actionLoading={actionLoading}
                        conversationLanguage={activeConversation?.language}
                      />
                    )}

                  {/* Clarification Choice Card & Bulk Scope Proposal Card */}
                  {msg.message_metadata &&
                    (msg.message_metadata.type === "CLARIFICATION_CHOICE" ||
                      msg.message_metadata.type === "BULK_SCOPE_PROPOSAL") && (
                      <ClarificationCard
                        card={msg.message_metadata}
                        onSelectOption={handleSelectClarificationOption}
                        onConfirmBulk={handleConfirmBulkProposal}
                        onCancelBulk={handleCancelBulkProposal}
                        disabled={sendingMessage || !!actionLoading}
                        actionLoading={actionLoading}
                      />
                    )}
                </div>
              </div>
            ))
          )}

          {/* Sending / Processing Indicator */}
          {sendingMessage && (
            <div className="flex gap-3 justify-start items-center">
              <div className="h-7 w-7 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-700 shrink-0">
                <Bot className="h-4 w-4" />
              </div>
              <div className="px-3.5 py-2.5 rounded-lg bg-white border border-slate-200 flex items-center gap-2 text-xs text-slate-600 shadow-2xs">
                <div className="h-1.5 w-1.5 rounded-full bg-blue-600 animate-pulse" />
                <div className="h-1.5 w-1.5 rounded-full bg-blue-600 animate-pulse delay-150" />
                <div className="h-1.5 w-1.5 rounded-full bg-blue-600 animate-pulse delay-300" />
                <span className="ml-1 text-slate-700 font-medium">
                  Evaluating schedule, matching activities & checking governance rules...
                </span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Message Composer Footer */}
        <div className="p-4 bg-white border-t border-slate-200 shrink-0">
          {/* Prompt Section 29: Standout Pending Decision State */}
          {isBulkConfirmationPending ? (
            <div className="mb-3.5 p-4 rounded-xl bg-amber-50/95 border border-amber-300 shadow-xs animate-in fade-in duration-200">
              <div className="flex items-center gap-2 text-xs font-black uppercase tracking-wider text-amber-900 mb-1.5">
                <span>⏳</span>
                <span>WAITING FOR YOUR DECISION</span>
              </div>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="text-xs text-amber-950 font-medium">
                  Bulk update: <strong>{pendingBulkCount} activities</strong> to <strong>{pendingBulkTargetPct}%</strong>
                </div>
                <div className="flex items-center gap-2 self-end sm:self-auto shrink-0">
                  <button
                    type="button"
                    onClick={() => handleConfirmBulkProposal()}
                    disabled={Boolean(actionLoading) || sendingMessage}
                    className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs transition-colors flex items-center gap-1.5 shadow-2xs cursor-pointer disabled:opacity-50"
                  >
                    {actionLoading === "bulk" ? (
                      <>
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        <span>Applying...</span>
                      </>
                    ) : (
                      <>
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        <span>Confirm Update</span>
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={handleCancelBulkProposal}
                    disabled={Boolean(actionLoading) || sendingMessage}
                    className="px-3.5 py-1.5 rounded-lg bg-white hover:bg-amber-100 border border-amber-300 text-amber-900 font-semibold text-xs transition-colors flex items-center gap-1.5 shadow-2xs cursor-pointer disabled:opacity-50"
                  >
                    {actionLoading === "bulk-cancel" ? (
                      <>
                        <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-700" />
                        <span>Cancelling...</span>
                      </>
                    ) : (
                      <>
                        <X className="h-3.5 w-3.5 text-amber-700" />
                        <span>Cancel</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          ) : hasPendingActionCard && (
            <div className="mb-3.5 p-4 rounded-xl bg-amber-50/95 border border-amber-300 shadow-xs animate-in fade-in duration-200">
              <div className="flex items-center gap-2 text-xs font-black uppercase tracking-wider text-amber-900 mb-1.5">
                <span>⏳</span>
                <span>WAITING FOR YOUR DECISION</span>
              </div>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="text-xs text-amber-950 font-medium">
                  {pendingActionCard?.card.activity_code ? (
                    <span>
                      Proposed update for <strong>{pendingActionCard.card.activity_code}</strong>:{" "}
                      {pendingActionCard.card.proposed_percent !== undefined
                        ? `${pendingActionCard.card.proposed_percent}%`
                        : "schedule adjustment"}
                    </span>
                  ) : (
                    <span>Schedule action requires your review and approval</span>
                  )}
                </div>
                <div className="flex items-center gap-2 self-end sm:self-auto shrink-0">
                  {pendingActionCard?.card.proposal_id && (
                    <button
                      type="button"
                      onClick={() => handleConfirmProposal(pendingActionCard.card.proposal_id!)}
                      disabled={actionLoading === pendingActionCard.card.proposal_id || sendingMessage}
                      className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs transition-colors flex items-center gap-1.5 shadow-2xs cursor-pointer disabled:opacity-50"
                    >
                      {actionLoading === pendingActionCard.card.proposal_id ? (
                        <>
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          <span>Applying...</span>
                        </>
                      ) : (
                        <>
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          <span>Confirm Update</span>
                        </>
                      )}
                    </button>
                  )}
                  {pendingActionCard?.card.proposal_id ? (
                    <button
                      type="button"
                      onClick={() => handleRejectProposal(pendingActionCard.card.proposal_id!)}
                      disabled={actionLoading === pendingActionCard.card.proposal_id || sendingMessage}
                      className="px-3.5 py-1.5 rounded-lg bg-white hover:bg-amber-100 border border-amber-300 text-amber-900 font-semibold text-xs transition-colors flex items-center gap-1 shadow-2xs cursor-pointer disabled:opacity-50"
                    >
                      <X className="h-3.5 w-3.5 text-amber-700" />
                      <span>Cancel</span>
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="flex items-center gap-2"
          >
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              className="hidden"
              accept=".pdf,.xlsx,.csv,image/*,audio/*"
            />

            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isChatLocked || uploading || sendingMessage}
              className="p-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 hover:text-slate-900 border border-slate-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title={isChatLocked ? "Decide on pending bulk update first" : "Attach shift report (PDF, Excel, Audio)"}
            >
              {uploading ? (
                <RefreshCw className="h-4 w-4 animate-spin text-blue-600" />
              ) : (
                <Paperclip className="h-4 w-4" />
              )}
            </button>

            {/* Voice Input Button (Sarvam STT) */}
            <VoiceInputButton
              onAudioRecorded={handleVoiceAudioRecorded}
              disabled={isChatLocked || sendingMessage || loadingMessages}
            />

            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={
                isBulkConfirmationPending
                  ? "Bulk update is waiting for your confirmation."
                  : hasPendingActionCard
                  ? (activeConversation?.language === "hi"
                      ? "कृपया पहले ऊपर दिए गए प्रस्ताव पर निर्णय लें..."
                      : activeConversation?.language === "hinglish"
                      ? "Kripya pehle upar diye gaye proposal par decision lein..."
                      : "Please make a decision on the pending proposal above...")
                  : "Report progress (any language) or use voice...."
              }
              disabled={isChatLocked || sendingMessage || loadingMessages}
              className="flex-1 px-4 py-2.5 rounded-lg bg-white border border-slate-300 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all disabled:opacity-50 disabled:bg-slate-50 disabled:cursor-not-allowed"
            />

            <button
              type="submit"
              disabled={isChatLocked || !inputText.trim() || sendingMessage || loadingMessages}
              className="p-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white shadow-xs transition-all disabled:opacity-40 disabled:hover:bg-blue-600 disabled:cursor-not-allowed"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>

          <div className="flex items-center justify-between text-[11px] text-slate-500 mt-2 px-1">
            <span>
              Any language • Browser Voice STT • Proposals require human confirmation
            </span>
            <span className="hidden sm:inline text-slate-500">
              5-minute proposal TTL · PostgreSQL ACID audit ledger
            </span>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal Dialog */}
      {deletingConversation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-4 animate-in fade-in duration-150">
          <div className="relative w-full max-w-sm rounded-xl bg-white shadow-2xl border border-slate-200 p-6 space-y-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">
                Delete conversation?
              </h3>
              <p className="text-xs text-slate-500 mt-1">
                This will permanently delete:
              </p>
              <div className="mt-2 p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-xs font-semibold text-slate-800 truncate">
                "{deletingConversation.title || "New Chat"}"
              </div>
              <p className="text-xs text-red-600 mt-2 font-medium">
                This action cannot be undone.
              </p>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100">
              <button
                type="button"
                onClick={() => setDeletingConversation(null)}
                disabled={isDeleting}
                className="px-4 py-2 rounded-lg border border-slate-300 hover:bg-slate-50 text-xs font-semibold text-slate-700 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                disabled={isDeleting}
                className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-xs font-semibold text-white transition-colors disabled:opacity-50 flex items-center gap-1.5 shadow-2xs"
              >
                {isDeleting ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    <span>Deleting...</span>
                  </>
                ) : (
                  <span>Delete</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
