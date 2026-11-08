import React, { useState, useEffect, useRef } from 'react';
import { Mail, RefreshCw, LogOut, ShieldCheck, AlertCircle } from 'lucide-react';
import { useAuthStore } from '../../store/useAuthStore';
import { api } from '../../lib/api';
import { sanitizeAuthError } from '../../lib/errorHandler';

export const VerifyEmailScreen: React.FC = () => {
  const {
    user,
    pendingVerificationEmail,
    serverCooldownSeconds,
    verificationNotice,
    clearPendingVerification,
    signOut,
    completeVerificationAndEnter,
    setAuthNotice,
    setAuthMode
  } = useAuthStore();

  const targetEmail = (user?.email || pendingVerificationEmail || '').trim().toLowerCase();

  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(verificationNotice || null);
  const [error, setError] = useState<string | null>(null);
  const [honeypot, setHoneypot] = useState('');

  const isCheckingRef = useRef(false);
  const hasCompletedRef = useRef(false);

  // Server-synced countdown timer (no localStorage / sessionStorage dependency)
  const [cooldown, setCooldown] = useState<number>(serverCooldownSeconds || 0);

  useEffect(() => {
    if (serverCooldownSeconds > 0) {
      setCooldown(serverCooldownSeconds);
    }
  }, [serverCooldownSeconds]);

  useEffect(() => {
    if (verificationNotice) {
      setMessage(verificationNotice);
    }
  }, [verificationNotice]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => {
      setCooldown(prev => Math.max(0, prev - 1));
    }, 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  // Background Cross-Device Auto-Polling (Every 4 Seconds)
  useEffect(() => {
    if (!targetEmail) return;

    let timerId: any = null;

    const performSilentCheck = async () => {
      if (hasCompletedRef.current || isCheckingRef.current) return;
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;

      try {
        isCheckingRef.current = true;
        const status = await api.getVerificationStatus(targetEmail);
        if (status.is_verified) {
          hasCompletedRef.current = true;
          setMessage('✓ Email verified! Entering StreamVault...');
          setError(null);
          await completeVerificationAndEnter(targetEmail);
        }
      } catch {
        // Silent poll network error — safe to ignore during background checks
      } finally {
        isCheckingRef.current = false;
      }
    };

    // Run poll every 4000ms
    timerId = setInterval(performSilentCheck, 4000);

    // Also run immediate silent check on window focus or visibility change
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        performSilentCheck();
      }
    };
    window.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleVisibility);

    return () => {
      if (timerId) clearInterval(timerId);
      window.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
    };
  }, [targetEmail, completeVerificationAndEnter]);

  const handleResend = async () => {
    if (cooldown > 0 || !targetEmail || hasCompletedRef.current) return;
    setLoading(true);
    setMessage(null);
    setError(null);

    try {
      const res = await api.resendVerification(targetEmail, honeypot);
      if (res.status === 'already_verified' || res.is_verified) {
        hasCompletedRef.current = true;
        setMessage('✓ Email verified! Entering StreamVault...');
        await completeVerificationAndEnter(targetEmail);
        return;
      }
      setMessage(res.message || 'A fresh verification link has been sent to your email.');
      setCooldown(res.cooldown_remaining_seconds || 120);
    } catch (err: any) {
      if (err.status === 429) {
        const remaining = err.cooldownRemainingSeconds || 120;
        setCooldown(remaining);
        setError(err.message || `Please wait ${remaining}s before requesting another link.`);
      } else {
        setError(sanitizeAuthError(err.message || err));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleCheckConfirmed = async () => {
    if (!targetEmail || hasCompletedRef.current) return;
    setLoading(true);
    setError(null);

    try {
      isCheckingRef.current = true;
      // 1. Inspect live server-side verification status via RPC
      const status = await api.getVerificationStatus(targetEmail);
      if (status.is_verified) {
        hasCompletedRef.current = true;
        setMessage('✓ Email verified! Entering StreamVault...');
        await completeVerificationAndEnter(targetEmail);
        return;
      }

      // Still unverified: sync cooldown and show informative timing
      if (status.cooldown_remaining_seconds > 0) {
        setCooldown(status.cooldown_remaining_seconds);
      }

      if (status.last_sent_seconds_ago !== undefined && status.last_sent_seconds_ago !== null) {
        if (status.last_sent_seconds_ago >= 900) {
          setError('Your previous verification link has expired (15+ minutes old). Please click "Resend Verification Email" below to get a fresh link.');
        } else {
          setError(`Your email (${targetEmail}) is not verified yet. Please click the verification link in your email inbox first.`);
        }
      } else {
        setError(`Your email (${targetEmail}) is not verified yet. Please click the verification link in your email inbox first.`);
      }
    } catch (err: any) {
      setError(sanitizeAuthError(err.message || err));
    } finally {
      isCheckingRef.current = false;
      setLoading(false);
    }
  };

  const handleSignOut = async () => {
    hasCompletedRef.current = true;
    clearPendingVerification();
    await signOut();
    window.history.pushState({}, '', '/');
  };

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-4 relative overflow-hidden">
      {/* Background Glow */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-red-600/10 blur-[150px] pointer-events-none rounded-full" />

      <div className="max-w-md w-full bg-[#141414] border border-neutral-800 rounded-2xl p-8 shadow-2xl relative z-10 text-center">
        {/* Invisible bot honeypot */}
        <input
          type="text"
          name="_honeypot"
          value={honeypot}
          onChange={(e) => setHoneypot(e.target.value)}
          style={{ display: 'none' }}
          tabIndex={-1}
          autoComplete="off"
          aria-hidden="true"
        />

        {/* Logo */}
        <div className="mb-6 flex justify-center">
          <span className="text-red-600 font-black tracking-widest text-3xl font-mono">
            STREAMVAULT
          </span>
        </div>

        <div className="w-16 h-16 bg-red-600/20 text-red-500 rounded-full flex items-center justify-center mx-auto mb-5 border border-red-500/30">
          <Mail className="w-8 h-8" />
        </div>

        <h1 className="text-2xl font-bold text-white mb-2">Verify Your Email</h1>
        <p className="text-neutral-400 text-sm mb-6 leading-relaxed">
          We have sent a confirmation link to <span className="text-white font-medium">{targetEmail || 'your email'}</span>.
          Email confirmation is compulsory to stream movies, search the catalog, and protect your account.
        </p>

        {message && (
          <div className="mb-4 p-3 bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 rounded-lg text-xs flex items-center gap-2 text-left">
            <ShieldCheck className="w-4 h-4 shrink-0" />
            <span>{message}</span>
          </div>
        )}

        {error && (
          <div className="mb-4 p-3 bg-red-500/15 border border-red-500/30 text-red-400 rounded-lg text-xs flex items-center gap-2 text-left">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="space-y-3">
          <button
            onClick={handleCheckConfirmed}
            disabled={loading}
            className="w-full py-3 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-xl transition duration-200 flex items-center justify-center gap-2 shadow-lg shadow-red-600/30 disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            I Have Verified My Email
          </button>

          <button
            onClick={handleResend}
            disabled={cooldown > 0 || loading}
            className="w-full py-2.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 font-medium rounded-xl transition duration-200 text-sm disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {cooldown > 0 ? `Resend available in ${cooldown}s...` : 'Resend Verification Email'}
          </button>

          <button
            onClick={handleSignOut}
            className="w-full py-2 text-neutral-500 hover:text-neutral-300 transition text-xs flex items-center justify-center gap-1.5 pt-2 cursor-pointer"
          >
            <LogOut className="w-3.5 h-3.5" />
            Sign Out & Switch Account
          </button>
        </div>
      </div>
    </div>
  );
};
