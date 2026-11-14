import React, { useState, useEffect } from 'react';
import { 
  ChevronRight, Play, Shield, Zap, Globe, Smartphone, Volume2, 
  ChevronDown, Film, Tv, Star, Check 
} from 'lucide-react';
import { Footer } from '../layout/Footer';
import { MediaImage } from '../common/MediaImage';
import { AuthModal } from '../auth/AuthModal';
import { updatePageSeo } from '../../lib/seo';
import { api } from '../../lib/api';
import { useAuthStore } from '../../store/useAuthStore';

interface PublicShowcaseItem {
  id: string;
  title: string;
  poster_url?: string;
  backdrop_url?: string;
  release_year?: number;
  maturity_rating?: string;
  type?: string;
  genres?: string[];
}

interface LandingPageProps {
  onNavigateLegal?: (slug: 'terms' | 'privacy' | 'dmca' | 'cookies') => void;
  initialAuthMode?: 'login' | 'register' | null;
  initialAuthNotice?: string | null;
}

export const LandingPage: React.FC<LandingPageProps> = ({
  onNavigateLegal,
  initialAuthMode = null,
  initialAuthNotice = null,
}) => {
  const [emailInput, setEmailInput] = useState('');
  const [isAuthOpen, setIsAuthOpen] = useState(!!initialAuthMode);
  const [authModalMode, setAuthModalMode] = useState<'login' | 'register' | 'forgot'>(
    initialAuthMode || 'login'
  );
  const [authTitleNotice, setAuthTitleNotice] = useState<string | null>(initialAuthNotice);
  const [showcaseItems, setShowcaseItems] = useState<PublicShowcaseItem[]>([]);
  const [openFaqIndex, setOpenFaqIndex] = useState<number | null>(null);

  const {
    authModalSuccessNotice,
    setAuthModalSuccessNotice,
    verifiedEmailForLogin,
    setVerifiedEmailForLogin,
    authMode,
    authNotice,
  } = useAuthStore();

  useEffect(() => {
    if (authModalSuccessNotice) {
      if (verifiedEmailForLogin) {
        setEmailInput(verifiedEmailForLogin);
      }
      setAuthModalMode('login');
      setAuthTitleNotice(authModalSuccessNotice);
      setIsAuthOpen(true);
    }
  }, [authModalSuccessNotice, verifiedEmailForLogin]);

  useEffect(() => {
    if (authMode === 'forgot' && authNotice) {
      setAuthModalMode('forgot');
      setIsAuthOpen(true);
    }
  }, [authMode, authNotice]);

  useEffect(() => {
    updatePageSeo({
      title: 'StreamVault — Watch Movies, TV Series & Originals in HD',
      description:
        'StreamVault is a high-performance streaming platform featuring adaptive 1080p HD playback, multi-language audio tracks, custom subtitles, and instant cross-device sync.',
      canonicalPath: '/',
      noIndex: false,
    });
  }, []);

  useEffect(() => {
    if (initialAuthMode) {
      setAuthModalMode(initialAuthMode);
      setIsAuthOpen(true);
      if (initialAuthNotice) {
        setAuthTitleNotice(initialAuthNotice);
      }
    }
  }, [initialAuthMode, initialAuthNotice]);

  useEffect(() => {
    let isMounted = true;
    const fetchShowcase = async () => {
      try {
        const res = await fetch('/api/metadata/public-showcase');
        if (res.ok) {
          const data = await res.json();
          if (isMounted && Array.isArray(data)) {
            setShowcaseItems(data);
          }
        }
      } catch (err) {
        console.warn('Could not fetch public showcase:', err);
      }
    };
    fetchShowcase();
    return () => {
      isMounted = false;
    };
  }, []);

  const openAuth = (mode: 'login' | 'register', titleNotice?: string | null) => {
    setAuthModalMode(mode);
    setAuthTitleNotice(titleNotice || null);
    setIsAuthOpen(true);
  };

  const handleHeroSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanEmail = emailInput.trim().toLowerCase();
    if (!cleanEmail) {
      openAuth('register');
      return;
    }
    try {
      const precheck = await api.signupPrecheck(cleanEmail);
      if (precheck.status === 'already_verified') {
        openAuth('login', 'This email is already registered and verified. Please sign in to your account.');
        return;
      }
      if (
        precheck.status === 'unverified_expired_resent' ||
        precheck.status === 'unverified_existing' ||
        precheck.status === 'unverified_cooldown'
      ) {
        useAuthStore.getState().setPendingVerification(
          cleanEmail,
          precheck.cooldown_remaining_seconds,
          precheck.message || 'Please check your email for the verification link.'
        );
        return;
      }
    } catch (err) {
      console.warn('Hero precheck warning:', err);
    }
    openAuth('register');
  };

  const handlePosterClick = (item: PublicShowcaseItem) => {
    sessionStorage.setItem('sv_post_login_redirect', `/home/${item.id}`);
    openAuth('login', `Sign in to watch "${item.title}"`);
  };

  const faqs = [
    {
      q: 'What is StreamVault?',
      a: 'StreamVault is a next-generation, high-performance streaming platform. It delivers cinema-grade 1080p HD video with hardware-accelerated HLS streaming, multi-track audio, synchronized WebVTT subtitles, and instant cross-device playback resumption.',
    },
    {
      q: 'Which devices are supported?',
      a: 'StreamVault works seamlessly in all modern desktop, tablet, and mobile browsers (Chrome, Safari, Firefox, Edge). With responsive layouts and native touch controls, you can enjoy a unified streaming experience anywhere without installing third-party plugins.',
    },
    {
      q: 'How does multi-language audio & subtitle switching work?',
      a: 'Titles encoded in the StreamVault library support multi-track audio switching (e.g., Original English, Hindi, Japanese) and selectable WebVTT subtitles on the fly directly inside the custom video player interface.',
    },
    {
      q: 'Can I create custom watchlists and resume playback?',
      a: 'Yes. Every verified account can curate personal custom watchlists and track watch history. If you pause a movie or episode on your computer, you can pick up at the exact second on your phone.',
    },
    {
      q: 'How is my data and privacy protected?',
      a: 'StreamVault is engineered with a strict privacy-first philosophy. We operate with zero third-party advertising trackers, enforce database-level Row Level Security (RLS), and never monetize or broker your personal information.',
    },
    {
      q: 'How do I get started?',
      a: 'Simply enter your email above or click "Get Started" to create your free account in seconds. Once your email is verified, you will immediately unlock access to our full library.',
    },
  ];

  return (
    <div className="min-h-screen bg-black text-white flex flex-col selection:bg-red-600 selection:text-white">
      {/* 1. Sticky Glassmorphic Top Navbar */}
      <header className="sticky top-0 z-40 bg-black/85 backdrop-blur-xl border-b border-neutral-800/80 px-4 sm:px-10 py-4 flex items-center justify-between transition-all">
        <div className="flex items-center gap-3">
          <span className="font-mono font-black text-red-600 tracking-widest text-2xl sm:text-3xl">
            STREAMVAULT
          </span>
        </div>

        <div className="flex items-center gap-3 sm:gap-4">
          <div className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-neutral-900/80 border border-neutral-800 text-xs text-neutral-300">
            <Globe className="w-3.5 h-3.5 text-neutral-400" />
            <span>English</span>
          </div>

          <button
            onClick={() => openAuth('login')}
            className="px-4 py-2 text-xs sm:text-sm font-semibold text-neutral-200 hover:text-white transition cursor-pointer"
          >
            Sign In
          </button>

          <button
            onClick={() => openAuth('register')}
            className="px-4 sm:px-5 py-2 text-xs sm:text-sm font-bold bg-red-600 hover:bg-red-700 active:scale-95 text-white rounded-xl transition shadow-lg shadow-red-600/30 cursor-pointer"
          >
            Get Started
          </button>
        </div>
      </header>

      {/* 2. Cinematic Hero Section */}
      <section className="relative min-h-[85vh] flex items-center justify-center px-4 sm:px-8 py-20 overflow-hidden text-center">
        {/* Background Radial & Vignette Glow */}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-red-950/30 via-black to-black pointer-events-none" />
        <div 
          className="absolute inset-0 opacity-15 bg-cover bg-center pointer-events-none blur-xs scale-105"
          style={{
            backgroundImage: `url('https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?w=1600&auto=format&fit=crop&q=80')`
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black via-black/80 to-transparent pointer-events-none" />

        {/* Hero Content */}
        <div className="relative z-10 max-w-4xl mx-auto space-y-8 animate-fade-in">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-red-950/50 border border-red-800/60 text-red-400 text-xs sm:text-sm font-semibold backdrop-blur-md">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span>Next-Generation OTT Streaming Media Engine</span>
          </div>

          <h1 className="text-4xl sm:text-6xl md:text-7xl font-black tracking-tight text-white leading-tight">
            Unlimited Cinema, Series & Originals.{' '}
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-red-500 to-red-700">
              Zero Compromises.
            </span>
          </h1>

          <p className="text-neutral-300 text-base sm:text-xl max-w-2xl mx-auto leading-relaxed font-normal">
            Experience adaptive 1080p HD streaming, multi-language original audio, instant resume across devices, and curated personal lists.
          </p>

          {/* Email Quick-Start Bar */}
          <form
            onSubmit={handleHeroSubmit}
            className="flex flex-col sm:flex-row items-center justify-center gap-3 max-w-xl mx-auto pt-4"
          >
            <input
              id="hero-email"
              name="email"
              type="email"
              autoComplete="email"
              aria-label="Email address"
              required
              value={emailInput}
              onChange={(e) => setEmailInput(e.target.value)}
              placeholder="Enter your email to start watching..."
              className="w-full sm:flex-1 px-5 py-4 bg-neutral-900/90 border border-neutral-700/80 rounded-2xl text-white placeholder-neutral-500 text-sm sm:text-base focus:outline-none focus:border-red-500 focus:ring-2 focus:ring-red-500/20 backdrop-blur-md transition"
            />
            <button
              type="submit"
              className="w-full sm:w-auto px-8 py-4 bg-red-600 hover:bg-red-700 active:scale-95 text-white font-bold text-sm sm:text-base rounded-2xl transition duration-200 shadow-xl shadow-red-600/40 flex items-center justify-center gap-2 shrink-0 cursor-pointer"
            >
              <span>Get Started</span>
              <ChevronRight className="w-5 h-5" />
            </button>
          </form>

          <p className="text-xs text-neutral-500">
            Ready to watch? Enter your email to create your account. Cancel anytime.
          </p>
        </div>
      </section>

      {/* 3. Trending Preview Showcase Strip (Public Teaser) */}
      {showcaseItems.length > 0 && (
        <section className="max-w-7xl w-full mx-auto px-4 sm:px-8 py-12">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-2xl sm:text-3xl font-black text-white flex items-center gap-3">
                <span className="w-1.5 h-6 bg-red-600 rounded-full" />
                <span>Trending on StreamVault</span>
              </h2>
              <p className="text-neutral-400 text-xs sm:text-sm mt-1">
                Top picks streaming right now in crystal clear 1080p
              </p>
            </div>
            <button
              onClick={() => openAuth('register')}
              className="text-xs font-bold text-red-500 hover:text-red-400 transition hidden sm:inline"
            >
              Explore Full Catalog ›
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-4">
            {showcaseItems.map((item, idx) => (
              <div
                key={item.id}
                onClick={() => handlePosterClick(item)}
                className="group relative rounded-2xl overflow-hidden bg-neutral-900 border border-neutral-800/80 hover:border-red-600/60 transition-all duration-300 hover:scale-[1.03] shadow-lg hover:shadow-2xl hover:shadow-red-600/20 cursor-pointer flex flex-col"
              >
                {/* Number Badge */}
                <div className="absolute top-2 left-2 z-20 w-7 h-7 rounded-lg bg-black/80 backdrop-blur-md border border-neutral-700/80 flex items-center justify-center font-mono font-black text-xs text-red-500">
                  {idx + 1}
                </div>

                {/* Poster Image */}
                <div className="aspect-[2/3] w-full relative overflow-hidden bg-neutral-950">
                  <MediaImage
                    title={item.title}
                    posterUrl={item.poster_url}
                    backdropUrl={item.backdrop_url}
                    aspect="poster"
                    className="w-full h-full"
                    imgClassName="w-full h-full object-cover transition duration-300 group-hover:opacity-90"
                    fallbackYear={item.release_year}
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                    <div className="w-11 h-11 rounded-full bg-red-600 text-white flex items-center justify-center shadow-lg shadow-red-600/40">
                      <Play className="w-5 h-5 fill-white translate-x-0.5" />
                    </div>
                  </div>
                </div>

                {/* Card Meta */}
                <div className="p-3 bg-neutral-900/90 flex-1 flex flex-col justify-between">
                  <h3 className="font-bold text-white text-xs truncate group-hover:text-red-400 transition">
                    {item.title}
                  </h3>
                  <div className="flex items-center justify-between text-[10px] text-neutral-400 mt-1.5 font-medium">
                    <span>{item.release_year || 'HD'}</span>
                    <span className="px-1.5 py-0.5 bg-neutral-800 rounded text-[9px] text-neutral-300 uppercase">
                      {item.maturity_rating || 'U/A'}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 4. 4-Card Commercial Feature Bento Grid */}
      <section className="max-w-7xl w-full mx-auto px-4 sm:px-8 py-16">
        <div className="text-center max-w-2xl mx-auto mb-12">
          <h2 className="text-3xl sm:text-4xl font-black text-white">
            Engineered for Pure Cinematic Immersion
          </h2>
          <p className="text-neutral-400 text-sm sm:text-base mt-3">
            Built from the ground up on low-latency streaming infrastructure with zero bloat.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Card 1 */}
          <div className="bg-[#141414] border border-neutral-800 rounded-3xl p-8 hover:border-neutral-700 transition flex flex-col justify-between relative overflow-hidden group">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-2xl bg-red-600/10 border border-red-600/20 text-red-500 flex items-center justify-center">
                <Zap className="w-6 h-6" />
              </div>
              <h3 className="text-2xl font-bold text-white group-hover:text-red-400 transition">
                Adaptive Multi-Bitrate Streaming
              </h3>
              <p className="text-neutral-400 text-sm leading-relaxed">
                Smooth 1080p, 720p, and 480p HLS playback engineered for high-bandwidth fiber as well as mobile networks. Zero buffering pauses with dynamic bitrate transitions.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-neutral-800/80 flex items-center gap-3 text-xs text-neutral-400 font-mono">
              <span className="text-emerald-400">● 1080p Master</span>
              <span>•</span>
              <span>H.264 / AAC Clean Demux</span>
            </div>
          </div>

          {/* Card 2 */}
          <div className="bg-[#141414] border border-neutral-800 rounded-3xl p-8 hover:border-neutral-700 transition flex flex-col justify-between relative overflow-hidden group">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-2xl bg-red-600/10 border border-red-600/20 text-red-500 flex items-center justify-center">
                <Volume2 className="w-6 h-6" />
              </div>
              <h3 className="text-2xl font-bold text-white group-hover:text-red-400 transition">
                Multi-Audio & Custom Subtitles
              </h3>
              <p className="text-neutral-400 text-sm leading-relaxed">
                Switch seamlessly between Original English, Hindi, Japanese, and custom WebVTT subtitle tracks on the fly without interrupting video playback.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-neutral-800/80 flex items-center gap-3 text-xs text-neutral-400 font-mono">
              <span className="text-red-400">Multi-Channel</span>
              <span>•</span>
              <span>Synchronized WebVTT</span>
            </div>
          </div>

          {/* Card 3 */}
          <div className="bg-[#141414] border border-neutral-800 rounded-3xl p-8 hover:border-neutral-700 transition flex flex-col justify-between relative overflow-hidden group">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-2xl bg-red-600/10 border border-red-600/20 text-red-500 flex items-center justify-center">
                <Smartphone className="w-6 h-6" />
              </div>
              <h3 className="text-2xl font-bold text-white group-hover:text-red-400 transition">
                Seamless Cross-Device Sync
              </h3>
              <p className="text-neutral-400 text-sm leading-relaxed">
                Start streaming on your living room desktop and resume at the exact second on your smartphone or tablet with real-time watch progress synchronization.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-neutral-800/80 flex items-center gap-3 text-xs text-neutral-400 font-mono">
              <span className="text-emerald-400">Realtime Progress</span>
              <span>•</span>
              <span>Universal Web Access</span>
            </div>
          </div>

          {/* Card 4 */}
          <div className="bg-[#141414] border border-neutral-800 rounded-3xl p-8 hover:border-neutral-700 transition flex flex-col justify-between relative overflow-hidden group">
            <div className="space-y-4">
              <div className="w-12 h-12 rounded-2xl bg-red-600/10 border border-red-600/20 text-red-500 flex items-center justify-center">
                <Shield className="w-6 h-6" />
              </div>
              <h3 className="text-2xl font-bold text-white group-hover:text-red-400 transition">
                Privacy-First & Ad-Free Experience
              </h3>
              <p className="text-neutral-400 text-sm leading-relaxed">
                Enjoy your entertainment completely undisturbed. Zero third-party ad networks, encrypted bearer tokens, and row-level database protection for every account.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-neutral-800/80 flex items-center gap-3 text-xs text-neutral-400 font-mono">
              <span className="text-emerald-400">100% Ad-Free</span>
              <span>•</span>
              <span>GDPR & DPDP Compliant</span>
            </div>
          </div>
        </div>
      </section>

      {/* 5. Interactive FAQ Accordion (6 Common Questions) */}
      <section className="max-w-4xl w-full mx-auto px-4 sm:px-8 py-16">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-black text-white">
            Frequently Asked Questions
          </h2>
          <p className="text-neutral-400 text-sm mt-2">
            Everything you need to know about the StreamVault platform.
          </p>
        </div>

        <div className="space-y-3">
          {faqs.map((faq, idx) => {
            const isOpen = openFaqIndex === idx;
            return (
              <div
                key={idx}
                className="bg-[#141414] border border-neutral-800 rounded-2xl overflow-hidden transition"
              >
                <button
                  type="button"
                  onClick={() => setOpenFaqIndex(isOpen ? null : idx)}
                  className="w-full px-6 py-5 flex items-center justify-between text-left font-bold text-base sm:text-lg text-white hover:text-red-400 transition cursor-pointer"
                >
                  <span>{faq.q}</span>
                  <ChevronDown
                    className={`w-5 h-5 text-neutral-400 transition-transform duration-200 ${
                      isOpen ? 'transform rotate-180 text-red-500' : ''
                    }`}
                  />
                </button>
                {isOpen && (
                  <div className="px-6 pb-6 text-sm sm:text-base text-neutral-300 leading-relaxed border-t border-neutral-800/60 pt-4 animate-fade-in">
                    {faq.a}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Bottom CTA */}
        <div className="text-center mt-12 space-y-4">
          <p className="text-sm text-neutral-300">
            Ready to experience cinema the way it was intended?
          </p>
          <button
            onClick={() => openAuth('register')}
            className="px-8 py-4 bg-red-600 hover:bg-red-700 active:scale-95 text-white font-bold text-base rounded-2xl transition duration-200 shadow-xl shadow-red-600/40 inline-flex items-center gap-2 cursor-pointer"
          >
            <span>Create Your StreamVault Account</span>
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </section>

      {/* 6. Commercial Footer */}
      <Footer onNavigateLegal={onNavigateLegal} />

      {/* Auth Modal Triggered from Landing Page */}
      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => {
          setIsAuthOpen(false);
          setAuthTitleNotice(null);
          setAuthModalSuccessNotice(null);
          setVerifiedEmailForLogin(null);
        }}
        initialEmail={emailInput}
        initialMode={authModalMode}
        titleNotice={authTitleNotice}
        onModeChange={setAuthModalMode}
      />
    </div>
  );
};
