// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import OperationsPanel from './OperationsPanel';
import type {Project} from './lib';

afterEach(cleanup);
const project={id:'one',environment_name:'app-env',status:'ready'} as Project;
it('loads matching settings for an operation and confirms resource changes',async()=>{
 const user=userEvent.setup(),onAction=vi.fn(async()=>{});
 render(<OperationsPanel project={project} privileged busy={false} onAction={onAction}/>);
 await user.selectOptions(screen.getByLabelText('Production operation'),'budget');
 expect((screen.getByLabelText('Operation settings (JSON)') as HTMLTextAreaElement).value).toContain('monthlyUsd');
 expect((screen.getByRole('button',{name:'Run selected tool'}) as HTMLButtonElement).disabled).toBe(true);
 await user.type(screen.getByLabelText('Confirm production changes'),'app-env');
 expect((screen.getByRole('button',{name:'Run selected tool'}) as HTMLButtonElement).disabled).toBe(false);
 expect(onAction).not.toHaveBeenCalled();
});
it('shows backend failures inside the operation form',async()=>{
 const user=userEvent.setup();
 render(<OperationsPanel project={project} privileged busy={false} onAction={vi.fn(async()=>{throw new Error('AWS request failed');})}/>);
 await user.click(screen.getByRole('button',{name:'Run selected tool'}));
 expect((await screen.findByRole('alert')).textContent).toBe('AWS request failed');
});
