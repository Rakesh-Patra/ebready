// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
const auth = vi.hoisted(() => ({ signUp: vi.fn(), signInWithPassword: vi.fn(), verifyEmail: vi.fn(), sendResetPasswordEmail: vi.fn(), exchangeResetPasswordToken: vi.fn(), resetPassword: vi.fn() }));
vi.mock('./lib', () => ({ insforge: { auth }, message: (error: Error) => error.message }));
import AuthDialog from './AuthDialog';
beforeEach(() => vi.clearAllMocks()); afterEach(cleanup);
it('logs in with email and password and loads the authenticated account', async () => {
  auth.signInWithPassword.mockResolvedValue({ error: null }); const authenticated = vi.fn(), close = vi.fn();
  render(<AuthDialog initialMode="login" close={close} authenticated={authenticated}/>);
  await userEvent.type(screen.getByLabelText('Email address'), 'test@example.test');
  await userEvent.type(screen.getByLabelText('Password'), 'secure-password');
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
  expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: 'test@example.test', password: 'secure-password' }); expect(authenticated).toHaveBeenCalled(); expect(close).toHaveBeenCalled();
});
it('registers, verifies the email code and opens the account', async () => {
  auth.signUp.mockResolvedValue({ data: { requireEmailVerification: true }, error: null }); auth.verifyEmail.mockResolvedValue({ error: null }); const authenticated = vi.fn();
  render(<AuthDialog initialMode="signup" close={() => {}} authenticated={authenticated}/>);
  await userEvent.type(screen.getByLabelText('Your name'), 'Tester'); await userEvent.type(screen.getByLabelText('Email address'), 'test@example.test'); await userEvent.type(screen.getByLabelText('Password'), 'secure-password');
  await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
  await userEvent.type(await screen.findByLabelText('Email code'), '123456'); await userEvent.click(screen.getByRole('button', { name: 'Verify email' }));
  expect(auth.verifyEmail).toHaveBeenCalledWith({ email: 'test@example.test', otp: '123456' }); expect(authenticated).toHaveBeenCalled();
});
it('exchanges the reset code before saving the new password', async () => {
  auth.sendResetPasswordEmail.mockResolvedValue({ error: null }); auth.exchangeResetPasswordToken.mockResolvedValue({ data: { token: 'reset-token' }, error: null }); auth.resetPassword.mockResolvedValue({ error: null });
  render(<AuthDialog initialMode="login" close={() => {}} authenticated={async () => {}}/>);
  await userEvent.type(screen.getByLabelText('Email address'), 'test@example.test'); await userEvent.click(screen.getByRole('button', { name: 'Forgot password?' })); await userEvent.click(screen.getByRole('button', { name: 'Send reset code' }));
  await userEvent.type(await screen.findByLabelText('New password'), 'new-secure-password'); await userEvent.type(screen.getByLabelText('Email code'), '123456'); await userEvent.click(screen.getByRole('button', { name: 'Save new password' }));
  expect(auth.exchangeResetPasswordToken).toHaveBeenCalledWith({ email: 'test@example.test', code: '123456' }); expect(auth.resetPassword).toHaveBeenCalledWith({ newPassword: 'new-secure-password', otp: 'reset-token' });
});
it('shows backend authentication failures', async () => {
  auth.signInWithPassword.mockResolvedValue({ error: new Error('Invalid email or password') }); render(<AuthDialog initialMode="login" close={() => {}} authenticated={async () => {}}/>);
  await userEvent.type(screen.getByLabelText('Email address'), 'test@example.test'); await userEvent.type(screen.getByLabelText('Password'), 'wrong-password'); await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
  expect(screen.getByRole('alert').textContent).toContain('Invalid email or password');
});
