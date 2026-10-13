// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DoctorPanel from './DoctorPanel';
const api=vi.hoisted(()=>({backend:vi.fn()}));
vi.mock('./lib',()=>({...api,message:(error:Error)=>error.message}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const report={port:8080,planHash:'a'.repeat(64),scanComplete:true,findings:[{code:'loopback-binding',path:'app.py',line:3,message:'Use 0.0.0.0',fixable:true}],patches:[{path:'app.py',diff:'-localhost\n+0.0.0.0'}],verification:{static:'failed',build:'not_verified',runtime:'not_verified',security:'not_verified'}};
it('requires diff approval and invalidates preparation after applying repairs',async()=>{
  const user=userEvent.setup(),applied=vi.fn();api.backend.mockResolvedValueOnce(report).mockResolvedValueOnce({...report,findings:[],patches:[],applied:['app.py']});
  render(<DoctorPanel uploadId="upload" port={8080} disabled={false} onApplied={applied}/>);
  await user.click(screen.getByRole('button',{name:'Check deployment blockers'}));
  expect(screen.getByText('app.py:3')).toBeTruthy();expect(screen.getByText('Build: not verified')).toBeTruthy();
  expect((screen.getByRole('button',{name:'Apply reviewed repairs'}) as HTMLButtonElement).disabled).toBe(true);
  await user.click(screen.getByRole('checkbox',{name:/reviewed the proposed diffs/}));
  await user.click(screen.getByRole('button',{name:'Apply reviewed repairs'}));
  expect(api.backend).toHaveBeenLastCalledWith('/api/uploads/upload/doctor',{port:8080,apply:true,reviewed:true,planHash:report.planHash});
  expect(applied).toHaveBeenCalledOnce();expect(screen.getByText(/Repaired 1 file/)).toBeTruthy();
});
it('shows backend failures without enabling repairs',async()=>{
  api.backend.mockRejectedValue(new Error('Project changed after review.'));
  render(<DoctorPanel uploadId="upload" port={8080} disabled={false} onApplied={vi.fn()}/>);
  await userEvent.click(screen.getByRole('button',{name:'Check deployment blockers'}));
  expect(screen.getByRole('alert').textContent).toBe('Project changed after review.');
  expect(screen.queryByRole('button',{name:'Apply reviewed repairs'})).toBeNull();
});
