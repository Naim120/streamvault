import React, { useState, useEffect, useRef } from 'react';
import { Mail, Lock, User, Eye, EyeOff, AlertCircle, CheckCircle2, X } from 'lucide-react';
import { useAuthStore, cleanAuthUrlParams } from '../../store/useAuthStore';
import { api } from '../../lib/api';
import { sanitizeAuthError } from '../../lib/errorHandler';

type Mode = 'login' | 'register' | 'forgot';

export interface AuthModalProps {
  isOpen?: boolean;
  onClose?: () => void;
  initialEmail?: string;
  initialMode?: 'login' | 'register' | 'forgot';
  titleNotice?: string | null;
}

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen = true,
  onClose,
  initialEmail = '',
  initialMode,
  titleNotice,
}) => {
  const { signIn, signUp, authNotice, setAuthNotice, authMode, setAuthMode } = useAuthStore();
  const [mode, setMode] = useState<Mode>(initialMode || authMode || 'login');
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [honeypot, setHoneypot] = useState('');
  const passwordInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (initialMode) {
      setMode(initialMode);
    } else if (authMode) {
      setMode(authMode);
    }
  }, [initialMode, authMode]);

  useEffect(() => {
    if (initialEmail) {
      setEmail(initialEmail);
    }
  }, [initialEmail]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && onClose) {
        onClose();
      }
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // 60-second cooldown timer persisted in sessionStorage so refresh cannot bypass
  const [resetCooldown, setResetCooldown] = useState<number>(() => {
    if (typeof window !== 'undefined') {
      const savedExpiry = sessionStorage.getItem('streamvault_forgot_cooldown_expiry');
      if (savedExpiry) {
        const remaining = Math.ceil((parseInt(savedExpiry, 10) - Date.now()) / 1000);
        return remaining > 0 ? remaining : 0;
      }
    }
    return 0;
  });

  const startCooldown = (seconds: number) => {
    setResetCooldown(seconds);
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('streamvault_forgot_cooldown_expiry', (Date.now() + seconds * 1000).toString());
    }
  };

  useEffect(() => {
    let timer: any;
    if (resetCooldown > 0) {
      timer = setTimeout(() => {
        const next = resetCooldown - 1;
        setResetCooldown(next);
        if (next <= 0 && typeof window !== 'undefined') {
          sessionStorage.removeItem('streamvault_forgot_cooldown_expiry');
        }
      }, 1000);
    }
    return () => clearTimeout(timer);
  }, [resetCooldown]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);
    setLoading(true);

    try {
      const cleanedEmail = email.trim().toLowerCase();

      if (mode === 'login') {
        const res = await signIn(cleanedEmail, password);
        if (res.error) {
          const errLow = res.error.toLowerCase();
          if (errLow.includes('email not confirmed') || errLow.includes('not confirmed') || errLow.includes('confirm your email')) {
            try {
              const precheck = await api.signupPrecheck(cleanedEmail);
              useAuthStore.getState().setPendingVerification(
                cleanedEmail,
                precheck.cooldown_remaining_seconds,
                precheck.message || 'Please verify your email address before signing in.',
                password
              );
              onClose?.();
              return;
            } catch {
              useAuthStore.getState().setPendingVerification(
                cleanedEmail,
                120,
                'Please verify your email address before signing in.',
                password
              );
              onClose?.();
              return;
            }
          }
          setError(sanitizeAuthError(res.error));
        } else {
          cleanAuthUrlParams();
          onClose?.();
        }
      } else if (mode === 'register') {
        if (!fullName.trim()) {
          setError('Please provide your full name');
          setLoading(false);
          return;
        }

        // 1. Pre-Check Before Sign-Up
        try {
          const precheck = await api.signupPrecheck(cleanedEmail);

          if (precheck.status === 'already_verified') {
            setMode('login');
            setAuthNotice('This email is already registered and verified. Please sign in to your account.');
            setError(null);
            setTimeout(() => {
              passwordInputRef.current?.focus();
            }, 50);
            setLoading(false);
            return;
          }

          if (
            precheck.status === 'unverified_expired_resent' ||
            precheck.status === 'unverified_existing' ||
            precheck.status === 'unverified_cooldown'
          ) {
            useAuthStore.getState().setPendingVerification(
              cleanedEmail,
              precheck.cooldown_remaining_seconds,
              precheck.message || 'Please check your email for the verification link.',
              password
            );
            onClose?.();
            setLoading(false);
            return;
          }
        } catch (err: any) {
          console.warn('Precheck error, continuing to direct sign-up:', err);
        }

        // New user sign-up
        const res = await signUp(cleanedEmail, password, fullName);
        if (res.error) {
          setError(sanitizeAuthError(res.error));
        } else if (!res.emailConfirmed) {
          useAuthStore.getState().setPendingVerification(
            cleanedEmail,
            120,
            'Registration successful! Please check your email to verify your account.',
            password
          );
          onClose?.();
        }
      } else if (mode === 'forgot') {
        if (!cleanedEmail) {
          setError('Please enter a valid email address.');
          setLoading(false);
          return;
        }

        if (resetCooldown > 0) return;

        try {
          const res = await api.forgotPassword(cleanedEmail, honeypot);
          setSuccessMsg(res.message);
          startCooldown(res.cooldown_seconds || 60);
        } catch (apiErr: any) {
          setError(apiErr.message || 'Failed to dispatch password reset link');
          if (apiErr.retryAfterSeconds) {
            startCooldown(apiErr.retryAfterSeconds);
          } else if (apiErr.status === 429) {
            startCooldown(60);
          }
        }
      }
    } catch (err: any) {
      setError(sanitizeAuthError(err));
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  const cardContent = (
    <div
      className="max-w-md w-full bg-[#141414]/95 backdrop-blur-xl border border-neutral-800 rounded-3xl p-8 sm:p-10 shadow-2xl relative z-10"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Close Button if rendered as modal */}
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          className="absolute top-5 right-5 text-neutral-400 hover:text-white transition p-1.5 rounded-full hover:bg-neutral-800 cursor-pointer"
          aria-label="Close auth dialog"
        >
          <X className="w-5 h-5" />
        </button>
      )}

      {/* Brand */}
      <div className="text-center mb-6">
        <h1 className="text-red-600 font-black tracking-widest text-3xl font-mono">
          STREAMVAULT
        </h1>
        <p className="text-neutral-400 text-xs mt-1">Unlimited movies, TV shows, and more</p>
        {titleNotice && (
          <div className={`mt-3 px-3 py-1.5 rounded-xl text-xs font-medium flex items-center justify-center gap-1.5 ${
            titleNotice.includes('✓')
              ? 'bg-emerald-950/40 border border-emerald-800/60 text-emerald-200'
              : 'bg-red-950/40 border border-red-800/50 text-red-200'
          }`}>
            {titleNotice.includes('✓') && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />}
            <span>{titleNotice}</span>
          </div>
        )}
      </div>

      {/* Global Auth Notice (e.g. Password Updated, Expired OTP link) */}
      {authNotice && authNotice !== titleNotice && (
          <div className={`mb-5 p-3.5 rounded-xl text-xs flex items-center gap-2.5 ${
            authNotice.includes('✓')
              ? 'bg-emerald-950/40 border border-emerald-800/60 text-emerald-200'
              : 'bg-amber-950/40 border border-amber-800/60 text-amber-200'
          }`}>
            {authNotice.includes('✓') ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
            )}
            <span className="flex-1">{authNotice}</span>
            <button
              type="button"
              onClick={() => setAuthNotice(null)}
              className="text-neutral-400 hover:text-white text-xs ml-1 font-bold"
            >
              ✕
            </button>
          </div>
        )}

        {/* Tab Switcher (Login vs Register) */}
        {mode !== 'forgot' && (
          <div className="flex bg-neutral-900/80 p-1 rounded-xl mb-6 border border-neutral-800">
            <button
              type="button"
              onClick={() => { setMode('login'); setError(null); setSuccessMsg(null); }}
              className={`flex-1 py-2 rounded-lg text-sm font-semibold transition ${
                mode === 'login' ? 'bg-red-600 text-white shadow-md' : 'text-neutral-400 hover:text-white'
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              onClick={() => { setMode('register'); setError(null); setSuccessMsg(null); }}
              className={`flex-1 py-2 rounded-lg text-sm font-semibold transition ${
                mode === 'register' ? 'bg-red-600 text-white shadow-md' : 'text-neutral-400 hover:text-white'
              }`}
            >
              Sign Up
            </button>
          </div>
        )}

        {/* Mode Title for Forgot Password */}
        {mode === 'forgot' && (
          <div className="mb-6 text-center">
            <h2 className="text-white text-lg font-bold">Reset Password</h2>
            <p className="text-neutral-400 text-xs mt-1">
              Enter your email address and we'll dispatch a secure recovery link.
            </p>
          </div>
        )}

        {/* Local Error Alert */}
        {error && (
          <div className="mb-5 p-3.5 bg-red-950/40 border border-red-800/60 rounded-xl text-red-200 text-xs flex items-center gap-2.5">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Local Success Alert */}
        {successMsg && (
          <div className="mb-5 p-3.5 bg-emerald-950/40 border border-emerald-800/60 rounded-xl text-emerald-200 text-xs flex items-center gap-2.5">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>{successMsg}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Honeypot field for bot deterrence */}
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

          {mode === 'register' && (
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
                Full Name
              </label>
              <div className="relative">
                <User className="w-4 h-4 text-neutral-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="John Doe"
                  required
                  className="w-full bg-neutral-900/90 border border-neutral-700/80 rounded-xl py-3 pl-10 pr-4 text-white text-sm placeholder-neutral-500 focus:outline-none focus:border-red-600 transition"
                />
              </div>
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
              Email Address
            </label>
            <div className="relative">
              <Mail className="w-4 h-4 text-neutral-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                required
                className="w-full bg-neutral-900/90 border border-neutral-700/80 rounded-xl py-3 pl-10 pr-4 text-white text-sm placeholder-neutral-500 focus:outline-none focus:border-red-600 transition"
              />
            </div>
          </div>

          {mode !== 'forgot' && (
            <div>
              <div className="flex justify-between items-center mb-1.5">
                <label className="block text-xs font-semibold uppercase tracking-wider text-neutral-400">
                  Password
                </label>
                {mode === 'login' && (
                  <button
                    type="button"
                    onClick={() => { setMode('forgot'); setError(null); setSuccessMsg(null); }}
                    className="text-xs text-neutral-400 hover:text-white transition"
                  >
                    Forgot?
                  </button>
                )}
              </div>
              <div className="relative">
                <Lock className="w-4 h-4 text-neutral-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  ref={passwordInputRef}
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="w-full bg-neutral-900/90 border border-neutral-700/80 rounded-xl py-3 pl-10 pr-11 text-white text-sm placeholder-neutral-500 focus:outline-none focus:border-red-600 transition"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="text-neutral-500 hover:text-neutral-300 absolute right-3.5 top-1/2 -translate-y-1/2"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || (mode === 'forgot' && resetCooldown > 0)}
            className="w-full py-3.5 bg-red-600 hover:bg-red-700 text-white font-bold rounded-xl transition duration-200 mt-2 shadow-lg shadow-red-600/30 disabled:opacity-50 disabled:cursor-not-allowed text-sm flex items-center justify-center gap-2"
          >
            {loading ? (
              <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : mode === 'login' ? (
              'Sign In'
            ) : mode === 'register' ? (
              'Create Account'
            ) : resetCooldown > 0 ? (
              `Resend available in ${resetCooldown}s...`
            ) : (
              'Send Reset Link'
            )}
          </button>
        </form>

        {mode === 'forgot' && (
          <div className="text-center mt-6">
            <button
              onClick={() => { setMode('login'); setError(null); setSuccessMsg(null); }}
              className="text-xs text-neutral-400 hover:text-white transition"
            >
              ← Back to Sign In
            </button>
          </div>
        )}

        <div className="mt-8 pt-6 border-t border-neutral-800/80 text-center">
          <p className="text-neutral-500 text-xs">
            Protected with StreamVault Security.
          </p>
        </div>
      </div>
  );

  if (onClose) {
    return (
      <div
        className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/85 backdrop-blur-md overflow-y-auto animate-fade-in"
        onClick={onClose}
      >
        {cardContent}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-4 relative overflow-hidden">
      <div 
        className="absolute inset-0 opacity-20 bg-cover bg-center pointer-events-none scale-105 blur-xs"
        style={{
          backgroundImage: `url('https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?w=1600&auto=format&fit=crop&q=80')`
        }}
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black via-black/85 to-black/70 pointer-events-none" />
      {cardContent}
    </div>
  );
};
