import React, { useState, useEffect, useRef } from 'react';
import { Search, ChevronDown, User, LogOut, Check, Home, KeyRound } from 'lucide-react';
import { useAuthStore } from '../../store/useAuthStore';
import { useUIStore } from '../../store/useUIStore';
import { ChangePasswordModal } from '../auth/ChangePasswordModal';

export const Navbar: React.FC = () => {
  const { user, profile, signOut } = useAuthStore();
  const { activeTab, setActiveTab, selectedGenre, setSelectedGenre } = useUIStore();

  const [isScrolled, setIsScrolled] = useState(false);
  const [showGenreMenu, setShowGenreMenu] = useState(false);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [showChangePasswordModal, setShowChangePasswordModal] = useState(false);

  const genreMenuRef = useRef<HTMLDivElement | null>(null);
  const profileMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 40);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Close dropdowns on outside click or tab switch
  useEffect(() => {
    setShowGenreMenu(false);
    setShowProfileMenu(false);
  }, [activeTab]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (genreMenuRef.current && !genreMenuRef.current.contains(e.target as Node)) {
        setShowGenreMenu(false);
      }
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target as Node)) {
        setShowProfileMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const genres = [
    'All Genres',
    'Action & Adventure',
    'Sci-Fi & Fantasy',
    'Anime & Animation',
    'Crime & Thrillers',
    'Comedy & Standup',
    'Romance & Drama',
    'Horror & Mystery',
    'Documentary'
  ];

  const navigateToTab = (tab: 'home' | 'search' | 'profile') => {
    setSelectedGenre(null);
    setActiveTab(tab);
  };

  const handleSelectGenre = (g: string) => {
    const selected = g === 'All Genres' ? null : g;
    setActiveTab('home');
    setSelectedGenre(selected);
    setShowGenreMenu(false);
  };

  return (
    <>
      {/* Top Navbar */}
      <nav
        className={`fixed top-0 left-0 right-0 z-40 transition-colors duration-300 ${
          isScrolled ? 'bg-black/95 backdrop-blur-md shadow-2xl' : 'bg-gradient-to-b from-black/85 via-black/40 to-transparent'
        }`}
      >
        <div className="max-w-7xl mx-auto px-4 sm:px-8 py-3.5 flex items-center justify-between">
          {/* Left: Brand + Essential Links */}
          <div className="flex items-center gap-8">
            <button
              onClick={() => navigateToTab('home')}
              className="text-red-600 font-black tracking-widest text-2xl font-mono hover:opacity-90 transition flex-shrink-0 cursor-pointer"
            >
              STREAMVAULT
            </button>

            {/* Desktop Navigation Links */}
            <div className="hidden md:flex items-center gap-6 text-sm font-medium">
              <button
                onClick={() => navigateToTab('home')}
                className={`transition cursor-pointer ${activeTab === 'home' && !selectedGenre ? 'text-white font-bold' : 'text-neutral-400 hover:text-neutral-200'}`}
              >
                Home
              </button>

              {/* Genre Filter Dropdown */}
              <div className="relative" ref={genreMenuRef}>
                <button
                  onClick={() => setShowGenreMenu(!showGenreMenu)}
                  className={`flex items-center gap-1.5 transition cursor-pointer px-2.5 py-1 rounded-md ${
                    selectedGenre 
                      ? 'bg-red-600/20 text-red-500 font-bold border border-red-500/30' 
                      : 'text-neutral-400 hover:text-white hover:bg-white/5'
                  }`}
                >
                  <span>{selectedGenre || 'Genres'}</span>
                  <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showGenreMenu ? 'rotate-180' : ''}`} />
                </button>

                {showGenreMenu && (
                  <div className="absolute top-10 left-0 w-56 bg-neutral-900 border border-neutral-800 rounded-2xl p-2 shadow-2xl backdrop-blur-xl z-50 animate-fade-in">
                    <div className="px-3 py-1.5 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider border-b border-neutral-800/60 mb-1">
                      Browse Genres
                    </div>
                    {genres.map((g) => {
                      const isSelected = (selectedGenre === g || (g === 'All Genres' && !selectedGenre));
                      return (
                        <button
                          key={g}
                          onClick={() => handleSelectGenre(g)}
                          className={`w-full text-left px-3 py-2 rounded-xl text-xs transition cursor-pointer flex items-center justify-between group ${
                            isSelected
                              ? 'bg-red-600 text-white font-bold shadow-md'
                              : 'text-neutral-300 hover:bg-neutral-800 hover:text-white'
                          }`}
                        >
                          <span>{g}</span>
                          {isSelected && <Check className="w-3.5 h-3.5 text-white" />}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Right: Search Icon Button & Profile Avatar Button */}
          <div className="flex items-center gap-4">
            {/* Search Icon (Hidden on mobile where floating bottom bar provides Search) */}
            <button
              onClick={() => navigateToTab('search')}
              className={`hidden md:block p-2 rounded-full transition cursor-pointer ${
                activeTab === 'search' 
                  ? 'bg-red-600/20 text-red-500' 
                  : 'text-neutral-300 hover:text-white hover:bg-white/10'
              }`}
              title="Search Catalog"
              aria-label="Search"
            >
              <Search className="w-5 h-5" />
            </button>

            {/* Profile Dropdown (Hidden on mobile where floating bottom bar provides Profile access) */}
            <div className="relative hidden md:block" ref={profileMenuRef}>
              <button
                onClick={() => setShowProfileMenu(!showProfileMenu)}
                className="flex items-center gap-2 group p-1 rounded-lg hover:bg-white/10 transition cursor-pointer"
                aria-label="Profile Menu"
              >
                <img
                  src={profile?.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'}
                  alt="Profile Avatar"
                  className="w-8 h-8 rounded-md object-cover border border-neutral-700 group-hover:border-red-500 transition"
                />
                <ChevronDown className={`w-3.5 h-3.5 text-neutral-400 group-hover:text-white transition hidden sm:block ${showProfileMenu ? 'rotate-180' : ''}`} />
              </button>

              {showProfileMenu && (
                <div className="absolute right-0 top-11 w-60 bg-neutral-900 border border-neutral-800 rounded-2xl p-2 shadow-2xl backdrop-blur-xl z-50 animate-fade-in">
                  <div className="px-3 py-2.5 border-b border-neutral-800 mb-1">
                    <p className="text-white text-xs font-bold line-clamp-1">
                      {profile?.display_name || user?.email?.split('@')[0]}
                    </p>
                    <p className="text-neutral-500 text-[11px] truncate">{user?.email}</p>
                  </div>

                  <button
                    onClick={() => { navigateToTab('profile'); setShowProfileMenu(false); }}
                    className="w-full text-left px-3 py-2 rounded-xl text-xs text-neutral-300 hover:bg-neutral-800 hover:text-white transition flex items-center gap-2.5 cursor-pointer"
                  >
                    <User className="w-4 h-4 text-neutral-400" />
                    <span>My Profile & Lists</span>
                  </button>

                  <button
                    onClick={() => { setShowChangePasswordModal(true); setShowProfileMenu(false); }}
                    className="w-full text-left px-3 py-2 rounded-xl text-xs text-neutral-300 hover:bg-neutral-800 hover:text-white transition flex items-center gap-2.5 cursor-pointer"
                  >
                    <KeyRound className="w-4 h-4 text-neutral-400" />
                    <span>Change Password</span>
                  </button>

                  <button
                    onClick={() => { signOut(); setShowProfileMenu(false); }}
                    className="w-full text-left px-3 py-2 rounded-xl text-xs text-red-400 hover:bg-red-600/15 transition flex items-center gap-2.5 mt-1 cursor-pointer"
                  >
                    <LogOut className="w-4 h-4 text-red-500" />
                    <span>Sign Out</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </nav>

      {/* Floating Translucent Pill Bottom Navigation Bar (Mobile Only - 3 Tabs: Home, Search, Profile) */}
      <div className="md:hidden fixed bottom-5 left-1/2 -translate-x-1/2 z-40 bg-[#161616]/90 backdrop-blur-xl border border-neutral-800 rounded-full px-6 py-2.5 shadow-2xl flex items-center gap-8">
        <button
          onClick={() => navigateToTab('home')}
          className={`flex flex-col items-center gap-1 transition ${
            activeTab === 'home' ? 'text-red-500 font-bold' : 'text-neutral-400 hover:text-white'
          }`}
        >
          <Home className="w-5 h-5" />
          <span className="text-[10px]">Home</span>
        </button>

        <button
          onClick={() => navigateToTab('search')}
          className={`flex flex-col items-center gap-1 transition ${
            activeTab === 'search' ? 'text-red-500 font-bold' : 'text-neutral-400 hover:text-white'
          }`}
        >
          <Search className="w-5 h-5" />
          <span className="text-[10px]">Search</span>
        </button>

        <button
          onClick={() => navigateToTab('profile')}
          className={`flex flex-col items-center gap-1 transition ${
            activeTab === 'profile' ? 'text-red-500 font-bold' : 'text-neutral-400 hover:text-white'
          }`}
        >
          <img
            src={profile?.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'}
            alt="Profile Avatar"
            className={`w-5 h-5 rounded-full object-cover ${activeTab === 'profile' ? 'ring-2 ring-red-500' : ''}`}
          />
          <span className="text-[10px]">Profile</span>
        </button>
      </div>

      <ChangePasswordModal
        isOpen={showChangePasswordModal}
        onClose={() => setShowChangePasswordModal(false)}
      />
    </>
  );
};
