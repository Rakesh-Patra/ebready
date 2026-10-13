// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GithubDoctor from './GithubDoctor';
const api=vi.hoisted(()=>({backend:vi.fn(),uploadZip:vi.fn(),downloadPreparedZip:vi.fn()}));
vi.mock('./lib',()=>({...api,message:(e:Error)=>e.message,parseRepository:(url:string)=>({url:url.replace(/\.git$/,'')})}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const snapshot={uploadId:'snapshot',port:8080,repository:'https://github.com/owner/repo',commit:'a'.repeat(40),name:'repo-aaaaaaaaaaaa.zip'};
const prepared={...snapshot,dockerfile:'FROM node:22-alpine\nCMD ["node","app.js"]',reviewHash:'hash',generated:false,environment:[],environmentScanComplete:true,scout:{gate_passed:true,summary:'passed',details:[]}};
it('imports GitHub without a ZIP upload and offers the reviewed snapshot for deployment',async()=>{
  const user=userEvent.setup(),onUse=vi.fn();api.backend.mockResolvedValueOnce(snapshot).mockResolvedValueOnce(prepared);
  render(<GithubDoctor repository="https://github.com/owner/repo.git" signedIn login={vi.fn()} onUse={onUse}/>);
  await user.click(screen.getByRole('button',{name:'Prepare Dockerfile from GitHub snapshot'}));
  expect(api.backend).toHaveBeenCalledWith('/api/repository/import',{repository:snapshot.repository});
  expect(screen.getByRole('button',{name:'Check deployment blockers'})).toBeTruthy();
  expect(screen.getByText('aaaaaaaaaaaa')).toBeTruthy();api.uploadZip.mockClear();
  await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));
  await user.click(screen.getByRole('checkbox',{name:/Allow AI/}));
  await user.click(screen.getByRole('button',{name:'Prepare & review Dockerfile'}));
  expect(api.backend).toHaveBeenLastCalledWith('/api/uploads/snapshot/prepare',{port:8080,allowAI:true});
  expect(screen.queryByRole('button',{name:'Use reviewed GitHub snapshot for deployment'})).toBeNull();
  await user.click(screen.getByRole('button',{name:'Review Dockerfile to continue'}));
  await user.click(screen.getByRole('checkbox',{name:'I reviewed this root Dockerfile.'}));
  await user.click(screen.getByRole('button',{name:'Use reviewed GitHub snapshot for deployment'}));
  expect(onUse).toHaveBeenCalledWith(prepared);expect(api.uploadZip).not.toHaveBeenCalled();
});
it('asks signed-out users to log in before import',async()=>{
  const login=vi.fn();render(<GithubDoctor repository={snapshot.repository} signedIn={false} login={login} onUse={vi.fn()}/>);
  await userEvent.click(screen.getByRole('button',{name:'Log in to prepare from GitHub'}));
  expect(login).toHaveBeenCalledOnce();expect(api.backend).not.toHaveBeenCalled();
});
