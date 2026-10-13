// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,render,screen} from '@testing-library/react';
import TrialCountdown from './TrialCountdown';
import type {Project} from './lib';
afterEach(()=>{cleanup();vi.useRealTimers();});
const now=new Date('2026-10-12T12:00:00Z');
const project={plan:'free_trial',trial_started_at:now.toISOString(),trial_expires_at:new Date(+now+1800000).toISOString(),trial_cleanup_state:'scheduled'} as Project;
it('counts down from the saved deadline and displays expiry',()=>{vi.useFakeTimers();vi.setSystemTime(now);render(<TrialCountdown project={project}/>);expect(screen.getByRole('timer').textContent).toContain('30:00');act(()=>vi.advanceTimersByTime(61000));expect(screen.getByRole('timer').textContent).toContain('28:59');act(()=>vi.advanceTimersByTime(1739000));expect(screen.getByRole('timer').textContent).toContain('Trial expired');});
it('labels and counts down the two-hour test pass',()=>{vi.useFakeTimers();vi.setSystemTime(now);render(<TrialCountdown project={{...project,plan:'paid_test',trial_expires_at:new Date(+now+7200000).toISOString()}}/>);expect(screen.getByRole('timer').textContent).toContain('Two-hour test pass · 120:00');});
it('distinguishes cleanup pending from verified removal',()=>{const {rerender}=render(<TrialCountdown project={{...project,trial_cleanup_state:'verifying'}}/>);expect(screen.getByText(/verification pending/)).toBeTruthy();rerender(<TrialCountdown project={{...project,trial_cleanup_state:'complete'}}/>);expect(screen.getByText(/cleanup verified/)).toBeTruthy();});
