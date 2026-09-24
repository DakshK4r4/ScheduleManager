"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Mic, MicOff, AlertCircle, RefreshCw, Square, X } from "lucide-react";

export type RecordingState = "IDLE" | "RECORDING" | "STOPPING" | "TRANSCRIBING" | "ERROR";

interface VoiceInputButtonProps {
  onAudioRecorded?: (audioBlob: Blob, signal?: AbortSignal) => Promise<void>;
  onTranscript?: (text: string) => void;
  onRecordingStateChange?: (state: RecordingState) => void;
  disabled?: boolean;
}

const MAX_RECORDING_SECONDS = 30;

export default function VoiceInputButton({
  onAudioRecorded,
  onTranscript,
  onRecordingStateChange,
  disabled = false,
}: VoiceInputButtonProps) {
  const [state, setState] = useState<RecordingState>("IDLE");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const [isSupported, setIsSupported] = useState<boolean>(true);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isCancelledRef = useRef<boolean>(false);

  // Notify parent of state changes
  const updateState = useCallback(
    (newState: RecordingState) => {
      setState(newState);
      onRecordingStateChange?.(newState);
    },
    [onRecordingStateChange]
  );

  useEffect(() => {
    if (typeof window !== "undefined") {
      const hasMedia =
        typeof navigator !== "undefined" &&
        Boolean(navigator.mediaDevices) &&
        typeof window.MediaRecorder !== "undefined";
      setIsSupported(hasMedia);
    }
  }, []);

  const cleanupHardware = useCallback(() => {
    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    mediaRecorderRef.current = null;
    setElapsedSeconds(0);
  }, []);

  // Complete cancellation: discard audio, release mic, abort pending network request, reset to IDLE
  const cancelRecording = useCallback(() => {
    isCancelledRef.current = true;

    // 1. Abort any in-flight transcription request
    if (abortControllerRef.current) {
      try {
        abortControllerRef.current.abort();
      } catch (err) {
        console.warn("Failed to abort transcription request:", err);
      }
      abortControllerRef.current = null;
    }

    // 2. Discard all recorded audio chunks
    audioChunksRef.current = [];

    // 3. Stop MediaRecorder without triggering onstop processing
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try {
        mediaRecorderRef.current.stop();
      } catch (err) {
        console.warn("Error stopping MediaRecorder on cancel:", err);
      }
    }

    // 4. Release microphone tracks & clear timers
    cleanupHardware();

    // 5. Return cleanly to IDLE
    setErrorMessage(null);
    updateState("IDLE");
  }, [cleanupHardware, updateState]);

  // Stop recording normally and proceed to transcription
  const stopRecording = useCallback(() => {
    if (state !== "RECORDING") return;

    updateState("STOPPING");

    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current);
      timerIntervalRef.current = null;
    }

    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      try {
        mediaRecorderRef.current.stop();
      } catch (err) {
        console.warn("Error stopping MediaRecorder:", err);
        cleanupHardware();
        updateState("IDLE");
      }
    } else {
      cleanupHardware();
      updateState("IDLE");
    }
  }, [state, cleanupHardware, updateState]);

  // Start recording
  const startRecording = useCallback(async () => {
    if (disabled || state !== "IDLE") return;

    setErrorMessage(null);
    audioChunksRef.current = [];
    setElapsedSeconds(0);
    isCancelledRef.current = false;
    abortControllerRef.current = null;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      // Select supported audio MIME type
      let mimeType = "audio/webm";
      if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
        mimeType = "audio/webm;codecs=opus";
      } else if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) {
        mimeType = "audio/ogg;codecs=opus";
      } else if (MediaRecorder.isTypeSupported("audio/mp4")) {
        mimeType = "audio/mp4";
      } else if (MediaRecorder.isTypeSupported("audio/wav")) {
        mimeType = "audio/wav";
      }

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (!isCancelledRef.current && event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        // If user cancelled, completely ignore and do not transcribe or send
        if (isCancelledRef.current) {
          audioChunksRef.current = [];
          cleanupHardware();
          return;
        }

        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
        cleanupHardware();

        if (audioBlob.size === 0) {
          updateState("IDLE");
          return;
        }

        updateState("TRANSCRIBING");

        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        try {
          if (onAudioRecorded) {
            await onAudioRecorded(audioBlob, abortController.signal);
          }
          if (!isCancelledRef.current) {
            updateState("IDLE");
          }
        } catch (err: any) {
          if (isCancelledRef.current || err?.name === "AbortError" || abortController.signal.aborted) {
            // User cancelled during transcription: silent return to IDLE
            updateState("IDLE");
            return;
          }
          console.error("Audio transmission failed:", err);
          updateState("ERROR");
          setErrorMessage("Couldn't transcribe the audio. Please try again.");
          setTimeout(() => {
            updateState("IDLE");
          }, 3500);
        } finally {
          abortControllerRef.current = null;
        }
      };

      recorder.onerror = (event: any) => {
        console.error("MediaRecorder error:", event.error);
        cleanupHardware();
        if (!isCancelledRef.current) {
          updateState("ERROR");
          setErrorMessage("Couldn't transcribe the audio. Please try again.");
        }
      };

      recorder.start(250); // Slice in 250ms chunks
      updateState("RECORDING");

      // Set up 1-second interval up to MAX_RECORDING_SECONDS (30s)
      timerIntervalRef.current = setInterval(() => {
        setElapsedSeconds((prev) => {
          if (prev >= MAX_RECORDING_SECONDS - 1) {
            stopRecording();
            return MAX_RECORDING_SECONDS;
          }
          return prev + 1;
        });
      }, 1000);
    } catch (err: any) {
      console.error("Microphone access denied or error:", err);
      cleanupHardware();
      updateState("ERROR");
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setErrorMessage("Microphone permission blocked. Please allow mic access.");
      } else {
        setErrorMessage("Couldn't start voice recording. Please check microphone.");
      }
    }
  }, [disabled, state, onAudioRecorded, cleanupHardware, stopRecording, updateState]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      isCancelledRef.current = true;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      cleanupHardware();
    };
  }, [cleanupHardware]);

  if (!isSupported) {
    return (
      <button
        type="button"
        disabled
        className="p-2.5 rounded-lg bg-slate-100 border border-slate-200 text-slate-400 cursor-not-allowed transition-colors"
        title="Voice recording is not supported in this browser"
        aria-label="Voice input unavailable"
      >
        <MicOff className="h-4 w-4" />
      </button>
    );
  }

  const formatTimer = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins < 10 ? "0" : ""}${mins}:${secs < 10 ? "0" : ""}${secs}`;
  };

  return (
    <div className="relative flex items-center">
      {/* Floating Status Pill for Error */}
      {errorMessage && (
        <div className="absolute bottom-full mb-2 left-0 bg-white border border-rose-200 px-3 py-1.5 rounded-lg shadow-lg flex items-center gap-2 text-xs text-rose-800 z-30 whitespace-nowrap animate-in fade-in slide-in-from-bottom-1">
          <AlertCircle className="h-3.5 w-3.5 text-rose-600 shrink-0" />
          <span>{errorMessage}</span>
          <button
            onClick={() => setErrorMessage(null)}
            className="ml-1 text-slate-400 hover:text-slate-700 font-bold"
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}

      {/* State-specific UI controls */}
      {state === "RECORDING" ? (
        <div className="flex items-center gap-1.5 rounded-lg bg-rose-50 border border-rose-300 p-1 shadow-xs animate-in fade-in duration-200">
          <div className="flex items-center gap-2 px-2 py-0.5 text-rose-700">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-rose-600" />
            </span>
            <span className="text-xs font-bold font-mono tracking-tight">
              Recording {formatTimer(elapsedSeconds)}
            </span>
            <span className="text-[10px] text-rose-500 font-mono hidden sm:inline">
              / 00:30
            </span>
          </div>

          <button
            type="button"
            onClick={cancelRecording}
            className="px-2.5 py-1 rounded-md text-xs font-semibold text-rose-800 hover:text-rose-950 hover:bg-rose-100 transition-colors flex items-center gap-1 cursor-pointer"
            title="Cancel and discard recording"
            aria-label="Cancel recording"
          >
            <X className="h-3.5 w-3.5" />
            <span>Cancel</span>
          </button>

          <button
            type="button"
            onClick={stopRecording}
            className="px-3 py-1 rounded-md text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white shadow-xs transition-colors flex items-center gap-1 cursor-pointer"
            title="Stop recording and send"
            aria-label="Stop recording and send"
          >
            <Square className="h-3 w-3 fill-current" />
            <span>Stop</span>
          </button>
        </div>
      ) : state === "TRANSCRIBING" ? (
        <div className="flex items-center gap-2 rounded-lg bg-blue-50 border border-blue-200 p-1 shadow-xs animate-in fade-in duration-200">
          <div className="flex items-center gap-1.5 px-2 py-0.5 text-blue-700">
            <RefreshCw className="h-3.5 w-3.5 animate-spin text-blue-600 shrink-0" />
            <span className="text-xs font-semibold">Transcribing...</span>
          </div>
          <button
            type="button"
            onClick={cancelRecording}
            className="px-2 py-1 rounded-md text-xs font-medium text-slate-600 hover:text-rose-700 hover:bg-rose-50 transition-colors flex items-center gap-1 cursor-pointer"
            title="Cancel transcription"
            aria-label="Cancel transcription"
          >
            <X className="h-3.5 w-3.5" />
            <span>Cancel</span>
          </button>
        </div>
      ) : state === "STOPPING" ? (
        <div className="flex items-center gap-1.5 rounded-lg bg-slate-100 border border-slate-200 px-2.5 py-1.5 text-slate-600 text-xs font-medium">
          <RefreshCw className="h-3 w-3 animate-spin text-slate-500" />
          <span>Stopping...</span>
        </div>
      ) : (
        /* IDLE or ERROR: Normal mic button */
        <button
          type="button"
          onClick={startRecording}
          disabled={disabled}
          className="p-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 hover:text-slate-900 border border-slate-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          title="Voice Input: Speak in Hindi, English, Hinglish, etc."
          aria-label="Record voice input"
        >
          <Mic className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
