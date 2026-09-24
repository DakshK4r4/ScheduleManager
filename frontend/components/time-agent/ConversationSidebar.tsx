"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  Plus,
  Search,
  MessageSquare,
  Pin,
  PinOff,
  Trash2,
  MoreVertical,
  X,
  Sparkles,
  Bot,
  ShieldCheck,
} from "lucide-react";
import { TimeAgentConversationSummary } from "@/lib/types";
import { formatRelativeTime } from "@/lib/timeUtils";

interface ConversationSidebarProps {
  conversations: TimeAgentConversationSummary[];
  activeConversationId: string | null;
  onSelectConversation: (conversationId: string) => void;
  onNewChat: () => void;
  onPinConversation: (conversationId: string, isPinned: boolean) => Promise<void>;
  onDeleteConversation: (conversation: TimeAgentConversationSummary) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  loading: boolean;
  projectName?: string;
  isMobileDrawerOpen: boolean;
  onCloseMobileDrawer: () => void;
}

export default function ConversationSidebar({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onPinConversation,
  onDeleteConversation,
  searchQuery,
  onSearchChange,
  loading,
  projectName,
  isMobileDrawerOpen,
  onCloseMobileDrawer,
}: ConversationSidebarProps) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close three-dot menu on click outside or Escape
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpenMenuId(null);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  // Separate pinned and recent conversations
  const pinnedChats = conversations.filter((c) => Boolean(c.is_pinned));
  const recentChats = conversations.filter((c) => !c.is_pinned);

  const renderChatItem = (conv: TimeAgentConversationSummary) => {
    const isSelected = conv.id === activeConversationId;
    const isMenuOpen = openMenuId === conv.id;
    const isPinned = Boolean(conv.is_pinned);

    return (
      <div key={conv.id} className="relative group">
        <button
          onClick={() => {
            onSelectConversation(conv.id);
            onCloseMobileDrawer();
          }}
          className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all flex items-center justify-between border ${
            isSelected
              ? "bg-blue-50 text-blue-900 border-blue-200 font-semibold shadow-2xs"
              : "text-slate-700 hover:bg-slate-100 hover:text-slate-900 border-transparent"
          }`}
        >
          <div className="flex items-center gap-2 min-w-0 flex-1 mr-1.5">
            {isPinned ? (
              <Pin className="h-3.5 w-3.5 text-blue-600 fill-blue-600 shrink-0" />
            ) : (
              <MessageSquare
                className={`h-3.5 w-3.5 shrink-0 ${
                  isSelected ? "text-blue-600" : "text-slate-400 group-hover:text-slate-600"
                }`}
              />
            )}
            <div className="min-w-0 flex-1">
              <span className="truncate block leading-snug">
                {conv.title || "New Chat"}
              </span>
              <span className="text-[10px] text-slate-400 block truncate mt-0.5">
                {formatRelativeTime(conv.updated_at || conv.created_at)}
                {conv.message_count > 0 && ` · ${conv.message_count} msgs`}
                {conv.language && (
                  <span className="ml-1.5 inline-block font-mono text-[9px] font-semibold px-1 py-0.2 rounded bg-slate-100 text-slate-600 border border-slate-200">
                    {conv.language.toUpperCase()}
                  </span>
                )}
                {conv.status === "WAITING_FOR_BULK_UPDATE_CONFIRMATION" && (
                  <span className="ml-1.5 inline-block font-sans text-[9px] font-bold px-1.5 py-0.2 rounded bg-amber-100 text-amber-800 border border-amber-300">
                    Waiting
                  </span>
                )}
              </span>
            </div>
          </div>

          {/* Three-Dot Menu Trigger */}
          <div className="relative shrink-0 flex items-center">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpenMenuId(isMenuOpen ? null : conv.id);
              }}
              className={`p-1 rounded-md transition-colors ${
                isMenuOpen
                  ? "bg-slate-200 text-slate-900"
                  : "text-slate-400 hover:text-slate-700 hover:bg-slate-200/60 opacity-80 group-hover:opacity-100"
              }`}
              aria-label="Conversation options"
              aria-haspopup="true"
              aria-expanded={isMenuOpen}
            >
              <MoreVertical className="h-3.5 w-3.5" />
            </button>
          </div>
        </button>

        {/* Dropdown Popover Menu */}
        {isMenuOpen && (
          <div
            ref={menuRef}
            className="absolute right-2 top-full mt-1 w-36 bg-white border border-slate-200 rounded-lg shadow-lg py-1 z-40 text-xs animate-in fade-in zoom-in-95 duration-150"
            role="menu"
          >
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpenMenuId(null);
                onPinConversation(conv.id, !isPinned);
              }}
              className="w-full text-left px-3 py-1.5 flex items-center gap-2 text-slate-700 hover:bg-slate-50 transition-colors"
              role="menuitem"
              aria-label={isPinned ? "Unpin conversation" : "Pin conversation"}
            >
              {isPinned ? (
                <>
                  <PinOff className="h-3.5 w-3.5 text-slate-500" />
                  <span>Unpin chat</span>
                </>
              ) : (
                <>
                  <Pin className="h-3.5 w-3.5 text-slate-500" />
                  <span>Pin chat</span>
                </>
              )}
            </button>

            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpenMenuId(null);
                onDeleteConversation(conv);
              }}
              className="w-full text-left px-3 py-1.5 flex items-center gap-2 text-red-600 hover:bg-red-50 transition-colors"
              role="menuitem"
              aria-label="Delete conversation"
            >
              <Trash2 className="h-3.5 w-3.5 text-red-500" />
              <span>Delete chat</span>
            </button>
          </div>
        )}
      </div>
    );
  };

  const content = (
    <div className="flex flex-col h-full bg-slate-50 border-r border-slate-200 text-slate-800 w-72 shrink-0">
      {/* Sidebar Header */}
      <div className="p-4 border-b border-slate-200 bg-white space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-lg bg-blue-600 flex items-center justify-center text-white shadow-xs">
              <Bot className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <span className="font-bold text-xs text-slate-900 tracking-tight block">
                Time Agent
              </span>
              <span className="block text-[10px] text-slate-500 truncate max-w-[150px]">
                {projectName ? `Project: ${projectName}` : "Schedule Scope"}
              </span>
            </div>
          </div>

          <button
            onClick={onCloseMobileDrawer}
            className="md:hidden text-slate-400 hover:text-slate-700 p-1 rounded-md"
            aria-label="Close sidebar"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* + New Chat Button */}
        <button
          onClick={() => {
            onNewChat();
            onCloseMobileDrawer();
          }}
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold shadow-xs transition-colors group"
        >
          <Plus className="h-3.5 w-3.5 transition-transform group-hover:rotate-90 duration-200" />
          <span>New Chat</span>
        </button>

        {/* Search Input */}
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search conversations..."
            className="w-full pl-8 pr-7 py-1.5 rounded-lg bg-white border border-slate-200 text-xs text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors shadow-2xs"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange("")}
              className="absolute right-2 top-2 text-slate-400 hover:text-slate-600"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Pinned & Recent Chats List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {loading && conversations.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-400">
            Loading conversations...
          </div>
        ) : conversations.length === 0 ? (
          <div className="py-8 px-4 text-center space-y-2">
            <Sparkles className="h-5 w-5 text-slate-400 mx-auto" />
            <p className="text-xs text-slate-600 font-medium">
              {searchQuery ? "No matching chats found" : "No chats yet"}
            </p>
            <p className="text-[11px] text-slate-500">
              {searchQuery
                ? "Try a different search term"
                : "Click + New Chat to begin reporting execution progress."}
            </p>
          </div>
        ) : (
          <>
            {/* PINNED SECTION */}
            <div>
              <div className="px-2 py-1 flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1">
                <span className="flex items-center gap-1">
                  <Pin className="h-3 w-3 text-blue-600 fill-blue-600" />
                  PINNED
                </span>
                {pinnedChats.length > 0 && (
                  <span className="text-[10px] font-mono font-medium text-slate-400">
                    {pinnedChats.length}
                  </span>
                )}
              </div>
              <div className="space-y-0.5">
                {pinnedChats.length === 0 ? (
                  <div className="px-3 py-1.5 text-[11px] text-slate-400 italic">
                    No pinned chats
                  </div>
                ) : (
                  pinnedChats.map(renderChatItem)
                )}
              </div>
            </div>

            {/* RECENT SECTION */}
            <div>
              <div className="px-2 py-1 flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1">
                <span>RECENT</span>
                <span className="text-[10px] font-mono font-medium text-slate-400">
                  {recentChats.length}
                </span>
              </div>
              <div className="space-y-0.5">
                {recentChats.length === 0 ? (
                  <div className="px-3 py-1.5 text-[11px] text-slate-400 italic">
                    {pinnedChats.length > 0 ? "All chats are pinned" : "No recent chats"}
                  </div>
                ) : (
                  recentChats.map(renderChatItem)
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Sidebar Footer with Project Isolation Badge */}
      <div className="p-3 border-t border-slate-200 bg-white text-[10px] text-slate-500 flex items-center justify-between">
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
          <span>Project-Isolated</span>
        </span>
        <span className="font-mono text-slate-400">
          PostgreSQL Governed
        </span>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Persistent Sidebar */}
      <div className="hidden md:flex h-full shrink-0">{content}</div>

      {/* Mobile Drawer Overlay */}
      {isMobileDrawerOpen && (
        <div className="md:hidden fixed inset-0 z-50 flex">
          <div
            className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs transition-opacity"
            onClick={onCloseMobileDrawer}
          />
          <div className="relative z-10 flex h-full shadow-xl animate-in slide-in-from-left duration-200">
            {content}
          </div>
        </div>
      )}
    </>
  );
}
