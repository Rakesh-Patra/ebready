// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ZipSource from './ZipSource';
const api=vi.hoisted(()=>({backend:vi.fn(),uploadZip:vi.fn(),downloadPreparedZip:vi.fn()}));
vi.mock('./lib',()=>({...api,message:(error:Error)=>error.message}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const draft={uploadId:'upload',name:'app.zip',dockerfile:'FROM node:22-alpine\nCMD ["node","app.js"]',reviewHash:'hash',generated:true,port:3000,environment:[],environmentScanComplete:true,scout:{gate_passed:true,summary:'Source gate passed',details:[]}};
it('requires consent before preparing a ZIP and passes the draft for review',async()=>{
  const user=userEvent.setup(),onPrepared=vi.fn();api.uploadZip.mockResolvedValue({uploadId:'upload'});api.backend.mockResolvedValue(draft);
  render(<ZipSource prepared={null} onPrepared={onPrepared} reviewed={false} onReviewed={vi.fn()} signedIn login={vi.fn()} port={3000} onPort={vi.fn()}/>);
  await user.upload(screen.getByLabelText('Project ZIP file'),new File(['zip'],'app.zip',{type:'application/zip'}));
  expect(screen.queryByRole('button',{name:'Prepare & review Dockerfile'})).toBeNull();
  await user.click(screen.getByRole('button',{name:'Detect project settings'}));
  await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));
  await user.click(screen.getByRole('checkbox',{name:/Allow AI/}));await user.click(screen.getByRole('button',{name:'Prepare & review Dockerfile'}));
  expect(api.backend).toHaveBeenCalledWith('/api/uploads/upload/prepare',{port:3000,allowAI:true});expect(onPrepared).toHaveBeenCalledWith(draft);
});
it('shows the actual Dockerfile and hides the prepared ZIP download until reviewed',async()=>{
  const user=userEvent.setup(),onReviewed=vi.fn();render(<ZipSource prepared={draft} onPrepared={vi.fn()} reviewed={false} onReviewed={onReviewed} signedIn login={vi.fn()} port={3000} onPort={vi.fn()}/>);
  expect(document.querySelector('pre')?.textContent).toBe(draft.dockerfile);expect(screen.queryByRole('button',{name:'Download prepared ZIP'})).toBeNull();
  await user.click(screen.getByRole('button',{name:'Review Dockerfile to continue'}));
  await user.click(screen.getByRole('checkbox'));expect(onReviewed).toHaveBeenCalledWith(true);
});
it.each([['gordon','Docker AI (Gordon) draft'],['gemini','Gemini draft']] as const)('identifies the %s generator on the review screen',(generator,title)=>{
  render(<ZipSource prepared={{...draft,generator}} onPrepared={vi.fn()} reviewed={false} onReviewed={vi.fn()} signedIn login={vi.fn()} port={3000} onPort={vi.fn()}/>);
  expect(screen.getByText(title)).toBeTruthy();
});
it('shows Scout findings and requires explicit acknowledgement before a bypass action appears',async()=>{
  const user=userEvent.setup();render(<ZipSource prepared={{...draft,scout:{gate_passed:false,summary:'1 Critical, 7 High',details:[]}}} onPrepared={vi.fn()} reviewed={false} onReviewed={vi.fn()} signedIn login={vi.fn()} port={3000} onPort={vi.fn()}/>);
  expect(screen.getByText('Update vulnerable dependencies, then upload a new ZIP. Deployment is blocked.')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'Prepare with Scout bypass'})).toBeNull();
  await user.click(screen.getByText('Advanced options'));
  await user.click(screen.getByRole('checkbox',{name:/explicitly choose to bypass Scout/}));
  expect(screen.getByRole('button',{name:'Prepare with Scout bypass'})).toBeTruthy();
});

it('fills the detected port before preparation and requires review again after an edit',async()=>{
 const user=userEvent.setup();api.uploadZip.mockResolvedValue({uploadId:'upload',port:8000});api.backend.mockResolvedValue(draft);
 function Harness(){const [port,setPort]=useState(8080);return <ZipSource prepared={null} onPrepared={vi.fn()} reviewed={false} onReviewed={vi.fn()} signedIn login={vi.fn()} port={port} onPort={setPort}/>;}
 render(<Harness/>);await user.upload(screen.getByLabelText('Project ZIP file'),new File(['zip'],'app.zip',{type:'application/zip'}));
 await user.click(screen.getByRole('button',{name:'Detect project settings'}));
 expect((screen.getByLabelText('Container port for ZIP') as HTMLInputElement).value).toBe('8000');expect(api.backend).not.toHaveBeenCalled();
 await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));await user.click(screen.getByRole('checkbox',{name:/Allow AI/}));
 await user.clear(screen.getByLabelText('Container port for ZIP'));await user.type(screen.getByLabelText('Container port for ZIP'),'3000');
 expect(screen.queryByRole('button',{name:'Prepare & review Dockerfile'})).toBeNull();
 await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));await user.click(screen.getByRole('button',{name:'Prepare & review Dockerfile'}));
 expect(api.backend).toHaveBeenCalledWith('/api/uploads/upload/prepare',{port:3000,allowAI:true});expect(api.uploadZip).toHaveBeenCalledOnce();
});
