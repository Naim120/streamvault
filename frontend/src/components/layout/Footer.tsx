import React from 'react';

interface FooterProps {
  onNavigateLegal?: (slug: 'terms' | 'privacy' | 'dmca' | 'cookies') => void;
}

export const Footer: React.FC<FooterProps> = ({ onNavigateLegal }) => {
  const handleLegalClick = (e: React.MouseEvent, slug: 'terms' | 'privacy' | 'dmca' | 'cookies') => {
    e.preventDefault();
    if (onNavigateLegal) {
      onNavigateLegal(slug);
    } else {
      window.history.pushState({}, '', `/legal/${slug}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  return (
    <footer className="w-full border-t border-neutral-900 bg-black/95 text-neutral-400 py-12 px-6 sm:px-12 mt-auto">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6 text-xs">
        {/* Brand & Status */}
        <div className="flex flex-col sm:flex-row items-center gap-4 text-center sm:text-left">
          <span className="font-mono font-black text-red-600 tracking-widest text-lg">
            STREAMVAULT
          </span>
          <div className="flex items-center gap-2 bg-neutral-900/80 border border-neutral-800 px-3 py-1 rounded-full">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[11px] font-medium text-neutral-300">All Systems Operational</span>
          </div>
        </div>

        {/* Legal Links */}
        <div className="flex flex-wrap items-center justify-center gap-6 font-medium text-neutral-400">
          <a
            href="/legal/terms"
            onClick={(e) => handleLegalClick(e, 'terms')}
            className="hover:text-white transition"
          >
            Terms of Service
          </a>
          <a
            href="/legal/privacy"
            onClick={(e) => handleLegalClick(e, 'privacy')}
            className="hover:text-white transition"
          >
            Privacy Policy
          </a>
          <a
            href="/legal/dmca"
            onClick={(e) => handleLegalClick(e, 'dmca')}
            className="hover:text-white transition"
          >
            DMCA & Copyright
          </a>
          <a
            href="/legal/cookies"
            onClick={(e) => handleLegalClick(e, 'cookies')}
            className="hover:text-white transition"
          >
            Cookie & Telemetry Policy
          </a>
        </div>

        {/* Copyright */}
        <div className="text-neutral-500 text-center md:text-right">
          © 2026 StreamVault Media Engine. All rights reserved.
        </div>
      </div>
    </footer>
  );
};

