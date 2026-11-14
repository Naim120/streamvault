import { create } from 'zustand';
import { User, Session } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';
import { api } from '../lib/api';
import { Profile } from '../types';
import { startTelemetrySession, sendTelemetryLogout, getCachedSessionLogId, clearTelemetrySession } from '../lib/telemetry';
import { invalidatePrewarmCache } from '../lib/streamPrewarm';
import { usePlayerStore } from './usePlayerStore';
import { useUIStore } from './useUIStore';

interface AuthState {
  user: User | null;
  session: Session | null;
  profile: Profile | null;
  loading: boolean;
  isEmailConfirmed: boolean;
  isPasswordRecovery: boolean;
  pendingVerificationEmail: string | null;
  pendingSignUpPassword: string | null;
  authModalSuccessNotice: string | null;
  verifiedEmailForLogin: string | null;
  serverCooldownSeconds: number;
  verificationNotice: string | null;
  authMode: 'login' | 'register' | 'forgot';
  setAuthMode: (mode: 'login' | 'register' | 'forgot') => void;
  authNotice: string | null;
  setAuthNotice: (msg: string | null) => void;
  setAuthModalSuccessNotice: (msg: string | null) => void;
  setVerifiedEmailForLogin: (email: string | null) => void;
  setPendingVerification: (email: string | null, cooldown?: number, notice?: string | null, password?: string | null) => void;
  clearPendingVerification: () => void;
  completeVerificationAndEnter: (email: string) => Promise<'signed_in' | 'redirect_to_login'>;
  initAuth: () => Promise<void>;
  signIn: (email: string, pass: string) => Promise<{ error: string | null }>;
  signUp: (email: string, pass: string, fullName: string) => Promise<{ error: string | null; emailConfirmed: boolean }>;
  signOut: () => Promise<void>;
  resendVerification: (email: string) => Promise<{ error: string | null }>;
  updateAvatar: (avatarUrl: string) => Promise<void>;
  refreshProfile: () => Promise<void>;
  updatePassword: (newPassword: string) => Promise<{ error: string | null }>;
  setPasswordRecovery: (val: boolean) => void;
  cancelPasswordRecovery: () => Promise<void>;
}

export async function getReachableRedirectUrl(path: string = '/home'): Promise<string> {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  if (typeof window === 'undefined') {
    const defaultOrigin = import.meta.env.VITE_APP_URL || 'http://localhost:8080';
    return `${defaultOrigin.replace(/\/+$/, '')}${cleanPath}`;
  }

  // Always attempt to get the canonical reachable origin from the backend first
  // (handles LAN IP, Cloudflare tunnel domains, and PUBLIC_SITE_URL env)
  try {
    const res = await api.getRedirectOrigin();
    if (res?.origin && !res.origin.includes('localhost') && !res.origin.includes('127.0.0.1')) {
      return `${res.origin.replace(/\/+$/, '')}${cleanPath}`;
    }
  } catch (e) {
    console.warn('Failed to fetch redirect-origin from backend:', e);
  }

  const hostname = window.location.hostname;
  // If the browser origin itself is already a reachable non-localhost origin, use it
  if (hostname !== 'localhost' && hostname !== '127.0.0.1') {
    return `${window.location.origin.replace(/\/+$/, '')}${cleanPath}`;
  }

  const fallbackOrigin = import.meta.env.VITE_APP_URL || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080');
  return `${fallbackOrigin.replace(/\/+$/, '')}${cleanPath}`;
}

export function cleanAuthUrlParams(): void {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  url.searchParams.delete('mode');
  url.searchParams.delete('code');
  url.searchParams.delete('token_hash');
  url.searchParams.delete('type');
  url.searchParams.delete('error');
  url.searchParams.delete('error_code');
  url.searchParams.delete('error_description');
  const cleanSearch = url.searchParams.toString();
  const cleanUrl = url.pathname + (cleanSearch ? `?${cleanSearch}` : '');
  window.history.replaceState({}, document.title, cleanUrl || '/');
}

export function clearClientUserState(): void {
  invalidatePrewarmCache();
  usePlayerStore.getState().closePlayer();
  useUIStore.getState().resetUI();
  clearTelemetrySession();
  if (typeof window !== 'undefined') {
    try {
      localStorage.removeItem('sv_recovery_session_active');
    } catch {}
  }
}

let _authListenersRegistered = false;

function registerAuthListeners(get: () => AuthState, set: (fn: Partial<AuthState> | ((s: AuthState) => Partial<AuthState>)) => void) {
  if (_authListenersRegistered) return;
  _authListenersRegistered = true;

  supabase.auth.onAuthStateChange(async (event, session) => {
    if (event === 'SIGNED_OUT') {
      clearClientUserState();
      set({ user: null, session: null, profile: null, isEmailConfirmed: false, isPasswordRecovery: false });
      return;
    }

    const hasHashRecoveryOnEvent = typeof window !== 'undefined' && (
      (window.location.hash.includes('access_token=') || new URLSearchParams(window.location.hash.replace(/^#/, '')).has('access_token')) &&
      (window.location.hash.includes('type=recovery') || new URLSearchParams(window.location.hash.replace(/^#/, '')).get('type') === 'recovery')
    );

    const isStoredRecoverySession = typeof window !== 'undefined' &&
      !!session?.user?.id &&
      localStorage.getItem('sv_recovery_session_active') === session.user.id;

    const isRecovery = !!session && (
      event === 'PASSWORD_RECOVERY' || hasHashRecoveryOnEvent || isStoredRecoverySession
    );

    if (isRecovery) {
      if (session?.user?.id && typeof window !== 'undefined') {
        try {
          localStorage.setItem('sv_recovery_session_active', session.user.id);
        } catch {}
      }
      cleanAuthUrlParams();
      set({
        user: session?.user || null,
        session,
        isPasswordRecovery: true,
        loading: false
      });
      return;
    }

    if (session?.user) {
      const user = session.user;
      const isConfirmed = !!user.email_confirmed_at;
      set({ user, session, isEmailConfirmed: isConfirmed, isPasswordRecovery: false });

      if (isConfirmed) {
        await get().refreshProfile();
        if (event === 'SIGNED_IN') {
          cleanAuthUrlParams();
          if (session?.access_token) {
            startTelemetrySession(session.access_token, user.email);
          }
        }
      }
    }
  });

  if (typeof window !== 'undefined') {
    const handleSilentRevalidate = async () => {
      if (document.visibilityState === 'visible' && navigator.onLine) {
        try {
          const { data } = await supabase.auth.getSession();
          if (data?.session?.user) {
            const isStoredRec = localStorage.getItem('sv_recovery_session_active') === data.session.user.id;
            set({
              user: data.session.user,
              session: data.session,
              isEmailConfirmed: !!data.session.user.email_confirmed_at,
              ...(isStoredRec ? { isPasswordRecovery: true } : {})
            });
          } else {
            clearClientUserState();
            set({
              user: null,
              session: null,
              profile: null,
              isEmailConfirmed: false,
              isPasswordRecovery: false
            });
          }
        } catch (err) {
          console.warn('Silent session revalidation error:', err);
        }
      }
    };

    window.addEventListener('visibilitychange', handleSilentRevalidate);
    window.addEventListener('online', handleSilentRevalidate);
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  session: null,
  profile: null,
  loading: true,
  isEmailConfirmed: false,
  isPasswordRecovery: false,
  pendingVerificationEmail: null,
  pendingSignUpPassword: null,
  authModalSuccessNotice: null,
  verifiedEmailForLogin: null,
  serverCooldownSeconds: 0,
  verificationNotice: null,
  authMode: 'login',
  authNotice: null,

  setAuthMode: (mode: 'login' | 'register' | 'forgot') => set({ authMode: mode }),
  setPasswordRecovery: (val: boolean) => set({ isPasswordRecovery: val }),
  setAuthNotice: (msg: string | null) => set({ authNotice: msg }),
  setAuthModalSuccessNotice: (msg: string | null) => set({ authModalSuccessNotice: msg }),
  setVerifiedEmailForLogin: (email: string | null) => set({ verifiedEmailForLogin: email }),
  setPendingVerification: (email: string | null, cooldown = 120, notice = null, password = null) =>
    set({
      pendingVerificationEmail: email ? email.trim().toLowerCase() : null,
      serverCooldownSeconds: cooldown,
      verificationNotice: notice,
      ...(password !== undefined && { pendingSignUpPassword: password })
    }),
  clearPendingVerification: () =>
    set({
      pendingVerificationEmail: null,
      pendingSignUpPassword: null,
      serverCooldownSeconds: 0,
      verificationNotice: null
    }),

  completeVerificationAndEnter: async (email: string) => {
    const { pendingSignUpPassword } = get();
    // 1. Try refreshing existing session if one exists
    const { data: refreshData } = await supabase.auth.refreshSession().catch(() => ({ data: { session: null, user: null } }));
    if (refreshData?.user?.email_confirmed_at) {
      set({
        user: refreshData.user,
        session: refreshData.session,
        isEmailConfirmed: true,
        pendingVerificationEmail: null,
        pendingSignUpPassword: null,
        authModalSuccessNotice: null,
        verifiedEmailForLogin: null,
      });
      await get().refreshProfile();
      if (refreshData.session?.access_token) {
        startTelemetrySession(refreshData.session.access_token, refreshData.user.email);
      }
      window.history.replaceState({}, '', '/home');
      return 'signed_in';
    }

    // 2. If we have the user's signup/login password in memory on this tab, auto-sign them in seamlessly!
    if (pendingSignUpPassword && email) {
      const { data: signInData, error: signInErr } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password: pendingSignUpPassword,
      });
      if (!signInErr && signInData?.user && signInData?.session) {
        set({
          user: signInData.user,
          session: signInData.session,
          isEmailConfirmed: true,
          pendingVerificationEmail: null,
          pendingSignUpPassword: null,
          authModalSuccessNotice: null,
          verifiedEmailForLogin: null,
        });
        await get().refreshProfile();
        if (signInData.session?.access_token) {
          startTelemetrySession(signInData.session.access_token, signInData.user.email);
        }
        window.history.replaceState({}, '', '/home');
        return 'signed_in';
      }
    }

    // 3. Fallback (if page was refreshed and password is not in memory):
    // MUST clear user: null and pendingVerificationEmail: null so App.tsx exits VerifyEmailScreen and opens Sign In!
    await supabase.auth.signOut().catch(() => {});
    set({
      user: null,
      session: null,
      isEmailConfirmed: false,
      pendingVerificationEmail: null,
      pendingSignUpPassword: null,
      verifiedEmailForLogin: email.trim().toLowerCase(),
      authModalSuccessNotice: '✓ Your email is verified! Please enter your password to sign in.',
      authNotice: '✓ Your email is verified! Please enter your password to sign in.',
    });
    window.history.replaceState({}, '', '/');
    return 'redirect_to_login';
  },

  initAuth: async () => {
    registerAuthListeners(get, set);

    // 1. Detect direct URL access or error fragments
    if (typeof window !== 'undefined') {
      const hash = window.location.hash.replace(/^#/, '');
      const search = window.location.search.replace(/^\?/, '');
      const hashParams = new URLSearchParams(hash);
      const searchParams = new URLSearchParams(search);

      // Cryptographic Proof #1 Check: URL hash contains BOTH access_token AND type=recovery
      const hasHashRecoveryToken =
        (hash.includes('access_token=') || hashParams.has('access_token')) &&
        (hash.includes('type=recovery') || hashParams.get('type') === 'recovery');

      // Cryptographic Proof #2 Check: URL contains BOTH token_hash AND type=recovery
      const tokenHash = searchParams.get('token_hash') || hashParams.get('token_hash');
      const hasOtpRecoveryProof = !!tokenHash && (searchParams.get('type') === 'recovery' || hashParams.get('type') === 'recovery');

      // BLOCK DIRECT ACCESS: If someone visits ?mode=reset-password without valid cryptographic proof, strip it and keep isPasswordRecovery = false
      const hasDirectModeReset = searchParams.get('mode') === 'reset-password' || hashParams.get('mode') === 'reset-password';
      if (hasDirectModeReset && !hasHashRecoveryToken && !hasOtpRecoveryProof) {
        cleanAuthUrlParams();
      }

      const errorCode = hashParams.get('error_code') || searchParams.get('error_code');
      const errorParam = hashParams.get('error') || searchParams.get('error');
      const errorDesc = hashParams.get('error_description') || searchParams.get('error_description');

      if (
        errorCode === 'otp_expired' ||
        errorParam === 'access_denied' ||
        (errorDesc && (errorDesc.toLowerCase().includes('expired') || errorDesc.toLowerCase().includes('invalid')))
      ) {
        // Strip the ugly hash and error params from the URL cleanly
        cleanAuthUrlParams();

        // Sign out any stale session stored in localStorage to prevent false dashboard logins
        try {
          await supabase.auth.signOut();
        } catch {}

        clearClientUserState();
        set({
          user: null,
          session: null,
          profile: null,
          isEmailConfirmed: false,
          isPasswordRecovery: false,
          loading: false,
          authMode: 'forgot',
          authNotice: 'This password reset link has expired or was superseded by a newer request. Please request a fresh link below.'
        });
        return;
      }

      // 2. Cryptographic Proof #2: Verify OTP recovery token: token_hash AND type=recovery
      if (tokenHash && hasOtpRecoveryProof) {
        try {
          const { data, error } = await supabase.auth.verifyOtp({
            token_hash: tokenHash,
            type: 'recovery'
          });
          if (error) {
            cleanAuthUrlParams();
            clearClientUserState();
            set({
              user: null,
              session: null,
              profile: null,
              isEmailConfirmed: false,
              isPasswordRecovery: false,
              loading: false,
              authMode: 'forgot',
              authNotice: 'This password reset link has expired or was superseded by a newer request. Please request a fresh link below.'
            });
            return;
          }
          if (data.session) {
            if (data.session.user?.id && typeof window !== 'undefined') {
              try {
                localStorage.setItem('sv_recovery_session_active', data.session.user.id);
              } catch {}
            }
            cleanAuthUrlParams();
            set({
              user: data.session.user,
              session: data.session,
              isEmailConfirmed: true,
              isPasswordRecovery: true,
              loading: false,
              authNotice: null
            });
            return;
          }
        } catch (err) {
          console.error('Verify OTP error:', err);
        }
      }

      // 3. Detect PKCE Auth Code: code alongside type=recovery
      const code = searchParams.get('code');
      const typeParam = searchParams.get('type') || hashParams.get('type');
      if (code && typeParam === 'recovery') {
        try {
          const { data, error } = await supabase.auth.exchangeCodeForSession(code);
          if (error) {
            cleanAuthUrlParams();
            clearClientUserState();
            set({
              user: null,
              session: null,
              profile: null,
              isEmailConfirmed: false,
              isPasswordRecovery: false,
              loading: false,
              authMode: 'forgot',
              authNotice: 'This password reset link has expired or was superseded by a newer request. Please request a fresh link below.'
            });
            return;
          }
          if (data.session) {
            if (data.session.user?.id && typeof window !== 'undefined') {
              try {
                localStorage.setItem('sv_recovery_session_active', data.session.user.id);
              } catch {}
            }
            cleanAuthUrlParams();
            set({
              user: data.session.user,
              session: data.session,
              isEmailConfirmed: true,
              isPasswordRecovery: true,
              loading: false,
              authNotice: null
            });
            return;
          }
        } catch (err) {
          console.error('Exchange code error:', err);
        }
      }
    }

    // 4. Cryptographic Proof #1: URL hash contains BOTH access_token= AND type=recovery
    const hasHashRecovery = typeof window !== 'undefined' && (
      (window.location.hash.includes('access_token=') || new URLSearchParams(window.location.hash.replace(/^#/, '')).has('access_token')) &&
      (window.location.hash.includes('type=recovery') || new URLSearchParams(window.location.hash.replace(/^#/, '')).get('type') === 'recovery')
    );

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) {
        const user = session.user;
        const isConfirmed = !!user.email_confirmed_at;
        const isStoredRecovery = typeof window !== 'undefined' && localStorage.getItem('sv_recovery_session_active') === user.id;

        // ONLY enter password recovery if cryptographic hash recovery is explicitly present or stored recovery marker is active
        if (hasHashRecovery || isStoredRecovery) {
          if (typeof window !== 'undefined') {
            try {
              localStorage.setItem('sv_recovery_session_active', user.id);
            } catch {}
          }
          cleanAuthUrlParams();
          set({
            user,
            session,
            isEmailConfirmed: isConfirmed,
            isPasswordRecovery: true
          });
          return;
        }

        set({
          user,
          session,
          isEmailConfirmed: isConfirmed,
          isPasswordRecovery: false
        });

        if (isConfirmed) {
          await get().refreshProfile();
          if (session?.access_token) {
            startTelemetrySession(session.access_token, user.email);
          }
        }
      }
    } catch (e) {
      console.error('Auth initialization error:', e);
    } finally {
      set({ loading: false });
    }
  },

  refreshProfile: async () => {
    const { user } = get();
    if (!user) return;
    try {
      const { data, error } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', user.id)
        .single();
      if (data && !error) {
        set({ profile: data as Profile });
      } else if (error && error.code === 'PGRST116') {
        const newProf = {
          id: user.id,
          email: user.email!,
          display_name: user.user_metadata?.full_name || user.email?.split('@')[0],
          avatar_url: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'
        };
        await supabase.from('profiles').insert(newProf);
        set({ profile: newProf as any });
      }
    } catch (e) {
      console.error('Failed to load profile:', e);
    }
  },

  signIn: async (email, password) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password
    });
    if (error) {
      return { error: error.message };
    }
    if (typeof window !== 'undefined') {
      try {
        localStorage.removeItem('sv_recovery_session_active');
      } catch {}
    }
    const isConfirmed = !!data.user?.email_confirmed_at;
    cleanAuthUrlParams();
    set({
      user: data.user,
      session: data.session,
      isEmailConfirmed: isConfirmed,
      isPasswordRecovery: false,
      authNotice: null
    });
    if (isConfirmed && data.user) {
      await get().refreshProfile();
      if (data.session?.access_token) {
        startTelemetrySession(data.session.access_token, data.user.email);
      }
    }
    return { error: null };
  },

  signUp: async (email, password, fullName) => {
    const cleanEmail = email.trim().toLowerCase();
    const redirectTo = await getReachableRedirectUrl('/home');
    const { data, error } = await supabase.auth.signUp({
      email: cleanEmail,
      password,
      options: {
        emailRedirectTo: redirectTo,
        data: {
          full_name: fullName,
          avatar_url: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'
        }
      }
    });
    if (error) {
      return { error: error.message, emailConfirmed: false };
    }
    const isConfirmed = !!data.user?.email_confirmed_at;
    set({
      user: data.user,
      session: data.session,
      isEmailConfirmed: isConfirmed,
      isPasswordRecovery: false,
      authNotice: null,
      pendingVerificationEmail: isConfirmed ? null : cleanEmail,
      pendingSignUpPassword: isConfirmed ? null : password
    });
    return { error: null, emailConfirmed: isConfirmed };
  },

  signOut: async () => {
    const { session } = get();
    if (session?.access_token) {
      try {
        await sendTelemetryLogout(session.access_token, getCachedSessionLogId());
      } catch (err) {
        console.warn('Sign-out telemetry warning:', err);
      }
    }
    await supabase.auth.signOut().catch(() => {});
    cleanAuthUrlParams();
    clearClientUserState();
    set({
      user: null,
      session: null,
      profile: null,
      isEmailConfirmed: false,
      isPasswordRecovery: false,
      pendingVerificationEmail: null,
      pendingSignUpPassword: null,
      authModalSuccessNotice: null,
      serverCooldownSeconds: 0,
      verificationNotice: null,
      authNotice: null
    });
  },

  resendVerification: async (email: string) => {
    try {
      await api.resendVerification(email);
      return { error: null };
    } catch (err: any) {
      return { error: err.message || 'Failed to resend verification email' };
    }
  },

  updateAvatar: async (avatarUrl: string) => {
    const { user, profile } = get();
    if (!user) return;
    await supabase.from('profiles').update({ avatar_url: avatarUrl }).eq('id', user.id);
    if (profile) {
      set({ profile: { ...profile, avatar_url: avatarUrl } });
    }
  },

  updatePassword: async (newPassword: string) => {
    try {
      const { error } = await supabase.auth.updateUser({
        password: newPassword
      });
      if (error) {
        return { error: error.message };
      }

      // Notify backend BEFORE signOut so the active recovery session JWT authorizes completion
      const currentSession = get().session || (await supabase.auth.getSession()).data.session;
      const activeEmail = (get().user?.email || currentSession?.user?.email || '').trim().toLowerCase();
      if (currentSession?.access_token) {
        await api.notifyPasswordResetComplete(activeEmail, currentSession.access_token).catch(() => {});
      }

      // Successfully updated password!
      // Sign out completely to force clean re-login with new password as requested
      try {
        await supabase.auth.signOut();
      } catch {}

      // Clean URL params and hash completely
      cleanAuthUrlParams();
      clearClientUserState();

      set({
        user: null,
        session: null,
        profile: null,
        isPasswordRecovery: false,
        isEmailConfirmed: false,
        authMode: 'login',
        verifiedEmailForLogin: activeEmail || null,
        authModalSuccessNotice: '✓ Password updated successfully! Please sign in with your new password.',
        authNotice: '✓ Password updated successfully! Please sign in with your new password.'
      });
      return { error: null };
    } catch (e: any) {
      return { error: e.message || 'Failed to update password' };
    }
  },

  cancelPasswordRecovery: async () => {
    try {
      await supabase.auth.signOut();
    } catch {}
    cleanAuthUrlParams();
    clearClientUserState();
    set({
      user: null,
      session: null,
      profile: null,
      isPasswordRecovery: false,
      isEmailConfirmed: false
    });
  }
}));

if (typeof window !== 'undefined') {
  (window as any).__useAuthStore = useAuthStore;
}
