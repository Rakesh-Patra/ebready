// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MobileMenu from './MobileMenu';
afterEach(cleanup);
it('opens account controls and closes after an action',async()=>{
 const onLogout=vi.fn(),user=userEvent.setup();render(<MobileMenu theme="dark" onTheme={vi.fn()} onLogin={vi.fn()} onSignup={vi.fn()} email="owner@example.test" onLogout={onLogout}/>);
 expect(screen.queryByText('owner@example.test')).toBeNull();await user.click(screen.getByLabelText('Open navigation menu'));
 expect(await screen.findByText('owner@example.test')).toBeTruthy();await user.click(screen.getByRole('button',{name:'Sign out'}));
 expect(onLogout).toHaveBeenCalledOnce();await waitFor(()=>expect(screen.queryByText('owner@example.test')).toBeNull());
});
it('provides login for guest cloud access and supports Escape',async()=>{
 const onMode=vi.fn(),user=userEvent.setup();render(<MobileMenu theme="light" onTheme={vi.fn()} onLogin={vi.fn()} onSignup={vi.fn()} onMode={onMode} mode="demo"/>);
 await user.click(screen.getByLabelText('Open navigation menu'));await user.click(await screen.findByRole('button',{name:'Log in for AWS'}));expect(onMode).toHaveBeenCalledOnce();
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Log in for AWS'})).toBeNull());
 await user.click(screen.getByLabelText('Open navigation menu'));await screen.findByRole('button',{name:'Log in'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Log in'})).toBeNull());expect(document.activeElement).toBe(screen.getByLabelText('Open navigation menu'));
});
