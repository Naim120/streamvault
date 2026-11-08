import React, { useEffect } from 'react';
import { ArrowLeft, Shield, FileText, Scale, Cookie, CheckCircle2 } from 'lucide-react';
import { updatePageSeo } from '../../lib/seo';
import { Footer } from '../layout/Footer';

export type LegalSlug = 'terms' | 'privacy' | 'dmca' | 'cookies';

interface LegalPageProps {
  currentSlug: LegalSlug;
  onSelectSlug: (slug: LegalSlug) => void;
  onBack: () => void;
  isAuthenticated: boolean;
}

export const LegalPage: React.FC<LegalPageProps> = ({
  currentSlug,
  onSelectSlug,
  onBack,
  isAuthenticated,
}) => {
  const tabs = [
    { slug: 'terms' as LegalSlug, label: 'Terms of Service', icon: FileText, title: 'Terms of Service' },
    { slug: 'privacy' as LegalSlug, label: 'Privacy Policy', icon: Shield, title: 'Privacy Policy' },
    { slug: 'dmca' as LegalSlug, label: 'DMCA & Copyright', icon: Scale, title: 'DMCA & Copyright Policy' },
    { slug: 'cookies' as LegalSlug, label: 'Cookie & Telemetry', icon: Cookie, title: 'Cookie & Telemetry Policy' },
  ];

  const currentTab = tabs.find((t) => t.slug === currentSlug) || tabs[0];

  useEffect(() => {
    updatePageSeo({
      title: `${currentTab.title} — StreamVault`,
      description: `Official legal and compliance documentation for StreamVault: ${currentTab.title}.`,
      canonicalPath: `/legal/${currentSlug}`,
      noIndex: false,
    });
  }, [currentSlug, currentTab.title]);

  return (
    <div className="min-h-screen bg-black text-white flex flex-col selection:bg-red-600 selection:text-white">
      {/* Header Bar */}
      <header className="sticky top-0 z-50 bg-black/90 backdrop-blur-xl border-b border-neutral-800/80 px-4 sm:px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-xs sm:text-sm font-semibold text-neutral-400 hover:text-white transition px-3 py-1.5 rounded-xl bg-neutral-900 hover:bg-neutral-800 border border-neutral-700/60 cursor-pointer"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>{isAuthenticated ? 'Back to Home' : 'Back to StreamVault'}</span>
          </button>
          <span className="font-mono font-black text-red-600 tracking-widest text-lg sm:text-xl hidden sm:inline">
            STREAMVAULT
          </span>
        </div>

        <div className="text-xs font-mono text-neutral-400">
          Last Updated: <span className="text-neutral-200">November 2026</span>
        </div>
      </header>

      {/* Main Container */}
      <main className="flex-1 max-w-5xl w-full mx-auto px-4 sm:px-8 py-10">
        {/* Tab Navigation */}
        <div className="flex items-center gap-2 overflow-x-auto pb-4 border-b border-neutral-800 mb-8 scrollbar-none">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.slug === currentSlug;
            return (
              <button
                key={tab.slug}
                onClick={() => onSelectSlug(tab.slug)}
                className={`flex items-center gap-2.5 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold transition shrink-0 cursor-pointer ${
                  isActive
                    ? 'bg-red-600 text-white shadow-lg shadow-red-600/30'
                    : 'bg-neutral-900/80 text-neutral-400 hover:text-white hover:bg-neutral-800 border border-neutral-800'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Legal Document Content */}
        <div className="bg-[#141414]/80 border border-neutral-800/90 rounded-3xl p-6 sm:p-12 shadow-2xl space-y-10 text-neutral-300 text-sm sm:text-base leading-relaxed">
          {/* TAB 1: TERMS OF SERVICE */}
          {currentSlug === 'terms' && (
            <article className="space-y-8 animate-fade-in">
              <div className="border-b border-neutral-800 pb-6">
                <span className="text-xs font-mono tracking-wider uppercase text-red-500 font-bold">
                  StreamVault Master Service Terms
                </span>
                <h1 className="text-3xl sm:text-4xl font-black text-white mt-2">
                  Terms and Conditions of Use
                </h1>
                <p className="text-neutral-400 text-sm mt-2">
                  Please review these Terms carefully before accessing or using the StreamVault media engine.
                </p>
              </div>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">01.</span> Account Eligibility & Registration
                </h2>
                <p>
                  To create an account and access streaming media on StreamVault, you must be at least 18 years of age, or have reached the legal age of majority in your jurisdiction. Individuals under 18 may only use the service under the supervision and with the express consent of a parent or legal guardian.
                </p>
                <p>
                  You agree to provide accurate, current, and complete registration credentials, including a verified email address. You are solely responsible for maintaining the confidentiality of your credentials and for all activities conducted through your account.
                </p>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">02.</span> Permitted Personal Non-Commercial Streaming Use
                </h2>
                <p>
                  StreamVault grants you a personal, revocable, non-exclusive, non-transferable license to access and stream audio-visual titles for private, non-commercial entertainment purposes.
                </p>
                <ul className="list-disc pl-5 space-y-2 text-neutral-400">
                  <li>
                    <strong className="text-neutral-200">Strict Prohibition on Stream Ripping:</strong> You may not record, intercept, mirror, download, scrape, reverse-engineer, or redistribute raw HLS transport streams (<code className="text-red-400">.m3u8</code> / <code className="text-red-400">.ts</code>) using automated scripts, scrapers, or third-party capture tools.
                  </li>
                  <li>
                    <strong className="text-neutral-200">Anti-Account Sharing:</strong> You may not sell, rent, lease, or sub-license access tokens or credentials to external parties.
                  </li>
                  <li>
                    <strong className="text-neutral-200">Security Circumvention:</strong> Any attempt to probe internal ports (<code className="text-neutral-300">:4000</code>, <code className="text-neutral-300">:6800</code>), bypass rate-limiting governors, or forge session headers will result in immediate permanent account termination and IP blacklisting.
                  </li>
                </ul>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">03.</span> Content Maturity Ratings & Viewer Discretion
                </h2>
                <p>
                  Titles in the StreamVault catalog are tagged with standard age-appropriateness indicators:
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
                  <div className="p-3 bg-neutral-900 border border-neutral-800 rounded-xl">
                    <span className="px-2 py-0.5 bg-neutral-800 text-white rounded text-xs font-bold mr-2">U</span>
                    <span className="text-xs text-neutral-300">Universal — Suitable for all ages.</span>
                  </div>
                  <div className="p-3 bg-neutral-900 border border-neutral-800 rounded-xl">
                    <span className="px-2 py-0.5 bg-neutral-800 text-white rounded text-xs font-bold mr-2">U/A 13+</span>
                    <span className="text-xs text-neutral-300">Suitable for ages 13 and older.</span>
                  </div>
                  <div className="p-3 bg-neutral-900 border border-neutral-800 rounded-xl">
                    <span className="px-2 py-0.5 bg-neutral-800 text-white rounded text-xs font-bold mr-2">U/A 16+</span>
                    <span className="text-xs text-neutral-300">Suitable for ages 16 and older.</span>
                  </div>
                  <div className="p-3 bg-neutral-900 border border-neutral-800 rounded-xl">
                    <span className="px-2 py-0.5 bg-red-950 text-red-400 border border-red-800 rounded text-xs font-bold mr-2">A</span>
                    <span className="text-xs text-neutral-300">Adults only — Restricted to 18+.</span>
                  </div>
                </div>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">04.</span> Service Availability & Disclaimers
                </h2>
                <p>
                  StreamVault provides streaming delivery on an "as-is" and "as-available" basis. While we strive for 99.9% uptime, we do not guarantee uninterrupted service during planned transcoding maintenance, network routing failovers, or upstream ISP outages. In no event shall StreamVault or its operators be liable for indirect, punitive, or consequential damages.
                </p>
              </section>
            </article>
          )}

          {/* TAB 2: PRIVACY POLICY */}
          {currentSlug === 'privacy' && (
            <article className="space-y-8 animate-fade-in">
              <div className="border-b border-neutral-800 pb-6">
                <span className="text-xs font-mono tracking-wider uppercase text-red-500 font-bold">
                  Data Privacy & Compliance Framework
                </span>
                <h1 className="text-3xl sm:text-4xl font-black text-white mt-2">
                  Privacy Policy
                </h1>
                <p className="text-neutral-400 text-sm mt-2">
                  Compliant with GDPR (EU 2016/679) and India Digital Personal Data Protection Act (DPDP) 2023.
                </p>
              </div>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">01.</span> Information We Collect
                </h2>
                <p>We believe in radical data minimization. We only collect information strictly required to run your streaming sessions:</p>
                <ul className="list-disc pl-5 space-y-2 text-neutral-400">
                  <li>
                    <strong className="text-neutral-200">Account Identity:</strong> Email address and user profile display name provided during registration.
                  </li>
                  <li>
                    <strong className="text-neutral-200">Playback Progress:</strong> Timestamp coordinates (position in seconds, completion flags) so you can resume playback seamlessly across your devices.
                  </li>
                  <li>
                    <strong className="text-neutral-200">Security & Session Telemetry:</strong> Anonymized client IP addresses (IPv4/IPv6), user-agent strings, and session cooldown logs collected exclusively to mitigate brute-force attacks and prevent unauthorized stream scraping.
                  </li>
                  <li>
                    <strong className="text-neutral-200">Quality of Experience (QoE):</strong> Diagnostic metrics such as HLS buffer stalls and adaptive resolution downshifts to automatically tune transcoding bitrates.
                  </li>
                </ul>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">02.</span> How We Protect Your Data
                </h2>
                <p>
                  Your information is protected through multi-layered defense mechanisms:
                </p>
                <div className="space-y-2 pt-1">
                  <div className="flex items-start gap-2.5 text-xs sm:text-sm">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-1" />
                    <span><strong>Database Row-Level Security (RLS):</strong> User data partitions are isolated at the database engine level. No user can read or write another user's watch history.</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs sm:text-sm">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-1" />
                    <span><strong>Zero Third-Party Advertising:</strong> We never sell, monetize, or broker your personal data to ad networks, tracking trackers, or external marketing entities.</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs sm:text-sm">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-1" />
                    <span><strong>Cryptographic Tokens:</strong> All API and stream requests require ephemeral, cryptographically signed bearer tokens over TLS 1.3 encryption.</span>
                  </div>
                </div>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">03.</span> Your Legal Rights (GDPR & India DPDP Act)
                </h2>
                <p>
                  Under international privacy frameworks, you have full ownership over your digital footprint:
                </p>
                <ul className="list-disc pl-5 space-y-2 text-neutral-400">
                  <li><strong>Right to Access:</strong> Request a complete export of your profile, watch progress, and custom lists.</li>
                  <li><strong>Right to Rectification:</strong> Update or correct your email and profile display name at any time from your account settings.</li>
                  <li><strong>Right to Erasure ("Right to be Forgotten"):</strong> Request immediate and permanent purging of your account, watch progress, and stored tokens from our database.</li>
                  <li><strong>Right to Grievance Redressal:</strong> Submit privacy concerns directly to our dedicated Data Protection Officer.</li>
                </ul>
              </section>
            </article>
          )}

          {/* TAB 3: DMCA & COPYRIGHT POLICY */}
          {currentSlug === 'dmca' && (
            <article className="space-y-8 animate-fade-in">
              <div className="border-b border-neutral-800 pb-6">
                <span className="text-xs font-mono tracking-wider uppercase text-red-500 font-bold">
                  Intellectual Property & Safe Harbor Compliance
                </span>
                <h1 className="text-3xl sm:text-4xl font-black text-white mt-2">
                  DMCA & Copyright Policy
                </h1>
                <p className="text-neutral-400 text-sm mt-2">
                  Safe Harbor compliance pursuant to Title 17, United States Code, Section 512 (DMCA) and Information Technology Act, 2000.
                </p>
              </div>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">01.</span> Safe Harbor Notice & Takedown
                </h2>
                <p>
                  StreamVault respects intellectual property rights and adheres strictly to safe harbor provisions. If you believe that your copyrighted work has been linked, indexed, or made accessible through StreamVault in a manner that constitutes infringement, please submit a formal notification to our Designated Copyright Agent.
                </p>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">02.</span> Elements of a Valid Infringement Notice
                </h2>
                <p>To be effective under 17 U.S.C. § 512(c)(3), your notice must be in writing and include:</p>
                <ol className="list-decimal pl-5 space-y-2 text-neutral-400">
                  <li>Physical or electronic signature of the copyright owner or authorized representative.</li>
                  <li>Specific identification of the copyrighted work claimed to have been infringed.</li>
                  <li>Identification of the material to be removed, including exact StreamVault catalog deep links (<code className="text-red-400">/home/[media-id]</code>).</li>
                  <li>Sufficient contact details: full legal name, physical address, telephone number, and official email address.</li>
                  <li>A statement that you have a good faith belief that use of the material is unauthorized by the copyright owner, its agent, or the law.</li>
                  <li>A statement under penalty of perjury that information in the notification is accurate.</li>
                </ol>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">03.</span> Repeat Infringer & Counter-Notice Policy
                </h2>
                <p>
                  Upon receipt of a substantiated takedown notice, StreamVault will promptly disable access to the identified title. In accordance with Section 512(i), StreamVault maintains an automated repeat infringer policy under which accounts or media feeds associated with multiple valid notices will be permanently expunged.
                </p>
              </section>

              <div className="p-5 bg-neutral-900 border border-neutral-800 rounded-2xl space-y-2">
                <h3 className="font-bold text-white text-base">Designated Grievance & DMCA Redressal Office</h3>
                <p className="text-xs text-neutral-400">
                  Attn: StreamVault Legal Operations & Grievance Officer<br />
                  Email: <span className="text-red-400 font-mono">dmca@streamvault.local</span> / <span className="text-red-400 font-mono">grievance@streamvault.local</span><br />
                  Response Commitment: Initial acknowledgment within 24 hours, resolution within 48 business hours.
                </p>
              </div>
            </article>
          )}

          {/* TAB 4: COOKIE & TELEMETRY POLICY */}
          {currentSlug === 'cookies' && (
            <article className="space-y-8 animate-fade-in">
              <div className="border-b border-neutral-800 pb-6">
                <span className="text-xs font-mono tracking-wider uppercase text-red-500 font-bold">
                  Client-Side Storage & Telemetry Specifications
                </span>
                <h1 className="text-3xl sm:text-4xl font-black text-white mt-2">
                  Cookie & Telemetry Policy
                </h1>
                <p className="text-neutral-400 text-sm mt-2">
                  Transparent disclosure of cookies, local storage objects, and playback diagnostics.
                </p>
              </div>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">01.</span> Essential Session Storage
                </h2>
                <p>
                  StreamVault uses modern browser storage mechanisms to maintain session continuity:
                </p>
                <div className="overflow-x-auto pt-2">
                  <table className="w-full text-left text-xs sm:text-sm">
                    <thead>
                      <tr className="border-b border-neutral-800 text-neutral-400">
                        <th className="py-2.5 font-semibold">Key / Token Name</th>
                        <th className="py-2.5 font-semibold">Storage Type</th>
                        <th className="py-2.5 font-semibold">Purpose & Necessity</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-neutral-800/60 text-neutral-300">
                      <tr>
                        <td className="py-3 font-mono text-red-400">sb-*-auth-token</td>
                        <td className="py-3">localStorage</td>
                        <td className="py-3 text-neutral-400">Encrypted JWT token to maintain your authenticated login session.</td>
                      </tr>
                      <tr>
                        <td className="py-3 font-mono text-red-400">sv_cookie_consent</td>
                        <td className="py-3">localStorage</td>
                        <td className="py-3 text-neutral-400">Remembers your cookie banner preferences so it does not reappear.</td>
                      </tr>
                      <tr>
                        <td className="py-3 font-mono text-red-400">sv_post_login_redirect</td>
                        <td className="py-3">sessionStorage</td>
                        <td className="py-3 text-neutral-400">Temporarily preserves deep links (e.g. /home/:id) during authentication.</td>
                      </tr>
                      <tr>
                        <td className="py-3 font-mono text-red-400">streamvault_forgot_cooldown</td>
                        <td className="py-3">sessionStorage</td>
                        <td className="py-3 text-neutral-400">Client-side 60s cooldown protecting against password reset email flooding.</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="space-y-3">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <span className="text-red-500 font-mono text-sm">02.</span> First-Party Technical Telemetry
                </h2>
                <p>
                  To optimize video delivery across fluctuating internet connections, our HLS player collects first-party telemetry:
                </p>
                <ul className="list-disc pl-5 space-y-2 text-neutral-400">
                  <li><strong>Buffer Health:</strong> Number of seconds currently buffered to prevent video stalls.</li>
                  <li><strong>Adaptive Bitrate (ABR) Switches:</strong> Transitions between 1080p, 720p, and 480p ladders based on network throughput.</li>
                  <li><strong>Audio Track & Subtitle State:</strong> Tracks your selected audio stream and WebVTT language preference.</li>
                </ul>
                <p className="text-xs text-neutral-400 pt-2">
                  * All technical diagnostics are processed directly on your self-hosted instance and are never broadcast to external data brokers.
                </p>
              </section>
            </article>
          )}
        </div>
      </main>

      {/* Commercial Footer */}
      <Footer onNavigateLegal={onSelectSlug} />
    </div>
  );
};

