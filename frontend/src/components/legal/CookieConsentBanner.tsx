import React, { useState, useEffect } from 'react';
import { ShieldCheck, X } from 'lucide-react';

interface CookieConsentBannerProps {
  onNavigateLegal?: (slug: 'terms' | 'privacy' | 'dmca' | 'cookies') => void;
}

export const CookieConsentBanner: React.FC<CookieConsentBannerProps> = ({ onNavigateLegal }) => {
  const [isVisible, setIsVisible] = useState(false);
  const [isDismissing, setIsDismissing] = useState(false);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const consent = localStorage.getItem('sv_cookie_consent');
      if (!consent) {
        // Small delay to allow initial page layout to settle
        const timer = setTimeout(() => setIsVisible(true), 800);
        return () => clearTimeout(timer);
      }
    }
  }, []);

  const handleConsent = (choice: 'accepted' | 'essential_only') => {
    setIsDismissing(true);
    setTimeout(() => {
      localStorage.setItem('sv_cookie_consent', choice);
      setIsVisible(false);
      setIsDismissing(false);
    }, 300);
  };

  const handleLegalClick = (e: React.MouseEvent, slug: 'privacy' | 'cookies') => {
    e.preventDefault();
    if (onNavigateLegal) {
      onNavigateLegal(slug);
    } else {
      window.history.pushState({}, '', `/legal/${slug}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  if (!isVisible) return null;

  return (
    <div
      className={`fixed bottom-4 left-4 right-4 sm:left-auto sm:right-6 sm:max-w-md z-[9999] transition-all duration-300 transform ${
        isDismissing ? 'opacity-0 translate-y-4 scale-95' : 'opacity-100 translate-y-0 scale-100 animate-fade-in'
      }`}
    >
      <div className="bg-[#141414]/95 backdrop-blur-xl border border-neutral-800 rounded-2xl p-5 shadow-2xl text-white shadow-black/80">
        <div className="flex items-start gap-3">
          <div className="p-2 bg-red-600/10 border border-red-600/20 rounded-xl text-red-500 shrink-0 mt-0.5">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div className="flex-1 text-xs text-neutral-300 leading-relaxed">
            <p className="font-semibold text-white text-sm mb-1">Cookie & Privacy Notice</p>
            <p>
              We use essential authentication cookies and first-party playback telemetry to deliver a seamless streaming experience and secure your account. Read our{' '}
              <a
                href="/legal/privacy"
                onClick={(e) => handleLegalClick(e, 'privacy')}
                className="text-red-400 hover:text-red-300 underline font-medium"
              >
                Privacy Policy
              </a>{' '}
              and{' '}
              <a
                href="/legal/cookies"
                onClick={(e) => handleLegalClick(e, 'cookies')}
                className="text-red-400 hover:text-red-300 underline font-medium"
              >
                Cookie Policy
              </a>
              .
            </p>
          </div>
          <button
            onClick={() => handleConsent('essential_only')}
            className="text-neutral-500 hover:text-white transition p-1 -mr-1 -mt-1 cursor-pointer"
            aria-label="Dismiss cookie notice"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex items-center justify-end gap-2.5 mt-4 pt-3 border-t border-neutral-800/80">
          <button
            onClick={() => handleConsent('essential_only')}
            className="px-3.5 py-2 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white bg-neutral-900 hover:bg-neutral-800 border border-neutral-700/60 transition cursor-pointer"
          >
            Essential Only
          </button>
          <button
            onClick={() => handleConsent('accepted')}
            className="px-4 py-2 rounded-xl text-xs font-bold text-white bg-red-600 hover:bg-red-700 active:scale-95 transition shadow-lg shadow-red-600/30 cursor-pointer"
          >
            Accept All
          </button>
        </div>
      </div>
    </div>
  );
};

