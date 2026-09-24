"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Volume2, Pause, Play, Loader2, AlertCircle } from "lucide-react";
import { generateTTS } from "@/lib/api";

export type AudioPlaybackState = "IDLE" | "GENERATING" | "PLAYING" | "PAUSED" | "ERROR";

interface MessageAudioPlayerProps {
  messageId: string;
  text: string;
  messageLanguage?: string | null;
  conversationLanguage?: string | null;
  activePlayingId: string | null;
  onAudioStarted: (id: string, pauseCallback: () => void) => void;
  onAudioEnded: (id: string) => void;
}

/**
 * Resolves the appropriate language code for text-to-speech.
 * Respects message language, conversation language, and performs script detection fallback.
 */
export function resolveSpeechLanguage(
  text: string,
  messageLanguage?: string | null,
  conversationLanguage?: string | null
): string {
  if (messageLanguage && messageLanguage !== "auto" && messageLanguage !== "unknown") {
    return normalizeLang(messageLanguage);
  }
  if (conversationLanguage && conversationLanguage !== "auto" && conversationLanguage !== "unknown") {
    return normalizeLang(conversationLanguage);
  }

  // Fallback to script detection for multilingual text
  if (/[\u0900-\u097F]/.test(text)) return "hi-IN";
  if (/[\u0B80-\u0BFF]/.test(text)) return "ta-IN";
  if (/[\u0C00-\u0C7F]/.test(text)) return "te-IN";
  if (/[\u0980-\u09FF]/.test(text)) return "bn-IN";
  if (/[\u0C80-\u0CFF]/.test(text)) return "kn-IN";
  if (/[\u0D00-\u0D7F]/.test(text)) return "ml-IN";
  if (/[\u0A80-\u0AFF]/.test(text)) return "gu-IN";
  if (/[\u0A00-\u0A7F]/.test(text)) return "pa-IN";
  if (/[\u0B00-\u0B7F]/.test(text)) return "od-IN";

  return "en-IN";
}

function normalizeLang(lang: string): string {
  const low = lang.toLowerCase().trim();
  if (low === "hi" || low === "hi-in" || low === "hinglish") return "hi-IN";
  if (low === "ta" || low === "ta-in") return "ta-IN";
  if (low === "te" || low === "te-in") return "te-IN";
  if (low === "bn" || low === "bn-in") return "bn-IN";
  if (low === "mr" || low === "mr-in") return "mr-IN";
  if (low === "gu" || low === "gu-in") return "gu-IN";
  if (low === "kn" || low === "kn-in") return "kn-IN";
  if (low === "ml" || low === "ml-in") return "ml-IN";
  if (low === "pa" || low === "pa-in") return "pa-IN";
  if (low === "od" || low === "od-in") return "od-IN";
  if (low === "en" || low === "en-in" || low === "en-us") return "en-IN";
  return lang.includes("-") ? lang : `${lang}-IN`;
}

export default function MessageAudioPlayer({
  messageId,
  text,
  messageLanguage,
  conversationLanguage,
  activePlayingId,
  onAudioStarted,
  onAudioEnded,
}: MessageAudioPlayerProps) {
  const [playbackState, setPlaybackState] = useState<AudioPlaybackState>("IDLE");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // In-memory cache for audio object during this session
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  // If another message started playing, reset this player to IDLE if we were playing
  useEffect(() => {
    if (activePlayingId !== messageId && (playbackState === "PLAYING" || playbackState === "PAUSED")) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
      }
      setPlaybackState("IDLE");
    }
  }, [activePlayingId, messageId, playbackState]);

  // Clean up audio on unmount
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
        audioUrlRef.current = null;
      }
    };
  }, []);

  const handlePause = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      setPlaybackState("PAUSED");
    }
  }, []);

  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation();

    // 1. If currently playing, clicking pauses the audio
    if (playbackState === "PLAYING") {
      handlePause();
      return;
    }

    // 2. If paused, clicking resumes playback
    if (playbackState === "PAUSED" && audioRef.current) {
      try {
        onAudioStarted(messageId, handlePause);
        await audioRef.current.play();
        setPlaybackState("PLAYING");
        return;
      } catch (err) {
        console.error("Audio resume error:", err);
      }
    }

    // 3. If audio is already cached in memory, replay it from beginning
    if (audioRef.current) {
      try {
        audioRef.current.currentTime = 0;
        onAudioStarted(messageId, handlePause);
        await audioRef.current.play();
        setPlaybackState("PLAYING");
        return;
      } catch (err) {
        console.error("Audio replay error:", err);
      }
    }

    // 4. Otherwise, generate TTS on-demand from Sarvam backend
    setPlaybackState("GENERATING");
    setErrorMessage(null);

    const resolvedLanguage = resolveSpeechLanguage(text, messageLanguage, conversationLanguage);

    try {
      const resp = await generateTTS({
        text,
        language: resolvedLanguage,
        model: "bulbul:v3",
      });

      if (!resp || !resp.audio_base64) {
        throw new Error("No audio returned from server");
      }

      // Decode base64 to binary blob
      const binaryString = atob(resp.audio_base64);
      const len = binaryString.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }

      const blob = new Blob([bytes], { type: resp.content_type || "audio/wav" });
      const blobUrl = URL.createObjectURL(blob);
      audioUrlRef.current = blobUrl;

      const audio = new Audio(blobUrl);
      audioRef.current = audio;

      audio.onended = () => {
        setPlaybackState("IDLE");
        onAudioEnded(messageId);
      };

      audio.onerror = (e) => {
        console.error("HTMLAudioElement playback error:", e);
        setPlaybackState("ERROR");
        setErrorMessage("Playback failed. Please try again.");
        onAudioEnded(messageId);
      };

      onAudioStarted(messageId, handlePause);
      await audio.play();
      setPlaybackState("PLAYING");
    } catch (err: any) {
      console.warn("TTS generation failed:", err?.message || err);
      setPlaybackState("ERROR");
      setErrorMessage("Unable to play audio. Please try again.");
      onAudioEnded(messageId);
    }
  };

  const getButtonTitle = () => {
    switch (playbackState) {
      case "GENERATING":
        return "Generating voice audio...";
      case "PLAYING":
        return "Pause audio";
      case "PAUSED":
        return "Resume playback";
      case "ERROR":
        return errorMessage || "Unable to play audio. Click to retry.";
      case "IDLE":
      default:
        return "Listen to response";
    }
  };

  return (
    <div className="inline-flex items-center gap-1.5 mb-1.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={playbackState === "GENERATING"}
        title={getButtonTitle()}
        aria-label={getButtonTitle()}
        className={`group inline-flex items-center justify-center h-6 w-6 rounded-md transition-all duration-150 focus:outline-none focus:ring-1.5 focus:ring-blue-500/50 ${
          playbackState === "PLAYING"
            ? "bg-blue-100 text-blue-700 hover:bg-blue-200 border border-blue-300"
            : playbackState === "PAUSED"
            ? "bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-200"
            : playbackState === "GENERATING"
            ? "bg-slate-100 text-blue-600 cursor-wait border border-slate-200"
            : playbackState === "ERROR"
            ? "bg-rose-50 text-rose-600 hover:bg-rose-100 border border-rose-200"
            : "bg-slate-100/90 text-slate-500 hover:text-blue-600 hover:bg-blue-50/80 border border-slate-200/80"
        }`}
      >
        {playbackState === "GENERATING" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : playbackState === "PLAYING" ? (
          <div className="flex items-center gap-0.5">
            <span className="h-2 w-0.5 bg-blue-600 rounded-full animate-pulse" />
            <span className="h-3 w-0.5 bg-blue-600 rounded-full animate-pulse delay-75" />
            <span className="h-2 w-0.5 bg-blue-600 rounded-full animate-pulse delay-150" />
          </div>
        ) : playbackState === "PAUSED" ? (
          <Play className="h-3 w-3 fill-current ml-0.5" />
        ) : playbackState === "ERROR" ? (
          <AlertCircle className="h-3.5 w-3.5 text-rose-500" />
        ) : (
          <Volume2 className="h-3.5 w-3.5 transition-transform group-hover:scale-110" />
        )}
      </button>

      {playbackState === "PLAYING" && (
        <span className="text-[10px] text-blue-600 font-medium animate-pulse select-none">
          Playing...
        </span>
      )}

      {playbackState === "PAUSED" && (
        <span className="text-[10px] text-slate-500 font-medium select-none">
          Paused
        </span>
      )}

      {playbackState === "GENERATING" && (
        <span className="text-[10px] text-slate-500 font-medium select-none">
          Generating voice...
        </span>
      )}

      {playbackState === "ERROR" && (
        <span className="text-[10px] text-rose-500 font-medium select-none">
          Voice unavailable
        </span>
      )}
    </div>
  );
}
