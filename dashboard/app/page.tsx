'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Image from 'next/image';

/* ── Types ── */
type StageId = 'drive_extract' | 'llm_analysis' | 'report_service' | 'email';
type StageStatus = 'pending' | 'running' | 'completed' | 'error';

interface PipelineEvent {
  stage: StageId | 'pipeline';
  status: StageStatus | 'success' | 'failed';
  message?: string;
  html?: string;
}

interface AppSettings {
  recipients: string[];
  next_run: string;
  stop_run: string;
  continuous: boolean;
  active: boolean;
  subject: string;
  body_line: string;
  interval_hours: number;
  cron_expression: string;
  last_run: string | null;
}

const AVAILABLE_EMAILS = [
  'aryan.gupta@wohlig.com',
  'chirag@wohlig.com',
  'jagruti@wohlig.com',
  'dhruv.solanki@wohlig.com',
  'chintan@wohlig.com',
];

/* ── Helpers ── */
const fmtDate = (d?: string | null) => {
  if (!d) return 'Not scheduled';
  const date = new Date(d);
  if (isNaN(date.getTime())) return 'Not scheduled';
  const opts: Intl.DateTimeFormatOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
  const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
  return `${date.toLocaleDateString(undefined, opts)} at ${time} ${tz}`;
};

const fmtLastRun = (d?: string | null) => {
  if (!d) return 'No runs yet';
  const date = new Date(d);
  if (isNaN(date.getTime())) return 'No runs yet';
  const opts: Intl.DateTimeFormatOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false };
  return date.toLocaleString(undefined, opts);
};

/* ── Main Page ── */
export default function HomePage() {
  const [html, setHtml] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [emailSending, setEmailSending] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [activeTab, setActiveTab] = useState<'preview' | 'source'>('preview');
  const [status, setStatus] = useState<{ text: string; type: 'ok' | 'err' | '' }>({ text: 'Ready', type: '' });

  const [stages, setStages] = useState<Record<StageId, { status: StageStatus; log: string }>>({
    drive_extract: { status: 'pending', log: 'Waiting...' },
    llm_analysis: { status: 'pending', log: 'Waiting...' },
    report_service: { status: 'pending', log: 'Waiting...' },
    email: { status: 'pending', log: 'Waiting...' },
  });
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [pipelineMsg, setPipelineMsg] = useState('Ready to start...');
  const [pipelineState, setPipelineState] = useState<'idle' | 'success' | 'error'>('idle');

  const [settings, setSettings] = useState<AppSettings>({
    recipients: ['chintan@wohlig.com', 'jagruti@wohlig.com', 'chirag@wohlig.com'],
    next_run: '',
    stop_run: '',
    continuous: false,
    active: false,
    subject: 'Company Workforce Report',
    body_line: 'Dear Team,\n\nPlease find the attached Company Workforce Report for your review.\n\nThis report summarizes the current workforce status, project allocations, and resource utilization across the organization.\n\nRegards,\n\nDhruv Solanki\nAryan Gupta',
    interval_hours: 24,
    cron_expression: '',
    last_run: null,
  });

  /* ── Fetch existing report on mount ── */
  useEffect(() => {
    fetch('/api/report')
      .then(r => r.json())
      .then(d => {
        if (d.html) {
          setHtml(d.html);
          setStatus({ text: 'Loaded existing report', type: 'ok' });
        }
      })
      .catch(() => {});
  }, []);

  /* ── Fetch settings on mount ── */
  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then(d => {
        if (d) setSettings(prev => ({ ...prev, ...d }));
      })
      .catch(() => {});
  }, []);

  /* ── Run Pipeline + Email (Go button) ── */
  const runAndEmail = useCallback(async () => {
    setLoading(true);
    setPipelineOpen(true);
    setPipelineState('idle');
    setPipelineMsg('Starting pipeline...');
    setStages({
      drive_extract: { status: 'pending', log: 'Waiting...' },
      llm_analysis: { status: 'pending', log: 'Waiting...' },
      report_service: { status: 'pending', log: 'Waiting...' },
      email: { status: 'pending', log: 'Waiting...' },
    });

    try {
      const res = await fetch('/api/run-and-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recipients: settings.recipients,
          subject: settings.subject,
          body_line: settings.body_line,
        }),
      });
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.trim() || !line.startsWith('data: ')) continue;
          const dataStr = line.slice(6);
          if (!dataStr) continue;
          try {
            const data: PipelineEvent = JSON.parse(dataStr);
            if (data.stage === 'pipeline') {
              if (data.status === 'success') {
                setPipelineState('success');
                setPipelineMsg('Report generated successfully!');
                setHtml(data.html ?? '');
                setStatus({ text: 'Report generated successfully', type: 'ok' });
                setSettings(prev => ({ ...prev, last_run: new Date().toISOString() }));
              } else {
                setPipelineState('error');
                setPipelineMsg(`Failed: ${data.message || 'Unknown error'}`);
                setStatus({ text: `Pipeline failed: ${data.message || ''}`, type: 'err' });
              }
            } else if (data.stage === 'email') {
              const sid = data.stage as StageId;
              setStages(prev => ({
                ...prev,
                [sid]: { status: data.status as StageStatus, log: data.message || '' },
              }));
              if (data.status === 'success') {
                setPipelineMsg('Email sent successfully!');
                setStatus({ text: 'Email sent successfully', type: 'ok' });
                setTimeout(() => setPipelineOpen(false), 3000);
              } else if (data.status === 'failed') {
                setPipelineState('error');
                setPipelineMsg(`Email failed: ${data.message || ''}`);
                setStatus({ text: `Email failed: ${data.message || ''}`, type: 'err' });
                // Keep overlay open so user sees the error
              } else {
                setPipelineMsg(`Email: ${data.message || 'Running...'}`);
              }
            } else {
              const sid = data.stage as StageId;
              setStages(prev => ({
                ...prev,
                [sid]: { status: data.status as StageStatus, log: data.message || '' },
              }));
              setPipelineMsg(`${sid}: ${data.message || 'Running...'}`);
            }
          } catch {
            // ignore parse errors
          }
        }
      }
    } catch (err: any) {
      setPipelineState('error');
      setPipelineMsg(err?.message?.includes('Failed to fetch') ? 'Backend not running' : `Error: ${err.message}`);
      setStatus({ text: 'Backend not running or unreachable', type: 'err' });
    } finally {
      setLoading(false);
    }
  }, [settings]);

  /* ── Run Pipeline Only (no email) ── */
  const runPipelineOnly = useCallback(async () => {
    setLoading(true);
    setPipelineOpen(true);
    setPipelineState('idle');
    setPipelineMsg('Starting pipeline...');
    setStages({
      drive_extract: { status: 'pending', log: 'Waiting...' },
      llm_analysis: { status: 'pending', log: 'Waiting...' },
      report_service: { status: 'pending', log: 'Waiting...' },
      email: { status: 'pending', log: 'Skipped — report only mode' },
    });

    try {
      const res = await fetch('/api/run-pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.trim() || !line.startsWith('data: ')) continue;
          const dataStr = line.slice(6);
          if (!dataStr) continue;
          try {
            const data: PipelineEvent = JSON.parse(dataStr);
            if (data.stage === 'pipeline') {
              if (data.status === 'success') {
                setPipelineState('success');
                setPipelineMsg('Report generated successfully!');
                setHtml(data.html ?? '');
                setStatus({ text: 'Report generated successfully', type: 'ok' });
                setSettings(prev => ({ ...prev, last_run: new Date().toISOString() }));
                setTimeout(() => setPipelineOpen(false), 2000);
              } else {
                setPipelineState('error');
                setPipelineMsg(`Failed: ${data.message || 'Unknown error'}`);
                setStatus({ text: `Pipeline failed: ${data.message || ''}`, type: 'err' });
              }
            } else {
              const sid = data.stage as StageId;
              setStages(prev => ({
                ...prev,
                [sid]: { status: data.status as StageStatus, log: data.message || '' },
              }));
              setPipelineMsg(`${sid}: ${data.message || 'Running...'}`);
            }
          } catch {
            // ignore parse errors
          }
        }
      }
    } catch (err: any) {
      setPipelineState('error');
      setPipelineMsg(err?.message?.includes('Failed to fetch') ? 'Backend not running' : `Error: ${err.message}`);
      setStatus({ text: 'Backend not running or unreachable', type: 'err' });
    } finally {
      setLoading(false);
    }
  }, []);
  const sendEmail = useCallback(async () => {
    if (!settings.recipients.length) {
      setStatus({ text: 'No recipients selected', type: 'err' });
      return;
    }
    setEmailSending(true);
    try {
      const res = await fetch('/api/send-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recipients: settings.recipients,
          subject: settings.subject,
          body_line: settings.body_line,
        }),
      });
      const data = await res.json();
      if (data.success) {
        setStatus({ text: `Email sent to ${data.sent_to?.join(', ')}`, type: 'ok' });
      } else {
        setStatus({ text: `Email failed: ${data.error || data.detail || ''}`, type: 'err' });
      }
    } catch (err: any) {
      setStatus({ text: `Email failed: ${err.message}`, type: 'err' });
    } finally {
      setEmailSending(false);
    }
  }, [settings]);

  /* ── Save Settings ── */
  const saveSettings = useCallback(async () => {
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      const data = await res.json();
      if (data.settings) setSettings(prev => ({ ...prev, ...data.settings }));
      setShowSettings(false);
      setStatus({ text: 'Settings saved', type: 'ok' });
    } catch (err: any) {
      setStatus({ text: `Save failed: ${err.message}`, type: 'err' });
    }
  }, [settings]);

  const resetSettings = useCallback(() => {
    setSettings({
      recipients: [],
      next_run: '',
      stop_run: '',
      continuous: false,
      active: false,
      subject: 'Company report',
      body_line: 'Please find the attached company workforce report.',
      interval_hours: 24,
      cron_expression: '',
      last_run: settings.last_run,
    });
  }, [settings.last_run]);

  /* ── Toggle recipient ── */
  const toggleRecipient = useCallback((email: string) => {
    setSettings(prev => {
      const exists = prev.recipients.includes(email);
      return {
        ...prev,
        recipients: exists ? prev.recipients.filter(e => e !== email) : [...prev.recipients, email],
      };
    });
  }, []);

  /* ── Derived states ── */

  const iframeSrc = useMemo(() => {
    if (!html) return '';
    const blob = new Blob([html], { type: 'text/html' });
    return URL.createObjectURL(blob);
  }, [html]);

  /* ── Render ── */
  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="sticky top-0 z-50 flex items-center justify-between gap-4 px-6 py-3 bg-navy text-white border-b border-hairline/40">
        <div className="flex items-center gap-3">
          <div className="bg-gradient-to-br from-mist to-orchid rounded-lg px-3 py-1.5 border border-hairline shadow-sm flex items-center justify-center">
            <Image src="/logo.webp" alt="Wohlig Logo" width={80} height={24} className="object-contain" priority />
          </div>
          <span className="text-sm font-medium text-white/80 hidden sm:inline">Report Dashboard</span>
        </div>
        <div className="flex items-center gap-3">
          {/* GO button - runs pipeline AND sends email */}
          <button
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition ${
              loading
                ? 'bg-white/10 text-white/50 cursor-not-allowed'
                : 'bg-gradient-to-r from-indigo to-purple text-white hover:brightness-110 shadow-md'
            }`}
            onClick={() => { if (!loading) runAndEmail(); }}
            disabled={loading}
          >
            {loading ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Running…
              </>
            ) : (
              <>
                ▶ Go (Run Pipeline + Send Email)
              </>
            )}
          </button>

          {/* Generate Report Once — pipeline only, no email */}
          <button
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-white/10 hover:bg-white/20 text-white transition ${loading ? 'opacity-50 cursor-not-allowed' : ''}`}
            onClick={() => { if (!loading) runPipelineOnly(); }}
            disabled={loading}
          >
            {loading ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Running…
              </>
            ) : 'Generate Report Only'}
          </button>

          {/* Send Email only */}
          <button
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition ${
              settings.recipients.length && !emailSending
                ? 'bg-emerald-500 text-white hover:bg-emerald-600 shadow-md'
                : 'bg-white/10 text-white/40 cursor-not-allowed'
            }`}
            onClick={sendEmail}
            disabled={!settings.recipients.length || emailSending}
          >
            {emailSending ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Sending…
              </>
            ) : (
              <>📧 Send Email</>
            )}
          </button>

          <button
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-white/10 hover:bg-white/20 text-white transition"
            onClick={() => setShowSettings(s => !s)}
          >
            ⚙️ Settings
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 flex flex-col gap-4 p-5 max-w-[1400px] mx-auto w-full">
        {/* Status bar */}
        <div className="flex items-center gap-3 px-4 py-2.5 bg-white border border-hairline rounded-xl shadow-sm text-sm text-slate">
          <span className={`w-2.5 h-2.5 rounded-full ${status.type === 'ok' ? 'bg-success' : status.type === 'err' ? 'bg-danger' : 'bg-slate'}`} />
          <span>{status.text}</span>
          {settings.last_run && (
            <span className="ml-auto text-xs text-slate/70">Last run: {fmtLastRun(settings.last_run)}</span>
          )}
        </div>

        {/* Viewer */}
        <div className="relative flex-1 bg-white border border-hairline rounded-xl overflow-hidden shadow-sm flex flex-col min-h-[60vh]">
          <div className="flex border-b border-hairline bg-mist">
            {(['preview', 'source'] as const).map(tab => (
              <button
                key={tab}
                className={`px-5 py-3 text-sm font-semibold border-b-2 transition ${
                  activeTab === tab
                    ? 'text-indigo border-indigo bg-indigo/[0.06]'
                    : 'text-slate border-transparent hover:text-indigo hover:bg-indigo/[0.04]'
                }`}
                onClick={() => setActiveTab(tab)}
              >
                {tab === 'preview' ? 'Preview' : 'Source'}
              </button>
            ))}
          </div>

          <div className="flex-1 relative bg-light overflow-hidden">
            {activeTab === 'preview' ? (
              html ? (
                <iframe
                  src={iframeSrc}
                  className="absolute inset-0 w-full h-full border-none"
                  sandbox="allow-scripts allow-same-origin"
                />
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-slate text-sm gap-2">
                  <p>Click <span className="font-semibold text-indigo">Go</span> to run the pipeline and send email:</p>
                  <p className="text-xs opacity-70">Extract data → LLM Analysis → Generate HTML → PDF → Email</p>
                </div>
              )
            ) : (
              <pre className="absolute inset-0 w-full h-full p-5 text-xs leading-relaxed text-slate whitespace-pre-wrap break-words font-mono bg-white overflow-auto">
                {html || 'No HTML generated yet.'}
              </pre>
            )}
          </div>

          {/* Pipeline overlay */}
          {pipelineOpen && (
            <div className="absolute inset-0 bg-white/97 backdrop-blur-sm flex flex-col p-8 gap-6 overflow-y-auto z-20">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-navy">Pipeline Progress</h2>
                <button className="text-sm text-slate hover:text-indigo px-3 py-1 rounded-lg hover:bg-mist transition" onClick={() => setPipelineOpen(false)}>
                  ✕ Close
                </button>
              </div>

              <div className="flex flex-col gap-3">
                {([
                  { id: 'drive_extract' as StageId, num: 1, name: 'Fetch Excel from Google Drive', desc: 'Download and extract all sheet data' },
                  { id: 'llm_analysis' as StageId, num: 2, name: 'LLM Analysis', desc: 'Send data to Ollama for workforce audit' },
                  { id: 'report_service' as StageId, num: 3, name: 'Generate HTML Report', desc: 'Render the final workforce report from template' },
                  { id: 'email' as StageId, num: 4, name: 'Send Email', desc: 'Generate PDF and email to selected recipients' },
                ]).map(s => {
                  const st = stages[s.id];
                  return (
                    <div
                      key={s.id}
                      className={`flex items-start gap-4 p-4 rounded-xl border transition ${
                        st.status === 'running'
                          ? 'border-indigo shadow-[0_0_0_3px_rgba(67,84,212,0.08)]'
                          : st.status === 'completed'
                          ? 'border-success bg-success/[0.03]'
                          : st.status === 'error'
                          ? 'border-danger bg-danger/[0.03]'
                          : 'border-hairline opacity-60'
                      }`}
                    >
                      <div
                        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold shrink-0 border ${
                          st.status === 'running'
                            ? 'bg-gradient-to-br from-indigo to-purple text-white border-transparent animate-pulse'
                            : st.status === 'completed'
                            ? 'bg-success text-white border-transparent'
                            : st.status === 'error'
                            ? 'bg-danger text-white border-transparent'
                            : 'bg-white text-slate border-hairline'
                        }`}
                      >
                        {s.num}
                      </div>
                      <div className="flex-1">
                        <div className="font-bold text-sm text-navy">{s.name}</div>
                        <div className="text-xs text-slate mt-0.5">{s.desc}</div>
                        <div className="mt-2 p-2 bg-mist rounded-lg text-xs font-mono text-slate max-h-28 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                          {st.log}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="flex items-center justify-center gap-3 pt-4 border-t border-hairline">
                <div className="w-4 h-4 border-2 border-hairline border-t-indigo rounded-full animate-spin" />
                <span className={`text-sm font-semibold ${pipelineState === 'error' ? 'text-danger' : pipelineState === 'success' ? 'text-success' : 'text-indigo'}`}>
                  {pipelineMsg}
                </span>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Settings panel */}
      {showSettings && (
        <div
          className="fixed inset-0 z-[100] flex justify-end"
          onClick={e => {
            if (e.currentTarget === e.target) setShowSettings(false);
          }}
        >
          <aside className="w-[420px] max-w-full bg-white border-l border-hairline shadow-2xl flex flex-col h-full">
            <div className="flex items-center justify-between px-5 py-4 bg-mist border-b border-hairline">
              <span className="font-bold text-navy">Settings</span>
              <button className="text-sm text-slate hover:text-indigo px-3 py-1 rounded-lg hover:bg-white transition" onClick={() => setShowSettings(false)}>
                Close
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-6">
              {/* Status */}
              <div className="space-y-3">
                <h3 className="text-[11px] font-bold text-indigo uppercase tracking-wider">Status</h3>
                <div className="flex items-center justify-between bg-white border border-hairline rounded-xl p-4">
                  <span className="font-bold text-navy text-sm">Automation</span>
                  <span className={`px-3 py-1 rounded-full text-xs font-bold ${settings.active ? 'bg-success/10 text-success' : 'bg-slate/10 text-slate'}`}>
                    {settings.active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </div>
              </div>

              {/* Schedule */}
              <div className="space-y-3">
                <h3 className="text-[11px] font-bold text-indigo uppercase tracking-wider">Schedule</h3>
                
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate">Next run</label>
                  <input
                    type="datetime-local"
                    value={settings.next_run}
                    onChange={e => setSettings(prev => ({ ...prev, next_run: e.target.value }))}
                    className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition"
                  />
                </div>
                
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate">Stop run</label>
                  <input
                    type="datetime-local"
                    value={settings.stop_run}
                    onChange={e => setSettings(prev => ({ ...prev, stop_run: e.target.value }))}
                    className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition"
                  />
                </div>

                <div className="pt-2">
                  <button
                    className={`w-full py-2.5 rounded-xl text-sm font-semibold border transition ${
                      settings.continuous
                        ? 'bg-gradient-to-r from-indigo to-purple text-white border-transparent shadow-md'
                        : 'bg-white text-slate border-hairline hover:border-indigo hover:text-indigo'
                    }`}
                    onClick={() => setSettings(prev => ({ ...prev, continuous: !prev.continuous }))}
                  >
                    🔁 Continuous Mode
                  </button>
                </div>

                {settings.continuous && (
                  <div className="space-y-3 pt-2">
                    <div className="space-y-1">
                      <label className="text-sm font-medium text-slate">Interval (hours)</label>
                      <input
                        type="number"
                        min={1}
                        value={settings.interval_hours}
                        onChange={e => setSettings(prev => ({ ...prev, interval_hours: Number(e.target.value) }))}
                        className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-sm font-medium text-slate">Or Cron Expression</label>
                      <input
                        type="text"
                        placeholder="e.g. 0 9 * * *"
                        value={settings.cron_expression}
                        onChange={e => setSettings(prev => ({ ...prev, cron_expression: e.target.value }))}
                        className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition font-mono"
                      />
                      <p className="text-xs text-slate opacity-70">Takes precedence over interval if set.</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Recipients */}
              <div className="space-y-3">
                <h3 className="text-[11px] font-bold text-indigo uppercase tracking-wider">Recipients</h3>
                <div className="grid grid-cols-1 gap-2">
                  {AVAILABLE_EMAILS.map(email => (
                    <button
                      key={email}
                      onClick={() => toggleRecipient(email)}
                      className={`px-4 py-2.5 rounded-xl text-sm font-semibold border transition text-center ${
                        settings.recipients.includes(email)
                          ? 'bg-gradient-to-r from-indigo to-purple text-white border-transparent shadow-md'
                          : 'bg-white text-slate border-hairline hover:border-indigo hover:text-indigo'
                      }`}
                    >
                      {email}
                    </button>
                  ))}
                </div>
              </div>

              {/* Subject & Body */}
              <div className="space-y-3">
                <h3 className="text-[11px] font-bold text-indigo uppercase tracking-wider">Email Content</h3>
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate">Subject</label>
                  <input
                    value={settings.subject}
                    onChange={e => setSettings(prev => ({ ...prev, subject: e.target.value }))}
                    className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate">Body line</label>
                  <textarea
                    rows={2}
                    value={settings.body_line}
                    onChange={e => setSettings(prev => ({ ...prev, body_line: e.target.value }))}
                    className="w-full bg-mist border border-hairline rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo focus:ring-2 focus:ring-indigo/10 transition resize-none"
                  />
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="px-5 py-4 bg-mist border-t border-hairline flex items-center justify-between">
              <button
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-white border border-hairline hover:bg-white/80 text-slate transition"
                onClick={resetSettings}
              >
                Reset
              </button>
              <button
                className="px-5 py-2 rounded-xl text-sm font-semibold bg-emerald-500 hover:bg-emerald-600 text-white shadow-md transition"
                onClick={saveSettings}
              >
                Save
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
