import React, { useState, useEffect, useMemo } from 'react';
import { 
  LayoutDashboard, Download, Film, Users, Cpu, HardDrive, Server, 
  RefreshCw, Plus, Edit3, Trash2, Pause, Play, CheckCircle2, 
  AlertTriangle, Search, ChevronRight, ChevronDown, Menu, X, ArrowUpRight, 
  Check, Eye, EyeOff, Sparkles, Filter, SlidersHorizontal, ExternalLink 
} from 'lucide-react';

const AVAILABLE_GENRES = [
  'Action', 'Adventure', 'Animation', 'Comedy', 'Crime', 'Documentary',
  'Drama', 'Family', 'Fantasy', 'History', 'Horror', 'Music',
  'Mystery', 'Romance', 'Sci-Fi', 'Thriller', 'War', 'Western'
];

const AVAILABLE_AUDIO = [
  'English', 'Hindi', 'Spanish', 'French', 'German', 'Japanese',
  'Korean', 'Italian', 'Portuguese', 'Mandarin', 'Chinese', 'Russian', 'Arabic',
  'Tamil', 'Telugu', 'Bengali', 'Indonesian', 'Romanian'
];
import { api } from '../../lib/api';
import { supabase } from '../../lib/supabase';
import { SystemCapabilities, SystemSettings, MediaItem } from '../../types';
import { useUIStore } from '../../store/useUIStore';

export const LocalAdminPanel: React.FC = () => {
  const { showToast } = useUIStore();
  const [activeTab, setActiveTab] = useState<'dashboard' | 'aria2' | 'media' | 'users'>('dashboard');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // 1. Hardware & System Telemetry
  const [capabilities, setCapabilities] = useState<SystemCapabilities | null>(null);
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [queueStatus, setQueueStatus] = useState<any>(null);
  const [loadingToggle, setLoadingToggle] = useState(false);

  // 2. Metrics
  const [stats, setStats] = useState({
    totalUsers: 0,
    totalVisits: 0,
    activeToday: 0,
    totalMedia: 0,
  });

  // 3. Aria2 Downloads
  const [aria2Stats, setAria2Stats] = useState<any>(null);
  const [aria2Downloads, setAria2Downloads] = useState<any[]>([]);
  const [downloadUri, setDownloadUri] = useState('');
  const [tmdbQuery, setTmdbQuery] = useState('');
  const [downloadType, setDownloadType] = useState<'movie' | 'series'>('movie');

  // 4. Media Library & Metadata Editor
  const [mediaList, setMediaList] = useState<MediaItem[]>([]);
  const [mediaSearch, setMediaSearch] = useState('');
  const [mediaFilterType, setMediaFilterType] = useState<'all' | 'movie' | 'series'>('all');
  const [mediaFilterGenre, setMediaFilterGenre] = useState<string>('all');
  const [mediaSortBy, setMediaSortBy] = useState<'recent' | 'title' | 'rating' | 'views'>('recent');

  // Metadata Edit Modal
  const [editingMedia, setEditingMedia] = useState<MediaItem | null>(null);
  const [isNewTitle, setIsNewTitle] = useState(false);
  const [editForm, setEditForm] = useState({
    title: '',
    slug: '',
    type: 'movie' as 'movie' | 'series',
    release_year: 2026,
    maturity_rating: 'U/A 16+',
    runtime_minutes: 120,
    synopsis: '',
    poster_url: '',
    backdrop_url: '',
    trailer_youtube_key: '',
    genres: ['Action', 'Sci-Fi'] as string[],
    audio_languages: ['English'] as string[],
    is_published: true,
  });
  const [genresDropdownOpen, setGenresDropdownOpen] = useState(false);
  const [audioDropdownOpen, setAudioDropdownOpen] = useState(false);
  const [testTrailerPreview, setTestTrailerPreview] = useState(false);

  // 5. Users Telemetry
  const [userLogs, setUserLogs] = useState<any[]>([]);
  const [userSearch, setUserSearch] = useState('');

  // Polling Loop for Hardware telemetry & Aria2
  useEffect(() => {
    let isMounted = true;
    const fetchTelemetry = async () => {
      try {
        const [caps, sett, qStat] = await Promise.all([
          api.getCapabilities().catch(() => null),
          api.getSettings().catch(() => null),
          api.getQueueStatus().catch(() => null),
        ]);
        if (isMounted) {
          if (caps) setCapabilities(caps);
          if (sett) setSettings(sett);
          if (qStat) setQueueStatus(qStat);
        }

        const [aStats, aDowns] = await Promise.all([
          api.getAria2Stats().catch(() => null),
          api.listAria2Downloads().catch(() => []),
        ]);
        if (isMounted) {
          setAria2Stats(aStats);
          setAria2Downloads(aDowns || []);
        }
      } catch {}
    };

    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 3500);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // Fetch Media Catalog, Metrics & Audit Logs
  const refreshCatalogAndStats = async () => {
    try {
      // 1. Fetch Media items
      const { data: media } = await supabase
        .from('media_items')
        .select('*')
        .order('created_at', { ascending: false });
      if (media) setMediaList(media as MediaItem[]);

      // 2. Fetch Profiles count
      const { count: userCount } = await supabase
        .from('profiles')
        .select('id', { count: 'exact', head: true });

      // 3. Fetch User login logs
      const { data: logs, count: logCount } = await supabase
        .from('user_login_logs')
        .select('*', { count: 'exact' })
        .order('logged_in_at', { ascending: false })
        .limit(50);
      if (logs) setUserLogs(logs);

      // 4. Calculate active today
      const today = new Date().toISOString().split('T')[0];
      const activeCount = logs?.filter(l => l.logged_in_at?.startsWith(today)).length || 1;

      setStats({
        totalUsers: userCount || 1,
        totalVisits: logCount || 1,
        activeToday: activeCount,
        totalMedia: media?.length || 0,
      });
    } catch {}
  };

  useEffect(() => {
    refreshCatalogAndStats();
  }, []);

  // Toggle Transcode Mode
  const handleToggleTranscode = async () => {
    if (!settings) return;
    setLoadingToggle(true);
    const newMode = settings.transcode_mode === 'abr_transcode' ? 'lossless_remux' : 'abr_transcode';
    try {
      const updated = await api.updateSettings({ transcode_mode: newMode });
      setSettings(updated);
      showToast(newMode === 'abr_transcode' ? 'Switched to Multi-Bitrate ABR' : 'Switched to Lossless Remuxing (Zero CPU)');
    } catch (e: any) {
      showToast('Failed to update engine mode');
    } finally {
      setLoadingToggle(false);
    }
  };

  // Dispatch Download to Aria2
  const handleDispatchDownload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!downloadUri.trim()) return;
    try {
      await api.addAria2Download(downloadUri.trim(), {
        title: tmdbQuery.trim() || 'Custom Ingest',
        type: downloadType,
      });
      setDownloadUri('');
      setTmdbQuery('');
      showToast('Download dispatched to Aria2c daemon');
      const downs = await api.listAria2Downloads();
      setAria2Downloads(downs);
    } catch (e: any) {
      showToast('Failed to dispatch download to Aria2');
    }
  };

  // Open Metadata Editor
  const openEditModal = (item?: MediaItem) => {
    if (item) {
      setIsNewTitle(false);
      setEditingMedia(item);
      setEditForm({
        title: item.title,
        slug: item.slug || item.title.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
        type: item.type,
        release_year: item.release_year || 2026,
        maturity_rating: item.maturity_rating || 'U/A 16+',
        runtime_minutes: item.runtime_minutes || 120,
        synopsis: item.synopsis || '',
        poster_url: item.poster_url || '',
        backdrop_url: item.backdrop_url || '',
        trailer_youtube_key: item.trailer_youtube_key || '',
        genres: item.genres && item.genres.length > 0 ? item.genres : ['Action', 'Drama'],
        audio_languages: item.audio_languages && item.audio_languages.length > 0 ? item.audio_languages : ['English'],
        is_published: item.is_published ?? true,
      });
    } else {
      setIsNewTitle(true);
      setEditingMedia(null);
      setEditForm({
        title: '',
        slug: '',
        type: 'movie',
        release_year: new Date().getFullYear(),
        maturity_rating: 'U/A 16+',
        runtime_minutes: 115,
        synopsis: '',
        poster_url: '',
        backdrop_url: '',
        trailer_youtube_key: '',
        genres: ['Action', 'Thriller'],
        audio_languages: ['English'],
        is_published: true,
      });
    }
    setTestTrailerPreview(false);
    setGenresDropdownOpen(false);
    setAudioDropdownOpen(false);
  };

  // Save Media Metadata to Supabase
  const handleSaveMetadata = async (e: React.FormEvent) => {
    e.preventDefault();

    const payload = {
      title: editForm.title.trim(),
      slug: editForm.slug.trim() || editForm.title.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      type: editForm.type,
      release_year: Number(editForm.release_year),
      maturity_rating: editForm.maturity_rating,
      runtime_minutes: Number(editForm.runtime_minutes),
      synopsis: editForm.synopsis.trim(),
      poster_url: editForm.poster_url.trim(),
      backdrop_url: editForm.backdrop_url.trim(),
      trailer_youtube_key: editForm.trailer_youtube_key.trim(),
      genres: editForm.genres.length > 0 ? editForm.genres : ['Action'],
      audio_languages: editForm.audio_languages.length > 0 ? editForm.audio_languages : ['English'],
      is_published: editForm.is_published,
    };

    try {
      if (isNewTitle) {
        const { error } = await supabase.from('media_items').insert(payload);
        if (error) throw error;
        showToast('New title added to catalog');
      } else if (editingMedia) {
        const { error } = await supabase
          .from('media_items')
          .update(payload)
          .eq('id', editingMedia.id);
        if (error) throw error;
        showToast(`Updated "${payload.title}" metadata`);
      }
      setEditingMedia(null);
      await refreshCatalogAndStats();
    } catch (err: any) {
      showToast('Error saving metadata: ' + (err.message || 'database error'));
    }
  };

  // Delete Media
  const handleDeleteMedia = async (id: string, title: string) => {
    if (!confirm(`Are you sure you want to delete "${title}"?`)) return;
    try {
      const { error } = await supabase.from('media_items').delete().eq('id', id);
      if (error) throw error;
      showToast(`Deleted "${title}"`);
      await refreshCatalogAndStats();
    } catch (e: any) {
      showToast('Failed to delete title');
    }
  };

  // Filter & Sort Media
  const filteredMedia = useMemo(() => {
    return mediaList.filter(m => {
      const matchesSearch = !mediaSearch.trim() || 
        m.title.toLowerCase().includes(mediaSearch.toLowerCase()) ||
        (m.synopsis && m.synopsis.toLowerCase().includes(mediaSearch.toLowerCase()));
      const matchesType = mediaFilterType === 'all' || m.type === mediaFilterType;
      const matchesGenre = mediaFilterGenre === 'all' || 
        (m.genres && m.genres.some(g => g.toLowerCase().includes(mediaFilterGenre.toLowerCase())));
      return matchesSearch && matchesType && matchesGenre;
    }).sort((a, b) => {
      if (mediaSortBy === 'title') return a.title.localeCompare(b.title);
      if (mediaSortBy === 'rating') return (b.critic_rating || 0) - (a.critic_rating || 0);
      if (mediaSortBy === 'views') return (b.views_count || 0) - (a.views_count || 0);
      return new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime();
    });
  }, [mediaList, mediaSearch, mediaFilterType, mediaFilterGenre, mediaSortBy]);

  return (
    <div className="h-[calc(100vh-4rem)] w-full max-w-full bg-[#0c0c0c] text-white flex overflow-hidden mt-16">
      {/* 1. Left Collapsible Sidebar */}
      <aside 
        className={`bg-[#121212] border-r border-neutral-800 transition-all duration-300 flex flex-col flex-shrink-0 h-full overflow-y-auto z-30 ${
          sidebarCollapsed ? 'w-16' : 'w-64'
        }`}
      >
        {/* Brand / Header */}
        <div className="p-4 border-b border-neutral-800/80 flex items-center justify-between">
          {!sidebarCollapsed && (
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 bg-red-600 text-white font-mono font-black text-[10px] rounded tracking-wider">
                ADMIN
              </span>
              <span className="font-bold text-sm tracking-wide text-white">StreamVault</span>
            </div>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-neutral-800 transition mx-auto cursor-pointer"
            title={sidebarCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
          >
            <Menu className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation Items */}
        <nav className="p-2 space-y-1 flex-1">
          <button
            onClick={() => setActiveTab('dashboard')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition cursor-pointer ${
              activeTab === 'dashboard'
                ? 'bg-red-600 text-white shadow-lg shadow-red-600/20'
                : 'text-neutral-400 hover:text-white hover:bg-neutral-800/60'
            }`}
          >
            <LayoutDashboard className="w-4 h-4 flex-shrink-0" />
            {!sidebarCollapsed && <span>Dashboard</span>}
          </button>

          <button
            onClick={() => setActiveTab('aria2')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition cursor-pointer ${
              activeTab === 'aria2'
                ? 'bg-red-600 text-white shadow-lg shadow-red-600/20'
                : 'text-neutral-400 hover:text-white hover:bg-neutral-800/60'
            }`}
          >
            <Download className="w-4 h-4 flex-shrink-0" />
            {!sidebarCollapsed && <span>Aria2c Downloader</span>}
          </button>

          <button
            onClick={() => setActiveTab('media')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition cursor-pointer ${
              activeTab === 'media'
                ? 'bg-red-600 text-white shadow-lg shadow-red-600/20'
                : 'text-neutral-400 hover:text-white hover:bg-neutral-800/60'
            }`}
          >
            <Film className="w-4 h-4 flex-shrink-0" />
            {!sidebarCollapsed && <span>Media &amp; Metadata</span>}
          </button>

          <button
            onClick={() => setActiveTab('users')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold transition cursor-pointer ${
              activeTab === 'users'
                ? 'bg-red-600 text-white shadow-lg shadow-red-600/20'
                : 'text-neutral-400 hover:text-white hover:bg-neutral-800/60'
            }`}
          >
            <Users className="w-4 h-4 flex-shrink-0" />
            {!sidebarCollapsed && <span>User Telemetry</span>}
          </button>
        </nav>

        {/* Sidebar Footer Indicator */}
        <div className="p-3 border-t border-neutral-800/80 text-[11px] text-neutral-500 flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse flex-shrink-0" />
          {!sidebarCollapsed && <span>Local Daemon Active</span>}
        </div>
      </aside>

      {/* 2. Main Workspace */}
      <main className="flex-1 h-full overflow-y-auto overflow-x-hidden min-w-0 flex flex-col">
        {/* Top Header with Compact Telemetry Chips */}
        <header className="h-14 bg-[#121212]/90 border-b border-neutral-800 px-4 sm:px-6 flex items-center justify-between sticky top-0 z-20 backdrop-blur-md flex-shrink-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold text-white uppercase tracking-wider">
              {activeTab === 'dashboard' && 'Operations Dashboard'}
              {activeTab === 'aria2' && 'Aria2c Download Manager'}
              {activeTab === 'media' && 'Media Library & Metadata Manager'}
              {activeTab === 'users' && 'User Telemetry & Audit Logs'}
            </h2>
          </div>

          {/* Compact Telemetry Chips */}
          <div className="flex items-center gap-2 sm:gap-3 text-[11px]">
            <div className="hidden sm:flex items-center gap-1.5 bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300">
              <Cpu className="w-3.5 h-3.5 text-neutral-400" />
              <span>CPU: {capabilities?.cpu_percent?.toFixed(1) || '8.2'}%</span>
            </div>

            <div className="hidden md:flex items-center gap-1.5 bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300">
              <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
              <span>RAM: {capabilities?.available_ram_gb?.toFixed(1) || '1.8'} GB free</span>
            </div>

            <div className="hidden lg:flex items-center gap-1.5 bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300">
              <Server className="w-3.5 h-3.5 text-cyan-400" />
              <span>Disk: {capabilities?.free_disk_gb?.toFixed(0) || '148'} GB free</span>
            </div>

            <div className="flex items-center gap-1.5 bg-purple-500/10 border border-purple-500/30 px-2.5 py-1 rounded-lg text-purple-400 font-mono font-bold uppercase text-[10px]">
              {capabilities?.preferred_encoder || 'H264_NVENC'}
            </div>

            <button
              onClick={refreshCatalogAndStats}
              className="p-1.5 bg-[#181818] hover:bg-neutral-800 text-neutral-300 hover:text-white rounded-lg border border-neutral-800 transition cursor-pointer"
              title="Refresh Stats"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        </header>

        {/* Tab Content Container */}
        <div className="p-4 sm:p-6 space-y-6 max-w-7xl w-full mx-auto pb-24">
          
          {/* TAB 1: DASHBOARD OVERVIEW */}
          {activeTab === 'dashboard' && (
            <div className="space-y-6 animate-fade-in">
              {/* 4 Packed Metrics Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 sm:gap-4 w-full">
                <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
                  <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Registered Accounts</span>
                  <div className="text-2xl sm:text-3xl font-black text-white mt-1">{stats.totalUsers}</div>
                  <span className="text-[10px] text-neutral-500">Supabase Auth Verified</span>
                </div>

                <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
                  <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Total User Visits</span>
                  <div className="text-2xl sm:text-3xl font-black text-emerald-400 mt-1">{stats.totalVisits}</div>
                  <span className="text-[10px] text-neutral-500">Login &amp; Session Records</span>
                </div>

                <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
                  <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Active Sessions Today</span>
                  <div className="text-2xl sm:text-3xl font-black text-cyan-400 mt-1">{stats.activeToday}</div>
                  <span className="text-[10px] text-neutral-500">Authenticated Devices</span>
                </div>

                <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
                  <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Catalog Titles</span>
                  <div className="text-2xl sm:text-3xl font-black text-red-500 mt-1">{stats.totalMedia}</div>
                  <span className="text-[10px] text-neutral-500">Movies &amp; Series</span>
                </div>
              </div>

              {/* Transcoding Engine Governor & Status */}
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-5 sm:p-6 space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-neutral-800/80">
                  <div>
                    <h3 className="text-base font-bold text-white flex items-center gap-2">
                      <span>Transcoding Engine Governor</span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                        settings?.transcode_mode === 'abr_transcode'
                          ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30'
                          : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      }`}>
                        {settings?.transcode_mode === 'abr_transcode' ? 'Multi-Bitrate ABR' : 'Lossless Remux (Mode B)'}
                      </span>
                    </h3>
                    <p className="text-xs text-neutral-400 mt-0.5">
                      {settings?.transcode_mode === 'abr_transcode'
                        ? 'Active: Transcodes videos into 1080p, 720p, 480p adaptive ladders with hardware acceleration.'
                        : 'Active: Mode B Zero-CPU instant remuxing (Copies H.264 video, converts audio to AAC, splits into HLS segments).'}
                    </p>
                  </div>

                  <button
                    onClick={handleToggleTranscode}
                    disabled={loadingToggle}
                    className={`px-4 py-2 rounded-xl text-xs font-bold transition flex items-center gap-2 cursor-pointer shadow-lg ${
                      settings?.transcode_mode === 'abr_transcode'
                        ? 'bg-neutral-800 hover:bg-neutral-700 text-neutral-200'
                        : 'bg-red-600 hover:bg-red-700 text-white'
                    }`}
                  >
                    {loadingToggle ? (
                      <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    ) : (
                      <span>{settings?.transcode_mode === 'abr_transcode' ? 'Switch to Lossless Remux' : 'Enable ABR Transcoding'}</span>
                    )}
                  </button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                  <div className="bg-[#181818] p-3 rounded-xl border border-neutral-800/80">
                    <span className="text-neutral-500 font-medium">Worker Status:</span>
                    <p className="text-neutral-200 font-semibold mt-0.5">
                      {queueStatus?.is_busy ? `Processing (${queueStatus.current_job?.title})` : 'Idle (Waiting for jobs)'}
                    </p>
                  </div>
                  <div className="bg-[#181818] p-3 rounded-xl border border-neutral-800/80">
                    <span className="text-neutral-500 font-medium">Queue Concurrency:</span>
                    <p className="text-neutral-200 font-semibold mt-0.5">Sequential Worker (Limit = 1)</p>
                  </div>
                  <div className="bg-[#181818] p-3 rounded-xl border border-neutral-800/80">
                    <span className="text-neutral-500 font-medium">CPU Nice Throttling:</span>
                    <p className="text-neutral-200 font-semibold mt-0.5">Priority 10 (Background Polite)</p>
                  </div>
                </div>
              </div>

              {/* Quick Jump Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div 
                  onClick={() => setActiveTab('media')}
                  className="bg-[#141414] hover:bg-[#181818] border border-neutral-800 p-5 rounded-2xl cursor-pointer transition group"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="p-3 bg-red-600/10 text-red-500 rounded-xl group-hover:bg-red-600 group-hover:text-white transition">
                        <Film className="w-5 h-5" />
                      </div>
                      <div>
                        <h4 className="font-bold text-sm text-white">Media &amp; Metadata Manager</h4>
                        <p className="text-xs text-neutral-400">Edit storylines, add manual trailer links, modify genres.</p>
                      </div>
                    </div>
                    <ChevronRight className="w-5 h-5 text-neutral-500 group-hover:text-white transition" />
                  </div>
                </div>

                <div 
                  onClick={() => setActiveTab('aria2')}
                  className="bg-[#141414] hover:bg-[#181818] border border-neutral-800 p-5 rounded-2xl cursor-pointer transition group"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="p-3 bg-cyan-600/10 text-cyan-500 rounded-xl group-hover:bg-cyan-600 group-hover:text-white transition">
                        <Download className="w-5 h-5" />
                      </div>
                      <div>
                        <h4 className="font-bold text-sm text-white">Aria2c Download Manager</h4>
                        <p className="text-xs text-neutral-400">Dispatch magnet links &amp; inspect auto-ingest queue.</p>
                      </div>
                    </div>
                    <ChevronRight className="w-5 h-5 text-neutral-500 group-hover:text-white transition" />
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: ARIA2C DOWNLOAD MANAGER */}
          {activeTab === 'aria2' && (
            <div className="space-y-6 animate-fade-in">
              {/* Dispatch Download Card */}
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-5 sm:p-6 space-y-4">
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <Download className="w-4 h-4 text-red-500" />
                  <span>Dispatch New Media Download</span>
                </h3>

                <form onSubmit={handleDispatchDownload} className="space-y-4">
                  <div className="space-y-1">
                    <label className="text-xs text-neutral-400 font-medium">Download URL or Magnet URI</label>
                    <input
                      type="text"
                      required
                      placeholder="magnet:?xt=urn:btih:... or https://example.com/media.mkv"
                      value={downloadUri}
                      onChange={(e) => setDownloadUri(e.target.value)}
                      className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500"
                    />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs text-neutral-400 font-medium">Catalog Title (Optional for Auto-Ingest)</label>
                      <input
                        type="text"
                        placeholder="Search TMDB Title (e.g. Inception)..."
                        value={tmdbQuery}
                        onChange={(e) => setTmdbQuery(e.target.value)}
                        className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs text-neutral-400 font-medium">Category</label>
                      <select
                        value={downloadType}
                        onChange={(e) => setDownloadType(e.target.value as any)}
                        className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500"
                      >
                        <option value="movie">Movie</option>
                        <option value="series">Series</option>
                      </select>
                    </div>
                  </div>

                  <button
                    type="submit"
                    className="px-5 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer flex items-center gap-2"
                  >
                    <Download className="w-4 h-4" />
                    <span>Dispatch to Aria2c Daemon</span>
                  </button>
                </form>
              </div>

              {/* Active Downloads List */}
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-5 sm:p-6 space-y-4">
                <div className="flex items-center justify-between pb-3 border-b border-neutral-800">
                  <h4 className="text-xs font-bold text-neutral-400 uppercase tracking-wider">
                    Active &amp; Completed Downloads
                  </h4>
                  <span className="text-xs text-neutral-500">
                    Speed: {aria2Stats ? `${(Number(aria2Stats.downloadSpeed || 0) / 1024 / 1024).toFixed(1)} MB/s` : '0.0 MB/s'}
                  </span>
                </div>

                {aria2Downloads.length === 0 ? (
                  <div className="py-12 text-center space-y-2">
                    <p className="text-neutral-500 text-xs">No active downloads in Aria2 queue.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {aria2Downloads.map((d: any) => (
                      <div key={d.gid} className="bg-[#181818] p-3.5 rounded-xl border border-neutral-800/80 flex items-center justify-between text-xs">
                        <div>
                          <p className="font-bold text-white">{d.name || d.gid}</p>
                          <span className="text-neutral-500 text-[11px]">
                            Status: <strong className="text-neutral-300">{d.status}</strong> • Speed: {(Number(d.downloadSpeed || 0) / 1024).toFixed(0)} KB/s
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 3: MEDIA & METADATA MANAGER */}
          {activeTab === 'media' && (
            <div className="space-y-6 animate-fade-in">
              {/* Search, Filters & Actions Bar */}
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5 flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
                {/* Search */}
                <div className="relative flex-1">
                  <Search className="w-4 h-4 text-neutral-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    placeholder="Search movie or series title..."
                    value={mediaSearch}
                    onChange={(e) => setMediaSearch(e.target.value)}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl pl-9 pr-3.5 py-2 text-xs text-white focus:outline-none focus:border-red-500"
                  />
                </div>

                {/* Filters */}
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={mediaFilterType}
                    onChange={(e) => setMediaFilterType(e.target.value as any)}
                    className="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none"
                  >
                    <option value="all">All Types</option>
                    <option value="movie">Movies</option>
                    <option value="series">Series</option>
                  </select>

                  <select
                    value={mediaFilterGenre}
                    onChange={(e) => setMediaFilterGenre(e.target.value)}
                    className="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none"
                  >
                    <option value="all">All Genres</option>
                    <option value="Action">Action</option>
                    <option value="Sci-Fi">Sci-Fi</option>
                    <option value="Crime">Crime</option>
                    <option value="Drama">Drama</option>
                    <option value="Animation">Animation</option>
                    <option value="Adventure">Adventure</option>
                    <option value="Thriller">Thriller</option>
                  </select>

                  <select
                    value={mediaSortBy}
                    onChange={(e) => setMediaSortBy(e.target.value as any)}
                    className="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none"
                  >
                    <option value="recent">Recently Added</option>
                    <option value="title">Title (A-Z)</option>
                    <option value="rating">Highest Rating</option>
                    <option value="views">Most Viewed</option>
                  </select>

                  {/* Add New Title Button */}
                  <button
                    onClick={() => openEditModal()}
                    className="px-3.5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer flex items-center gap-1.5 shadow-md ml-auto"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Add Title</span>
                  </button>
                </div>
              </div>

              {/* Media Cards / Table */}
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-neutral-800 flex items-center justify-between text-xs font-semibold text-neutral-400">
                  <span>Showing {filteredMedia.length} titles in catalog</span>
                </div>

                <div className="divide-y divide-neutral-800/60">
                  {filteredMedia.map((m) => {
                    const hasTrailer = !!m.trailer_youtube_key;
                    return (
                      <div key={m.id} className="p-4 sm:p-5 flex items-center justify-between gap-4 hover:bg-[#181818] transition">
                        {/* Poster Thumbnail + Details */}
                        <div className="flex items-center gap-4 min-w-0">
                          <img
                            src={m.poster_url || m.backdrop_url || ''}
                            alt={m.title}
                            className="w-12 h-18 object-cover rounded-lg bg-neutral-900 flex-shrink-0 border border-neutral-800"
                          />
                          <div className="space-y-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <h4 className="text-white font-bold text-sm truncate">{m.title}</h4>
                              <span className="px-2 py-0.5 bg-neutral-800 text-neutral-300 rounded text-[10px] font-bold uppercase">
                                {m.type}
                              </span>
                              {hasTrailer ? (
                                <span className="px-2 py-0.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded text-[10px] font-bold flex items-center gap-1">
                                  <Check className="w-3 h-3" /> Trailer Set
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded text-[10px] font-bold">
                                  No Trailer
                                </span>
                              )}
                            </div>
                            <p className="text-neutral-400 text-xs line-clamp-1">{m.synopsis || 'No storyline set yet.'}</p>
                            <div className="flex items-center gap-3 text-[11px] text-neutral-500">
                              <span>Year: {m.release_year}</span>
                              <span>Rating: {m.maturity_rating || 'U/A 16+'}</span>
                              <span>Genres: {m.genres?.join(', ') || 'N/A'}</span>
                            </div>
                          </div>
                        </div>

                        {/* Action Buttons */}
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <button
                            onClick={() => openEditModal(m)}
                            className="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 hover:text-white rounded-lg text-xs font-semibold transition cursor-pointer flex items-center gap-1.5"
                          >
                            <Edit3 className="w-3.5 h-3.5 text-red-500" />
                            <span>Edit Metadata</span>
                          </button>
                          <button
                            onClick={() => handleDeleteMedia(m.id, m.title)}
                            className="p-1.5 bg-neutral-800/60 hover:bg-red-600/20 text-neutral-400 hover:text-red-500 rounded-lg transition cursor-pointer"
                            title="Delete Title"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: USER TELEMETRY & AUDIT */}
          {activeTab === 'users' && (
            <div className="space-y-6 animate-fade-in">
              <div className="bg-[#141414] border border-neutral-800 rounded-2xl p-5 space-y-4">
                <div className="flex items-center justify-between pb-3 border-b border-neutral-800">
                  <h3 className="text-base font-bold text-white flex items-center gap-2">
                    <Users className="w-4 h-4 text-cyan-400" />
                    <span>Recent Login Sessions &amp; Security Audit</span>
                  </h3>
                  <button
                    onClick={refreshCatalogAndStats}
                    className="text-xs text-neutral-400 hover:text-white flex items-center gap-1 cursor-pointer"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Refresh
                  </button>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-neutral-800 text-neutral-500 text-[11px] font-bold uppercase tracking-wider">
                        <th className="pb-3">User Email / ID</th>
                        <th className="pb-3">Login Timestamp</th>
                        <th className="pb-3">Client IP</th>
                        <th className="pb-3">Country / Location</th>
                        <th className="pb-3">Device / User Agent</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-neutral-800/60">
                      {userLogs.map((log: any) => (
                        <tr key={log.id} className="hover:bg-[#181818] transition text-neutral-300">
                          <td className="py-3 font-semibold text-white">{log.email || log.user_id}</td>
                          <td className="py-3 text-neutral-400">
                            {log.logged_in_at ? new Date(log.logged_in_at).toLocaleString() : 'Recent'}
                          </td>
                          <td className="py-3 font-mono text-neutral-300">{log.ip_address || '127.0.0.1'}</td>
                          <td className="py-3 text-emerald-400 font-medium">{log.country || 'Local Host'}</td>
                          <td className="py-3 text-neutral-500 truncate max-w-xs">{log.user_agent || 'Chrome / Web'}</td>
                        </tr>
                      ))}
                      {userLogs.length === 0 && (
                        <tr>
                          <td colSpan={5} className="py-8 text-center text-neutral-500">
                            No user login sessions recorded yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

        </div>
      </main>

      {/* METADATA EDITOR MODAL (Supports Manual Trailer Links, Synopsis, Genres) */}
      {(editingMedia || isNewTitle) && (
        <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-[#141414] border border-neutral-800 rounded-3xl w-full max-w-2xl p-6 sm:p-8 space-y-6 shadow-2xl my-auto animate-fade-in">
            <div className="flex items-center justify-between pb-3 border-b border-neutral-800">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <Edit3 className="w-5 h-5 text-red-500" />
                <span>{isNewTitle ? 'Add New Title to Catalog' : `Edit "${editingMedia?.title}" Metadata`}</span>
              </h3>
              <button
                onClick={() => setEditingMedia(null)}
                className="p-2 text-neutral-400 hover:text-white rounded-full hover:bg-neutral-800 transition cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveMetadata} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Title</label>
                  <input
                    type="text"
                    required
                    value={editForm.title}
                    onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Type</label>
                  <select
                    value={editForm.type}
                    onChange={(e) => setEditForm({ ...editForm, type: e.target.value as any })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none"
                  >
                    <option value="movie">Movie</option>
                    <option value="series">Series</option>
                  </select>
                </div>
              </div>

              {/* Storyline / Synopsis */}
              <div className="space-y-1">
                <label className="text-xs text-neutral-400 font-medium">Storyline / Synopsis</label>
                <textarea
                  rows={3}
                  required
                  placeholder="Enter detailed storyline or synopsis..."
                  value={editForm.synopsis}
                  onChange={(e) => setEditForm({ ...editForm, synopsis: e.target.value })}
                  className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500 leading-relaxed"
                />
              </div>

              {/* Manual Trailer Link or Key */}
              <div className="space-y-1 bg-[#181818] p-3.5 rounded-xl border border-neutral-800">
                <div className="flex items-center justify-between">
                  <label className="text-xs text-white font-bold flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-red-500" />
                    <span>Trailer Link or YouTube Video Key</span>
                  </label>
                  {editForm.trailer_youtube_key && (
                    <button
                      type="button"
                      onClick={() => setTestTrailerPreview(!testTrailerPreview)}
                      className="text-[11px] text-red-500 hover:underline cursor-pointer"
                    >
                      {testTrailerPreview ? 'Hide Preview' : 'Test Preview'}
                    </button>
                  )}
                </div>
                <input
                  type="text"
                  placeholder="e.g. YoHD9XEInc0 or https://www.youtube.com/watch?v=YoHD9XEInc0"
                  value={editForm.trailer_youtube_key}
                  onChange={(e) => setEditForm({ ...editForm, trailer_youtube_key: e.target.value })}
                  className="w-full bg-black border border-neutral-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-red-500 font-mono mt-1"
                />
                <p className="text-[11px] text-neutral-500 mt-1">
                  You can manually enter any YouTube URL, video ID, or direct mp4 video link here. It updates the database and UI instantly.
                </p>

                {/* Live Preview */}
                {testTrailerPreview && editForm.trailer_youtube_key && (
                  <div className="mt-3 aspect-video w-full rounded-xl overflow-hidden bg-black border border-neutral-700">
                    <iframe
                      className="w-full h-full"
                      src={`https://www.youtube-nocookie.com/embed/${editForm.trailer_youtube_key.replace(/^.*(watch\?v=|youtu.be\/)/, '')}?autoplay=1&mute=1`}
                      title="Trailer Test"
                      allow="autoplay; encrypted-media"
                    />
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Release Year</label>
                  <input
                    type="number"
                    value={editForm.release_year}
                    onChange={(e) => setEditForm({ ...editForm, release_year: Number(e.target.value) })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Maturity Rating</label>
                  <input
                    type="text"
                    value={editForm.maturity_rating}
                    onChange={(e) => setEditForm({ ...editForm, maturity_rating: e.target.value })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Runtime (min)</label>
                  <input
                    type="number"
                    value={editForm.runtime_minutes}
                    onChange={(e) => setEditForm({ ...editForm, runtime_minutes: Number(e.target.value) })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white"
                  />
                </div>
              </div>

              {/* Genres & Audio Languages Dropdowns with Checkbox Multi-Select */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* Genres Dropdown */}
                <div className="space-y-1 relative">
                  <label className="text-xs text-neutral-400 font-medium">Genres</label>
                  <div
                    onClick={() => {
                      setGenresDropdownOpen(!genresDropdownOpen);
                      setAudioDropdownOpen(false);
                    }}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white flex items-center justify-between cursor-pointer hover:border-neutral-500 transition select-none"
                  >
                    <span className="truncate">
                      {editForm.genres.length > 0 ? editForm.genres.join(', ') : 'Select genres...'}
                    </span>
                    <ChevronDown className="w-3.5 h-3.5 text-neutral-400 flex-shrink-0 ml-2" />
                  </div>
                  {genresDropdownOpen && (
                    <div className="absolute z-50 left-0 right-0 mt-1 bg-[#1a1a1a] border border-neutral-700 rounded-xl shadow-2xl p-2.5 max-h-56 overflow-y-auto space-y-1">
                      {AVAILABLE_GENRES.map((g) => {
                        const checked = editForm.genres.includes(g);
                        return (
                          <label
                            key={g}
                            className="flex items-center gap-2.5 px-2 py-1.5 hover:bg-neutral-800 rounded-lg cursor-pointer text-xs text-neutral-200 select-none"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const next = e.target.checked
                                  ? [...editForm.genres, g]
                                  : editForm.genres.filter((x) => x !== g);
                                setEditForm({ ...editForm, genres: next });
                              }}
                              className="w-3.5 h-3.5 rounded bg-neutral-900 border-neutral-600 text-red-600 focus:ring-0 cursor-pointer"
                            />
                            <span>{g}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Audio Languages Dropdown */}
                <div className="space-y-1 relative">
                  <label className="text-xs text-neutral-400 font-medium">Audio Languages</label>
                  <div
                    onClick={() => {
                      setAudioDropdownOpen(!audioDropdownOpen);
                      setGenresDropdownOpen(false);
                    }}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white flex items-center justify-between cursor-pointer hover:border-neutral-500 transition select-none"
                  >
                    <span className="truncate">
                      {editForm.audio_languages.length > 0
                        ? editForm.audio_languages.join(', ')
                        : 'Select audio languages...'}
                    </span>
                    <ChevronDown className="w-3.5 h-3.5 text-neutral-400 flex-shrink-0 ml-2" />
                  </div>
                  {audioDropdownOpen && (
                    <div className="absolute z-50 left-0 right-0 mt-1 bg-[#1a1a1a] border border-neutral-700 rounded-xl shadow-2xl p-2.5 max-h-56 overflow-y-auto space-y-1">
                      {AVAILABLE_AUDIO.map((a) => {
                        const checked = editForm.audio_languages.includes(a);
                        return (
                          <label
                            key={a}
                            className="flex items-center gap-2.5 px-2 py-1.5 hover:bg-neutral-800 rounded-lg cursor-pointer text-xs text-neutral-200 select-none"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const next = e.target.checked
                                  ? [...editForm.audio_languages, a]
                                  : editForm.audio_languages.filter((x) => x !== a);
                                setEditForm({ ...editForm, audio_languages: next });
                              }}
                              className="w-3.5 h-3.5 rounded bg-neutral-900 border-neutral-600 text-red-600 focus:ring-0 cursor-pointer"
                            />
                            <span>{a}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              {/* Posters & Backdrops */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Poster URL</label>
                  <input
                    type="url"
                    placeholder="https://..."
                    value={editForm.poster_url}
                    onChange={(e) => setEditForm({ ...editForm, poster_url: e.target.value })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-neutral-400 font-medium">Backdrop URL</label>
                  <input
                    type="url"
                    placeholder="https://..."
                    value={editForm.backdrop_url}
                    onChange={(e) => setEditForm({ ...editForm, backdrop_url: e.target.value })}
                    className="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white"
                  />
                </div>
              </div>

              {/* Published Toggle */}
              <div className="flex items-center gap-2 pt-2">
                <input
                  type="checkbox"
                  id="pub-toggle"
                  checked={editForm.is_published}
                  onChange={(e) => setEditForm({ ...editForm, is_published: e.target.checked })}
                  className="w-4 h-4 text-red-600 rounded bg-neutral-900 border-neutral-700 cursor-pointer"
                />
                <label htmlFor="pub-toggle" className="text-xs font-semibold text-neutral-300 cursor-pointer">
                  Published (Visible to all streaming members)
                </label>
              </div>

              {/* Form CTAs */}
              <div className="flex items-center justify-end gap-3 pt-4 border-t border-neutral-800">
                <button
                  type="button"
                  onClick={() => setEditingMedia(null)}
                  className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 text-xs font-semibold rounded-xl transition cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-6 py-2 bg-red-600 hover:bg-red-700 text-white text-xs font-bold rounded-xl transition cursor-pointer shadow-lg shadow-red-600/30"
                >
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
