import React, { useState, useEffect } from 'react';
import { Lock, Eye, EyeOff, ShieldCheck, CheckCircle2, AlertCircle, XCircle } from 'lucide-react';
import { useAuthStore, cleanAuthUrlParams } from '../../store/useAuthStore';
import { useUIStore } from '../../store/useUIStore';
import { supabase } from '../../lib/supabase';
import { sanitizeAuthError } from '../../lib/errorHandler';

export const ResetPasswordScreen: React.FC = () => {
  const { user, updatePassword, setPasswordRecovery, cancelPasswordRecovery } = useAuthStore();
  const { showToast } = useUIStore();

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Strict Recovery Session Guard: verify active, authenticated recovery session exists in memory
  useEffect(() => {
    cleanAuthUrlParams();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session || !session.user) {
        useAuthStore.setState({ isPasswordRecovery: false, user: null, session: null });
        cleanAuthUrlParams();
      }
    });
  }, []);

  // Password Strength Criteria
  const hasMinLength = password.length >= 8;
  const hasLetter = /[a-zA-Z]/.test(password);
  const hasNumber = /[0-9]/.test(password);
  const hasSpecial = /[^a-zA-Z0-9]/.test(password);
  const isMatch = password.length > 0 && password === confirmPassword;

  const passedCriteria = [hasMinLength, hasLetter, hasNumber, hasSpecial].filter(Boolean).length;
  const strengthLevel = passedCriteria <= 1 ? 'Weak' : passedCriteria <= 3 ? 'Medium' : 'Strong';
  const strengthColor =
    passedCriteria <= 1 ? 'bg-red-500' : passedCriteria <= 3 ? 'bg-amber-500' : 'bg-emerald-500';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!hasMinLength) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    if (!hasNumber || !hasLetter) {
      setError('Password must contain both letters and numbers.');
      return;
    }
    if (!isMatch) {
      setError('Passwords do not match. Please re-enter.');
      return;
    }

    const activeEmail = user?.email || '';
    setLoading(true);
    const res = await updatePassword(password);
    setLoading(false);

    if (res.error) {
      setError(sanitizeAuthError(res.error));
    } else {
      try {
        const bc = new BroadcastChannel('sv_auth_channel');
        bc.postMessage({ type: 'SV_PASSWORD_RESET_COMPLETED', email: activeEmail, timestamp: Date.now() });
        bc.close();
      } catch {}
      try {
        localStorage.setItem('sv_password_reset_completed', JSON.stringify({ email: activeEmail, timestamp: Date.now() }));
      } catch {}

      setPasswordRecovery(false);
      cleanAuthUrlParams();
      if (useAuthStore.getState().user) {
        window.history.replaceState({}, '', '/home');
        window.dispatchEvent(new Event('sv:navigation'));
        showToast('✓ Password updated successfully! Welcome back.');
      } else {
        window.history.replaceState({}, '', '/');
        window.dispatchEvent(new Event('sv:navigation'));
        showToast('✓ Password updated successfully! Please sign in with your new password.');
      }
    }
  };

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-4 relative overflow-hidden">
      {/* Background Poster Collage / Dark Gradient Overlay */}
      <div
        className="absolute inset-0 opacity-20 bg-cover bg-center pointer-events-none scale-105 blur-xs"
        style={{
          backgroundImage: `url('https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?w=1600&auto=format&fit=crop&q=80')`
        }}
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black via-black/85 to-black/70 pointer-events-none" />

      {/* Main Card */}
      <div className="max-w-md w-full bg-[#141414]/90 backdrop-blur-xl border border-neutral-800 rounded-3xl p-8 sm:p-10 shadow-2xl relative z-10">
        {/* Brand Header */}
        <div className="text-center mb-6">
          <h1 className="text-red-600 font-black tracking-widest text-3xl font-mono">
            STREAMVAULT
          </h1>
          <p className="text-neutral-400 text-xs mt-1">Production Security & Account Recovery</p>
        </div>

        {/* Verified Identity Badge */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-4 mb-6 flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl bg-red-600/10 border border-red-600/30 flex items-center justify-center shrink-0 mt-0.5">
            <ShieldCheck className="w-5 h-5 text-red-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-white text-sm font-semibold flex items-center gap-2">
              Set New Password
            </h2>
            <p className="text-neutral-400 text-xs mt-0.5 truncate">
              Account: <span className="text-neutral-200 font-medium">{user?.email || 'Authenticated User'}</span>
            </p>
            <p className="text-neutral-500 text-[11px] mt-1">
              This recovery session is cryptographically bound to this account.
            </p>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-5 p-3.5 bg-red-950/40 border border-red-800/60 rounded-xl text-red-200 text-xs flex items-center gap-2.5">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* New Password */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
              New Password
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 text-neutral-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter new password"
                required
                className="w-full bg-neutral-900/90 border border-neutral-700/80 rounded-xl py-3 pl-10 pr-11 text-white text-sm placeholder-neutral-500 focus:outline-none focus:border-red-600 transition"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-white"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Confirm Password */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
              Confirm New Password
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 text-neutral-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type={showConfirmPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter new password"
                required
                className="w-full bg-neutral-900/90 border border-neutral-700/80 rounded-xl py-3 pl-10 pr-11 text-white text-sm placeholder-neutral-500 focus:outline-none focus:border-red-600 transition"
              />
              <button
                type="button"
                onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-white"
              >
                {showConfirmPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Password Strength Indicator */}
          {password.length > 0 && (
            <div className="bg-neutral-900/50 p-3 rounded-xl border border-neutral-800/80 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-neutral-400">Strength:</span>
                <span className={`font-semibold ${passedCriteria <= 1 ? 'text-red-400' : passedCriteria <= 3 ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {strengthLevel}
                </span>
              </div>
              <div className="w-full h-1.5 bg-neutral-800 rounded-full overflow-hidden flex gap-1">
                {[1, 2, 3, 4].map((step) => (
                  <div
                    key={step}
                    className={`h-full flex-1 transition-all duration-300 ${
                      step <= passedCriteria ? strengthColor : 'bg-neutral-800'
                    }`}
                  />
                ))}
              </div>
              <div className="grid grid-cols-2 gap-1.5 pt-1 text-[11px]">
                <span className={`flex items-center gap-1 ${hasMinLength ? 'text-emerald-400' : 'text-neutral-500'}`}>
                  {hasMinLength ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  8+ characters
                </span>
                <span className={`flex items-center gap-1 ${hasNumber ? 'text-emerald-400' : 'text-neutral-500'}`}>
                  {hasNumber ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  Includes number
                </span>
                <span className={`flex items-center gap-1 ${hasLetter ? 'text-emerald-400' : 'text-neutral-500'}`}>
                  {hasLetter ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  Includes letter
                </span>
                <span className={`flex items-center gap-1 ${isMatch ? 'text-emerald-400' : 'text-neutral-500'}`}>
                  {isMatch ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                  Passwords match
                </span>
              </div>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={loading || !hasMinLength || !isMatch}
            className="w-full py-3.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:pointer-events-none text-white rounded-xl font-bold text-sm shadow-lg shadow-red-600/30 transition duration-200 flex items-center justify-center gap-2 mt-2"
          >
            {loading ? (
              <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              'Update Password'
            )}
          </button>

          {/* Cancel Option */}
          <button
            type="button"
            onClick={cancelPasswordRecovery}
            className="w-full py-2.5 text-xs text-neutral-400 hover:text-white transition font-medium"
          >
            Cancel & Return to Sign In
          </button>
        </form>
      </div>
    </div>
  );
};
