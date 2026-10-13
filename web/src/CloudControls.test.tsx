// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,fireEvent,act} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CloudControls from './CloudControls';
import type {Project} from './lib';
vi.mock('./lib',async importOriginal=>({...await importOriginal<typeof import('./lib')>(),backend:vi.fn(async()=>({profiles:['operator']}))}));
afterEach(cleanup);
const project={user_id:'owner',repository:'https://github.com/a/b',stage:6,created_at:new Date().toISOString(),id:'one',mode:'live',status:'ready',name:'app',application_name:'app',environment_name:'source-env',region:'us-east-2',variable_names:[],port:8080,revision:1,replicas:1} as Project;
it('shows read controls to ordinary users and hides advanced mutations',()=>{
 render(<CloudControls project={project} projects={[project]} privileged={false} busy={false} onAction={vi.fn()}/>);
 expect(screen.getByRole('button',{name:'Detailed health'})).toBeTruthy();expect(screen.queryByRole('button',{name:'Clone environment'})).toBeNull();
});
it('requires exact confirmation and sends variable changes',async()=>{
 const action=vi.fn(async()=>{}),user=userEvent.setup();render(<CloudControls project={project} projects={[project]} privileged={false} busy={false} onAction={action}/>);
 await user.click(screen.getByText('Application environment variables'));await user.type(screen.getByLabelText('Settings to merge (KEY=VALUE)'),'NEW=value');
 expect((screen.getByRole('button',{name:'Apply variable changes'}) as HTMLButtonElement).disabled).toBe(true);
 await user.type(screen.getByLabelText('Confirm changes by typing the environment name'),'source-env');await user.click(screen.getByRole('button',{name:'Apply variable changes'}));
 expect(action).toHaveBeenCalledWith(project,'config',{environment:{NEW:'value'},unset:[],confirmation:'source-env'});
});
it('requires cost acknowledgement for clones and sends an owned swap ID',async()=>{
 const action=vi.fn(async()=>{}),user=userEvent.setup();const green={...project,id:'green',name:'green',environment_name:'green-env'};
 render(<CloudControls project={project} projects={[project,green]} privileged busy={false} onAction={action}/>);
 await user.type(screen.getByLabelText('Confirm changes by typing the environment name'),'source-env');await user.click(screen.getByText('Clone and blue/green deployment'));
 await user.type(screen.getByLabelText('Clone project name'),'clone-app');expect((screen.getByRole('button',{name:'Clone environment'}) as HTMLButtonElement).disabled).toBe(true);
 await user.click(screen.getByRole('checkbox'));await user.click(screen.getByRole('button',{name:'Clone environment'}));
 expect(action).toHaveBeenCalledWith(project,'clone',{name:'clone-app',confirmCosts:true,confirmation:'source-env'});
 await user.selectOptions(screen.getByLabelText('Swap URL with'),'green');await user.click(screen.getByRole('button',{name:'Swap environment URLs'}));
 expect(action).toHaveBeenCalledWith(project,'swap',{destinationId:'green',confirmation:'source-env'});
});
it('selects an operator-approved credentials profile',async()=>{
 const action=vi.fn(async()=>{}),user=userEvent.setup();render(<CloudControls project={project} projects={[project]} privileged busy={false} onAction={action}/>);
 await user.selectOptions(await screen.findByLabelText('AWS credentials profile'),'operator');await user.click(screen.getByRole('button',{name:'Detailed health'}));
 expect(action).toHaveBeenCalledWith(project,'health',{profile:'operator',confirmation:''});
});

it('uses the selected credentials profile for production operations',async()=>{
 const action=vi.fn(async()=>{}),user=userEvent.setup();render(<CloudControls project={project} projects={[project]} privileged busy={false} onAction={action}/>);
 await user.selectOptions(await screen.findByLabelText('AWS credentials profile'),'operator');await user.click(screen.getByRole('button',{name:'Run production operation'}));
 expect(action).toHaveBeenCalledWith(project,'production',{action:'metrics',configuration:{hours:24},confirmation:'',profile:'operator'});
});

it('keeps monitoring scheduled across parent renders and uses the latest callback',async()=>{
 const first=vi.fn(async()=>{}),second=vi.fn(async()=>{});
 const view=render(<CloudControls project={project} projects={[project]} privileged busy={false} onAction={first}/>);
 await screen.findByLabelText('AWS credentials profile');vi.useFakeTimers();
 try{
  fireEvent.change(screen.getByLabelText('Refresh monitoring'),{target:{value:'events'}});
  act(()=>vi.advanceTimersByTime(20000));
  view.rerender(<CloudControls project={project} projects={[project]} privileged busy={false} onAction={second}/>);
  act(()=>vi.advanceTimersByTime(10000));
  expect(first).not.toHaveBeenCalled();expect(second).toHaveBeenCalledWith(project,'events',{});
 }finally{vi.useRealTimers();}
});
