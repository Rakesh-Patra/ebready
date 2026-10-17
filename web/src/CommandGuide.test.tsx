// @vitest-environment jsdom
import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CommandGuide from './CommandGuide';

afterEach(cleanup);
it('starts with a short guide and keeps the complete professional reference collapsed',()=>{
 render(<CommandGuide/>);
 expect(screen.getByText('A few steps to your first deployment')).toBeTruthy();
 const summary=screen.getByText('Professional tools · all 24 commands + 15 infrastructure actions');
 expect(summary.parentElement?.hasAttribute('open')).toBe(false);
 expect(summary.parentElement?.querySelectorAll('.command-card').length).toBe(39);
});
it('lets professionals find database tools and explains an empty search',async()=>{
 const user=userEvent.setup();render(<CommandGuide/>);
 await user.click(screen.getByText('Professional tools · all 24 commands + 15 infrastructure actions'));
 await user.type(screen.getByLabelText('Find a command'),'database');
 expect(screen.getByText('ebkit operations database-migrate')).toBeTruthy();
 expect(screen.queryByText('ebkit swap')).toBeNull();
 await user.clear(screen.getByLabelText('Find a command'));
 await user.type(screen.getByLabelText('Find a command'),'no-such-command');
 expect(screen.getByRole('status').textContent).toContain('No commands found');
});
