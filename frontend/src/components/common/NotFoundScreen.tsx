import React, { useEffect } from 'react';
import { useUIStore } from '../../store/useUIStore';
import { updatePageSeo } from '../../lib/seo';

interface NotFoundScreenProps {
  onReset?: () => void;
  message?: string;
  targetPath?: string;
}

export const NotFoundScreen: React.FC<NotFoundScreenProps> = ({ 
  onReset, 
  message, 
  targetPath = '/home' 
}) => {
  useEffect(() => {
    updatePageSeo({
      title: '404 - Page Not Found — StreamVault',
      description: 'The requested title or page could not be found on StreamVault.',
      noIndex: true,
    });
  }, []);

  const handleHomeClick = () => {
    window.history.pushState({}, '', targetPath);
    useUIStore.getState().setActiveTab('home', false);
    useUIStore.getState().setSelectedGenre(null, false);
    useUIStore.getState().closeDetail(false);
    if (onReset) {
      onReset();
    } else {
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  return (
    <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center p-6 relative overflow-hidden select-none">
      {/* Background radial gradient glow */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-red-950/20 via-black to-black pointer-events-none" />

      {/* Brand logo header */}
      <div className="absolute top-8 left-8 sm:left-12">
        <button
          onClick={handleHomeClick}
          className="text-red-600 font-black tracking-widest text-2xl sm:text-3xl font-mono hover:opacity-90 transition cursor-pointer"
        >
          STREAMVAULT
        </button>
      </div>

      {/* Main 404 Content */}
      <div className="max-w-xl w-full text-center space-y-6 relative z-10 px-4">
        <h1 className="text-4xl sm:text-6xl font-black tracking-tight text-white">
          Lost your way?
        </h1>

        <p className="text-neutral-300 text-base sm:text-lg leading-relaxed font-normal">
          {message || "Sorry, we can't find that title or page. You'll find lots to explore on the catalog."}
        </p>

        <div className="pt-2">
          <button
            onClick={handleHomeClick}
            className="px-8 py-3.5 bg-red-600 hover:bg-red-700 active:scale-95 text-white font-bold text-sm sm:text-base rounded-xl transition duration-200 shadow-xl shadow-red-600/30 cursor-pointer inline-flex items-center gap-2"
          >
            Return to /home
          </button>
        </div>

        <div className="pt-8 border-t border-neutral-800/80">
          <span className="font-mono text-xs tracking-wider text-neutral-500 uppercase">
            Error Code: <strong className="text-neutral-400">404</strong>
          </span>
        </div>
      </div>
    </div>
  );
};

