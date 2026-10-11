import { useState } from 'react';
import { X, Boxes, ArrowRight, GitBranch, Cloud, ShieldCheck, LoaderCircle } from 'lucide-react';
import { insforge, message } from './lib';
import './auth.css';

export default function AuthDialog({ initialMode, close, authenticated }: { initialMode: 'login' | 'signup'; close: () => void; authenticated: () => Promise<void> }) {
  const [mode, setMode] = useState<string>(initialMode), [email, setEmail] = useState(''), [password, setPassword] = useState(''), [name, setName] = useState(''), [otp, setOtp] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      if (mode === 'signup') {
        const { data, error } = await insforge.auth.signUp({ email, password, name, redirectTo: `${window.location.origin}/` });
        if (error) throw error;
        if (data?.requireEmailVerification) { setMode('verify'); setPassword(''); setNotice('Check your email for the verification code.'); }
        else { await authenticated(); close(); }
      } else if (mode === 'verify') {
        const { error } = await insforge.auth.verifyEmail({ email, otp }); if (error) throw error;
        await authenticated(); close();
      } else if (mode === 'reset') {
        const { error } = await insforge.auth.sendResetPasswordEmail({ email }); if (error) throw error;
        setMode('reset-confirm'); setPassword(''); setNotice('If this account exists, a reset code has been emailed.');
      } else if (mode === 'reset-confirm') {
        const { data, error: exchangeError } = await insforge.auth.exchangeResetPasswordToken({ email, code: otp }); if (exchangeError) throw exchangeError;
        if (!data?.token) throw new Error('Reset code could not be verified. Request a new code.');
        const { error } = await insforge.auth.resetPassword({ newPassword: password, otp: data.token }); if (error) throw error;
        setPassword(''); setOtp(''); setMode('login'); setNotice('Password updated. Log in with your new password.');
      } else {
        const { error } = await insforge.auth.signInWithPassword({ email, password }); if (error) throw error;
        await authenticated(); close();
      }
    } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  async function google() { setBusy(true); setError(''); try { const { error } = await insforge.auth.signInWithOAuth('google', { redirectTo: `${window.location.origin}/` }); if (error) throw error; } catch (error) { setError(message(error)); setBusy(false); } }
  async function resend() { setBusy(true); setError(''); try { const { error } = await insforge.auth.resendVerificationEmail({ email }); if (error) throw error; setNotice('Verification email sent.'); } catch (error) { setError(message(error)); } finally { setBusy(false); } }
  const title = mode === 'signup' ? 'Create your account.' : mode === 'verify' ? 'Verify your email.' : mode.startsWith('reset') ? 'Reset your password.' : 'Welcome back.';
  return <div className="overlay top"><section className="modal auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-title"><aside className="auth-story"><div className="auth-brand"><span className="brand-mark"><Boxes size={23}/></span><strong>EBKit</strong></div><div className="auth-story-content"><span className="auth-kicker">FROM CODE TO CLOUD</span><h3>Your next idea.<br/>Already on its way.</h3><p>A workspace for your repositories, deployments, and everything you build next.</p><div className="auth-flow" aria-hidden="true"><span><GitBranch size={24}/></span><i/><span className="auth-flow-cloud"><Cloud size={28}/></span></div><div className="auth-story-note"><ShieldCheck size={17}/><span>One account. All your projects.</span></div></div><span className="auth-story-footer">Build something worth sharing.</span></aside><div className="auth-body"><div className="modal-heading"><span className="auth-kicker">{mode === 'signup' ? 'GET STARTED' : mode === 'login' ? 'YOUR WORKSPACE AWAITS' : 'ACCOUNT ACCESS'}</span><button className="icon-button" aria-label="Close authentication" disabled={busy} onClick={close}><X size={20}/></button></div><h2 id="auth-title">{title}</h2><p className="auth-description">{mode === 'signup' ? 'Create your workspace and bring your first project to the cloud.' : mode === 'login' ? 'Log in to manage your projects and deployments.' : mode === 'verify' ? 'Enter the six-digit code sent to your email.' : 'Recover access to your EBKit workspace.'}</p>
    {(mode === 'login' || mode === 'signup') && <><button className="button auth-google" disabled={busy} onClick={() => void google()}><span className="google-mark" aria-hidden="true">G</span>Continue with Google</button><div className="auth-divider"><span>or continue with email</span></div></>}
    <form onSubmit={event => void submit(event)}>
      {mode === 'signup' && <label>Your name<input autoComplete="name" placeholder="Your full name" required value={name} onChange={event => setName(event.target.value)}/></label>}
      <label>Email address<input type="email" autoComplete="email" placeholder="you@example.com" required disabled={mode === 'verify' || mode === 'reset-confirm'} value={email} onChange={event => setEmail(event.target.value)}/></label>
      {(mode === 'login' || mode === 'signup' || mode === 'reset-confirm') && <label>{mode === 'reset-confirm' ? 'New password' : 'Password'}<input type="password" placeholder="Enter your password" minLength={mode === 'login' ? undefined : 6} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required value={password} onChange={event => setPassword(event.target.value)}/></label>}
      {(mode === 'verify' || mode === 'reset-confirm') && <label>Email code<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={otp} onChange={event => setOtp(event.target.value)}/></label>}
      {notice && <p className="auth-notice" role="status">{notice}</p>}{error && <p className="form-error" role="alert">{error}</p>}
      <button className="button primary auth-submit" disabled={busy} type="submit">{busy && <LoaderCircle size={16} className="spin"/>}{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : mode === 'verify' ? 'Verify email' : mode === 'reset' ? 'Send reset code' : mode === 'reset-confirm' ? 'Save new password' : 'Log in'}{!busy && <ArrowRight size={16}/>}</button>
    </form>
    <div className="auth-links">{mode === 'login' ? <><button className="text-button" onClick={() => { setMode('signup'); setError(''); }}>Create an account</button><button className="text-button" onClick={() => { setMode('reset'); setError(''); }}>Forgot password?</button></> : mode === 'verify' ? <button className="text-button" disabled={busy} onClick={() => void resend()}>Resend code</button> : <button className="text-button" onClick={() => { setMode('login'); setError(''); }}>Back to log in</button>}</div>
  </div></section></div>;
}
