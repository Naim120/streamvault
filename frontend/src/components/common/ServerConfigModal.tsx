import React, { useState, useEffect } from 'react';
import { Server, RefreshCw, CheckCircle2, AlertCircle, X, Globe, Wifi } from 'lucide-react';
import {
  getServerStatus,
  setCustomServerUrl,
  initServerDiscovery,
  pingServer,
  isNativeApp
} from '../../lib/serverDiscovery';

interface ServerConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ServerConfigModal: React.FC<ServerConfigModalProps> = ({ isOpen, onClose }) => {
  const [customInput, setCustomInput] = useState('');
  const [status, setStatus] = useState(getServerStatus());
  const [isTesting, setIsTesting] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);

  const refreshState = () => {
    setStatus(getServerStatus());
    const stored = localStorage.getItem('sv_custom_server_url') || '';
    setCustomInput(stored);
  };

  useEffect(() => {
    if (isOpen) {
      refreshState();
      setFeedback(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleAutoDiscover = async () => {
    setIsTesting(true);
    setFeedback({ type: 'info', text: 'Querying Supabase registry and pinging servers...' });
    try {
      const url = await initServerDiscovery(true);
      if (url) {
        setFeedback({ type: 'success', text: `Connected to active server: ${url}` });
      } else {
        setFeedback({ type: 'error', text: 'No live servers responded. Check internet connection.' });
      }
    } catch (err: any) {
      setFeedback({ type: 'error', text: `Discovery failed: ${err.message || 'Unknown error'}` });
    } finally {
      setIsTesting(false);
      refreshState();
    }
  };

  const handleSaveCustom = async () => {
    const trimmed = customInput.trim();
    if (!trimmed) {
      handleClearCustom();
      return;
    }

    if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
      setFeedback({ type: 'error', text: 'URL must start with http:// or https://' });
      return;
    }

    setIsTesting(true);
    setFeedback({ type: 'info', text: `Testing connection to ${trimmed}...` });
    try {
      const ok = await setCustomServerUrl(trimmed);
      if (ok) {
        setFeedback({ type: 'success', text: 'Successfully connected and saved!' });
      } else {
        setFeedback({ type: 'error', text: `Could not reach ${trimmed}/api/ping` });
      }
    } catch (err: any) {
      setFeedback({ type: 'error', text: `Connection error: ${err.message}` });
    } finally {
      setIsTesting(false);
      refreshState();
    }
  };

  const handleClearCustom = async () => {
    setIsTesting(true);
    await setCustomServerUrl(null);
    setFeedback({ type: 'info', text: 'Reset to Cloud Auto-Discovery mode.' });
    setIsTesting(false);
    refreshState();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
    >
      <div
        className="relative w-full max-w-lg bg-[#141414] border border-neutral-800 rounded-2xl shadow-2xl p-6 sm:p-8 space-y-6 text-white"
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-neutral-800">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-red-600/20 text-red-500 border border-red-500/30">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base sm:text-lg font-bold tracking-tight">Backend Server Connection</h3>
              <p className="text-xs text-neutral-400">Server-Agnostic Cloud Discovery & LAN Configuration</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer"
            aria-label="Close"
            tabIndex={0}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Current Connection Status */}
        <div className="p-4 rounded-xl bg-neutral-900/80 border border-neutral-800/80 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-neutral-400 font-medium">Platform Mode:</span>
            <span className="font-semibold text-neutral-200">
              {status.isNative ? '📱 Android TV / JioOS Native App' : '🌐 Standard Web Browser'}
            </span>
          </div>

          <div className="flex items-center justify-between text-xs">
            <span className="text-neutral-400 font-medium">Active Server Endpoint:</span>
            <span className="font-mono font-bold text-emerald-400 break-all text-right max-w-[260px] truncate" title={status.activeUrl}>
              {status.activeUrl}
            </span>
          </div>

          <div className="flex items-center justify-between text-xs">
            <span className="text-neutral-400 font-medium">Resolution Strategy:</span>
            <span className="font-semibold text-neutral-300">
              {status.isCustom ? '⚙️ Manual Custom Override' : '☁️ Cloud Auto-Discovery (Supabase Beacon)'}
            </span>
          </div>

          {status.lastHeartbeat && (
            <div className="flex items-center justify-between text-xs text-neutral-500">
              <span>Registry Heartbeat:</span>
              <span>{new Date(status.lastHeartbeat).toLocaleString()}</span>
            </div>
          )}
        </div>

        {/* Feedback Alert */}
        {feedback && (
          <div
            className={`p-3 rounded-xl text-xs flex items-center gap-2.5 border ${
              feedback.type === 'success'
                ? 'bg-emerald-950/40 border-emerald-800/50 text-emerald-300'
                : feedback.type === 'error'
                ? 'bg-red-950/40 border-red-800/50 text-red-300'
                : 'bg-blue-950/40 border-blue-800/50 text-blue-300'
            }`}
          >
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400" />
            ) : feedback.type === 'error' ? (
              <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
            ) : (
              <RefreshCw className="w-4 h-4 shrink-0 text-blue-400 animate-spin" />
            )}
            <span>{feedback.text}</span>
          </div>
        )}

        {/* Action 1: Auto-Discover Button */}
        <div className="space-y-2">
          <label className="text-xs font-semibold text-neutral-300 flex items-center gap-1.5">
            <Globe className="w-3.5 h-3.5 text-neutral-400" />
            Cloud Server Registry Discovery
          </label>
          <button
            type="button"
            onClick={handleAutoDiscover}
            disabled={isTesting}
            tabIndex={0}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-neutral-800 hover:bg-neutral-700 active:scale-98 text-white rounded-xl text-xs sm:text-sm font-semibold transition border border-neutral-700/80 cursor-pointer disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isTesting ? 'animate-spin' : ''}`} />
            <span>🔄 Auto-Discover Server from Cloud</span>
          </button>
          <p className="text-[11px] text-neutral-500">
            Queries Supabase for the current active Cloudflare tunnel or VPS IP without reinstalling the APK.
          </p>
        </div>

        {/* Action 2: Manual Custom Server URL Input */}
        <div className="space-y-2">
          <label className="text-xs font-semibold text-neutral-300 flex items-center gap-1.5">
            <Wifi className="w-3.5 h-3.5 text-neutral-400" />
            Custom Server / Local LAN IP Override
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              placeholder="e.g. http://192.168.1.100:8080 or https://..."
              tabIndex={0}
              className="flex-1 bg-black/60 border border-neutral-800 rounded-xl px-3.5 py-2 text-xs sm:text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-red-600"
            />
            <button
              type="button"
              onClick={handleSaveCustom}
              disabled={isTesting}
              tabIndex={0}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 active:scale-98 text-white rounded-xl text-xs sm:text-sm font-bold transition shadow-md shadow-red-600/20 cursor-pointer disabled:opacity-50"
            >
              Save & Test
            </button>
          </div>
          {status.isCustom && (
            <button
              type="button"
              onClick={handleClearCustom}
              disabled={isTesting}
              tabIndex={0}
              className="text-xs text-neutral-400 hover:text-red-400 underline cursor-pointer"
            >
              Reset to Cloud Auto-Discovery
            </button>
          )}
        </div>

        {/* Footer */}
        <div className="pt-2 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            tabIndex={0}
            className="px-5 py-2 rounded-xl text-xs sm:text-sm font-semibold bg-neutral-800 hover:bg-neutral-700 text-white transition cursor-pointer"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

