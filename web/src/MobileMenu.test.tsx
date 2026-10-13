// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MobileMenu from './MobileMenu';
afterEach(cleanup);
it('contains navigation only and closes after selecting a destination',async()=>{
 const onSelect=vi.fn(),user=userEvent.setup();render(<MobileMenu items={[{label:'Projects',onSelect},{label:'Pricing',href:'#pricing'}]}/>);
 expect(screen.queryByRole('button',{name:'Projects'})).toBeNull();await user.click(screen.getByLabelText('Open navigation menu'));
 await screen.findByRole('button',{name:'Projects'});expect(screen.queryByRole('button',{name:'Log in'})).toBeNull();expect(screen.queryByRole('button',{name:/light mode/})).toBeNull();
 await user.click(screen.getByRole('button',{name:'Projects'}));expect(onSelect).toHaveBeenCalledOnce();await waitFor(()=>expect(screen.queryByRole('button',{name:'Projects'})).toBeNull());
});
it('closes with Escape and returns keyboard focus to the menu',async()=>{
 const user=userEvent.setup();render(<MobileMenu items={[{label:'Pricing',href:'#pricing'}]}/>);
 await user.click(screen.getByLabelText('Open navigation menu'));await screen.findByRole('link',{name:'Pricing'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.queryByRole('link',{name:'Pricing'})).toBeNull());expect(document.activeElement).toBe(screen.getByLabelText('Open navigation menu'));
});
