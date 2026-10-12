// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
const api = vi.hoisted(() => ({
  auth: { getCurrentUser: vi.fn(), onAuthStateChange: vi.fn((_callback: (event: string) => void) => () => {}), signInWithOAuth: vi.fn(), signOut: vi.fn() },
  database: { from: vi.fn() },
  backend: vi.fn(), getTrialAccount:vi.fn(),
  uploadZip: vi.fn(), downloadPreparedZip: vi.fn(),
}));
vi.mock('./lib', async original => ({ ...await original<typeof import('./lib')>(), insforge: api, backend: api.backend, getTrialAccount:api.getTrialAccount, uploadZip:api.uploadZip, downloadPreparedZip:api.downloadPreparedZip }));
vi.mock('@insforge/sdk', () => ({ createClient: () => ({}), AuthChangeEvent: { SIGNED_OUT: 'signedOut' } }));
import App from './App';

beforeEach(() => { vi.clearAllMocks();api.getTrialAccount.mockResolvedValue({approved:true,trialUsed:false,durationMinutes:30}); vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => ({ liveEnabled: true }) })); api.backend.mockResolvedValue({ dockerfile: 'Dockerfile' }); api.auth.getCurrentUser.mockResolvedValue({ data: { user: null }, error: null }); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
async function wizard() {
  const user = userEvent.setup(); render(<App/>);
  await user.click(screen.getByRole('button', { name: /Switch to demo/ }));
  await user.click(await screen.findByRole('button', { name: /New deployment/ }));
  await user.type(screen.getByLabelText('GitHub repository URL'), 'https://github.com/Rakesh-Patra/devboard.git');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /Check repository & continue/ }));
  return user;
}
describe('deployment dashboard', () => {
  it('requires the root Dockerfile acknowledgement', async () => {
    const user = userEvent.setup(); render(<App/>);
    await user.click(screen.getByRole('button', { name: /New deployment/ }));
    await user.type(screen.getByLabelText('GitHub repository URL'), 'https://github.com/a/b');
    expect(screen.queryByRole('button', { name: /Check repository & continue/ })).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: 'My repository has a root Dockerfile.' }));
    expect(screen.getByRole('button', { name: /Check repository & continue/ })).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: 'My repository has a root Dockerfile.' }));
    expect(screen.queryByRole('button', { name: /Check repository & continue/ })).toBeNull();
    expect(api.backend).not.toHaveBeenCalled();
  });
  it('opens separate login and registration dialogs', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(screen.getByRole('dialog', { name: 'Welcome back.' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Close authentication' }));
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(screen.getByRole('dialog', { name: 'Create your account.' })).toBeTruthy();
  });
  it('shows repository errors and stays on the first step', async () => {
    api.backend.mockRejectedValueOnce(new Error('No root Dockerfile found.'));
    const user = userEvent.setup(); render(<App/>);
    await user.click(screen.getByRole('button', { name: /New deployment/ }));
    await user.type(screen.getByLabelText('GitHub repository URL'), 'https://github.com/owner/repository'); await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /Check repository & continue/ }));
    expect(screen.getByRole('alert').textContent).toContain('No root Dockerfile found.');
    expect(screen.getByRole('dialog', { name: 'Start with your repository.' })).toBeTruthy();
  });
  it('requires login before the live deploy action', async () => {
    const user = userEvent.setup(); render(<App/>);
    await user.click(screen.getByRole('button', { name: /New deployment/ }));
    await user.type(screen.getByLabelText('GitHub repository URL'), 'https://github.com/owner/repository'); await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /Check repository & continue/ }));
    expect(screen.queryByRole('button', { name: 'Log in to start trial' })).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: /billable AWS resources/ }));
    expect(screen.getByRole('button', { name: 'Log in to start trial' })).toBeTruthy();
    await user.click(screen.getByRole('checkbox', { name: /billable AWS resources/ }));
    expect(screen.queryByRole('button', { name: 'Log in to start trial' })).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: /billable AWS resources/ }));
    await user.click(screen.getByRole('button', { name: 'Log in to start trial' }));
    expect(screen.getByRole('dialog', { name: 'Welcome back.' })).toBeTruthy();
    expect(api.backend.mock.calls.map(call => call[0])).toEqual(['/api/repository']);
  });
  it('prefills required database names and hides deploy until supplied', async () => {
    api.backend.mockResolvedValueOnce({environment:[{name:'POSTGRES_URL',required:true,source:'.env.example'}],environmentScanComplete:true});
    const user=userEvent.setup();render(<App/>);
    await user.click(screen.getByRole('button',{name:/New deployment/}));
    await user.type(screen.getByLabelText('GitHub repository URL'),'https://github.com/owner/devboard');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button',{name:/Check repository & continue/}));
    const input=screen.getByRole('textbox',{name:/Application environment/});
    expect((input as HTMLTextAreaElement).value).toBe('POSTGRES_URL=');
    await user.click(screen.getByRole('checkbox',{name:/billable AWS resources/}));
    expect(screen.queryByRole('button',{name:'Log in to start trial'})).toBeNull();
    await user.type(input,'postgresql://user:password@host/database');
    expect(screen.getByRole('button',{name:'Log in to start trial'})).toBeTruthy();
    await user.clear(input);
    expect(screen.queryByRole('button',{name:'Log in to start trial'})).toBeNull();
    expect(api.backend).toHaveBeenCalledTimes(1);
  });
  it('rejects real secret values entered in the name-only field', async () => {
    const user = await wizard();
    await user.type(screen.getByRole('textbox', { name: /Environment variable names/ }), 'API_KEY=secret');
    await user.click(screen.getByRole('button', { name: /Start demo deployment/ }));
    expect(screen.getByRole('alert').textContent).toMatch(/variable names only/);
    expect(api.database.from).not.toHaveBeenCalled();
  });
  it('creates a guest preview and requires its name to delete it', async () => {
    const user = await wizard();
    await user.click(screen.getByRole('button', { name: /Start demo deployment/ }));
    expect(screen.getByRole('dialog').textContent).toMatch(/simulated preview/);
    await user.click(screen.getByRole('button', { name: 'Settings' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete devboard?' });
    expect((within(dialog).getByRole('button', { name: 'Delete project' }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(within(dialog).getByRole('textbox'), 'devboard');
    await user.click(within(dialog).getByRole('button', { name: 'Delete project' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(api.database.from).not.toHaveBeenCalled();
  });
  it('starts Google sign-in with the exact application origin', async () => {
    api.auth.signInWithOAuth.mockResolvedValue({ error: null }); render(<App/>);
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }));
    expect(api.auth.signInWithOAuth).toHaveBeenCalledWith('google', { redirectTo: `${window.location.origin}/` });
  });
  it('reports sign-in failures', async () => {
    api.auth.signInWithOAuth.mockResolvedValue({ error: new Error('Unavailable') }); render(<App/>);
    await userEvent.click(await screen.findByRole('button', { name: /Sign in with Google/ }));
    expect(screen.getByRole('status').textContent).toMatch(/could not start/);
  });
  it('loads bounded project metadata for a signed-in user', async () => {
    api.auth.getCurrentUser.mockResolvedValue({ data: { user: { id: 'owner', email: 'test@example.test' } }, error: null });
    const limit = vi.fn().mockResolvedValue({ data: [], error: null });
    const order = vi.fn(() => ({ limit })); const select = vi.fn((_columns: string) => ({ order }));
    api.database.from.mockReturnValue({ select }); render(<App/>);
    await waitFor(() => expect(limit).toHaveBeenCalledWith(50));
    expect(api.database.from).toHaveBeenCalledWith('ebkit_web_projects');
    expect(select.mock.calls[0][0]).not.toContain('*');
    expect(await screen.findByText('Signed in')).toBeTruthy();
  });
  it('imports only names from .env and sends no guest data to the backend', async () => {
    const user = await wizard();
    const file = new File(['API_KEY=private-value\nDATABASE_URL=private-url'], '.env', { type: 'text/plain' });
    Object.defineProperty(file, 'text', { value: async () => 'API_KEY=private-value\nDATABASE_URL=private-url' });
    await user.upload(screen.getByLabelText('Import names from .env'), file);
    expect((screen.getByRole('textbox', { name: /Environment variable names/ }) as HTMLTextAreaElement).value).toBe('API_KEY\nDATABASE_URL');
    expect(document.body.textContent).not.toContain('private-value');
    expect(api.database.from).not.toHaveBeenCalled();
  });
  it('resumes a persisted demo after loading it', async () => {
    api.auth.getCurrentUser.mockResolvedValue({ data: { user: { id: 'owner' } }, error: null });
    const project = { id: 'project', user_id: 'owner', name: 'saved-demo', repository: 'https://github.com/a/b', region: 'us-east-2', port: 8080, status: 'deploying', stage: 5, replicas: 1, revision: 1, variable_names: [], created_at: new Date().toISOString() };
    const save = vi.fn().mockResolvedValue({ data: [{ id: 'project' }], error: null });
    const update = vi.fn(() => ({ eq: () => ({ select: save }) }));
    api.database.from.mockReturnValue({ select: () => ({ order: () => ({ limit: () => Promise.resolve({ data: [project], error: null }) }) }), update });
    render(<App/>); await screen.findByRole('button', { name: /saved-demo/ });
    await waitFor(() => expect(update).toHaveBeenCalledWith({ stage: 6, status: 'ready' }), { timeout: 3000 });
    expect(await screen.findByText('Preview ready')).toBeTruthy();
  });
  it('clears the account view after session expiry', async () => {
    api.auth.getCurrentUser.mockResolvedValue({ data: { user: { id: 'owner' } }, error: null });
    api.database.from.mockReturnValue({ select: () => ({ order: () => ({ limit: () => Promise.resolve({ data: [], error: null }) }) }) });
    render(<App/>); await screen.findByText('Signed in');
    const listener = api.auth.onAuthStateChange.mock.calls[0][0] as unknown as (event: string) => void;
    await act(async () => listener('signedOut'));
    expect(await screen.findByRole('button', { name: /Sign in with Google/ })).toBeTruthy();
  });
  async function failedProject(status = 'failed', repository = 'https://github.com/a/b') {
    api.auth.getCurrentUser.mockResolvedValue({ data: { user: { id: 'owner' } }, error: null });
    const project = { id: '11111111-1111-4111-8111-111111111111', user_id: 'owner', name: 'failed-app', repository, region: 'us-east-2', port: 8080, mode: 'live', operation_log: 'Build succeeded\nERROR: health check failed', status, stage: 2, replicas: 1, revision: 1, variable_names: [], created_at: new Date().toISOString(), operation_running: false };
    api.database.from.mockReturnValue({ select: () => ({ order: () => ({ limit: () => Promise.resolve({ data: [project], error: null }) }) }) });
    render(<App/>); await userEvent.click(await screen.findByRole('button', { name: /failed-app/ })); return project;
  }
  it('allows retrying a failed live deployment', async () => {
    const project = await failedProject();
    expect((screen.getByRole('button', { name: 'Redeploy in AWS' }) as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Redeploy in AWS' }));
    expect(api.backend).toHaveBeenCalledWith(`/api/projects/${project.id}/deploy`, { confirmCosts: true });
  });
  it('updates a ZIP project through its existing deployment endpoint', async()=>{
    const project=await failedProject('ready','zip:original.zip');const user=userEvent.setup();
    api.uploadZip.mockResolvedValue({uploadId:'replacement',port:3000});
    api.backend.mockImplementation(async(route:string)=>route.includes('/prepare')?{uploadId:'replacement',name:'updated.zip',dockerfile:'FROM node:22-alpine\nCMD ["node","app.js"]',reviewHash:'new-hash',generated:false,port:3000,environment:[],environmentScanComplete:true,scout:{gate_passed:true,summary:'Passed',details:[]}}:{});
    await user.click(screen.getByRole('button',{name:'Upload ZIP & redeploy'}));
    await user.upload(screen.getByLabelText('Project ZIP file'),new File(['zip'],'updated.zip',{type:'application/zip'}));
    await user.click(screen.getByRole('button',{name:'Detect project settings'}));
    await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));await user.click(screen.getByRole('checkbox',{name:/Allow AI/}));
    await user.click(screen.getByRole('button',{name:'Prepare & review Dockerfile'}));
    await screen.findByText('Existing root Dockerfile');await user.click(screen.getByRole('checkbox',{name:'I reviewed this root Dockerfile.'}));
    await user.click(screen.getByRole('button',{name:'Continue with reviewed ZIP'}));
    expect((screen.getByLabelText('Project name') as HTMLInputElement).value).toBe(project.name);
    await user.click(screen.getByRole('checkbox'));await user.click(screen.getByRole('button',{name:'Redeploy to AWS'}));
    await waitFor(()=>expect(api.backend).toHaveBeenCalledWith(`/api/projects/${project.id}/deploy`,expect.objectContaining({uploadId:'replacement',reviewHash:'new-hash',reviewed:true,port:3000,confirmCosts:true})));
    expect(api.backend.mock.calls.some(([route])=>route==='/api/deploy')).toBe(false);
  });
  it('lets a destroyed project start a fresh deployment with a new confirmation', async () => {
    const project = await failedProject('destroyed');
    expect((screen.getByRole('button', { name: 'Rollback in AWS' }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Deploy again' }));
    expect((screen.getByLabelText('GitHub repository URL') as HTMLInputElement).value).toBe(project.repository);
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    expect(api.backend).not.toHaveBeenCalled();
  });
  it('filters saved log output without issuing another cloud operation', async () => {
    await failedProject(); await userEvent.click(screen.getByRole('button', { name: 'Logs' }));
    expect(screen.getByRole('button', { name: 'Copy logs' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Download' })).toBeTruthy();
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter logs' }), 'error');
    expect(screen.getByText('ERROR: health check failed')).toBeTruthy();
    expect(screen.queryByText(/Build succeeded/)).toBeNull();
    expect(api.backend).not.toHaveBeenCalled();
  });
  it('opens version selection instead of silently disabling rollback', async () => {
    await failedProject(); await userEvent.click(screen.getByRole('button', { name: 'Rollback in AWS' }));
    expect(screen.getByLabelText('Rollback version label')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toMatch(/previously created version/);
    expect(api.backend).not.toHaveBeenCalled();
  });
});


it('ZIP wizard requires Dockerfile review and environment values before enabling AWS deployment', async()=>{
  const user=userEvent.setup();
  api.auth.getCurrentUser.mockResolvedValue({data:{user:{id:'owner',email:'owner@example.test'}},error:null});
  api.database.from.mockReturnValue({select:()=>({order:()=>({limit:async()=>({data:[],error:null})})})});
  api.uploadZip.mockResolvedValue({uploadId:'zip-one',port:3000});
  api.backend.mockResolvedValue({uploadId:'zip-one',name:'project.zip',dockerfile:'FROM node:22-alpine\nCMD ["node","app.js"]',reviewHash:'review-one',generated:true,port:3000,environment:[{name:'DATABASE_URL',required:true,source:'app.js'}],environmentScanComplete:true,scout:{gate_passed:true,summary:'Source dependency gate passed',details:[]}});
  render(<App/>);await screen.findByText('Signed in');
  await user.click(screen.getByRole('button',{name:/New deployment/}));
  await user.click(screen.getByRole('button',{name:'Upload ZIP'}));
  await user.upload(screen.getByLabelText('Project ZIP file'),new File(['zip'],'project.zip',{type:'application/zip'}));
  await user.click(screen.getByRole('button',{name:'Detect project settings'}));
  await user.click(screen.getByRole('checkbox',{name:'I checked the container port.'}));
  await user.click(screen.getByRole('checkbox',{name:/Allow AI/}));
  await user.click(screen.getByRole('button',{name:'Prepare & review Dockerfile'}));
  await screen.findByText('AI Dockerfile draft');
  expect(screen.queryByRole('button',{name:'Continue with reviewed ZIP'})).toBeNull();
  await user.click(screen.getByRole('checkbox',{name:'I reviewed this root Dockerfile.'}));
  await user.click(screen.getByRole('button',{name:'Continue with reviewed ZIP'}));
  expect((screen.getByLabelText('Container port') as HTMLInputElement).value).toBe('3000');
  expect(screen.queryByRole('button',{name:'Deploy to AWS'})).toBeNull();
  await user.click(screen.getByRole('checkbox'));
  expect(screen.queryByRole('button',{name:'Deploy to AWS'})).toBeNull();
  await user.clear(screen.getByRole('textbox',{name:/Application environment/}));
  await user.type(screen.getByRole('textbox',{name:/Application environment/}),'DATABASE_URL=postgresql://database.example/app');
  expect(screen.getByRole('button',{name:'Deploy to AWS'})).toBeTruthy();
  expect(api.backend).toHaveBeenCalledWith('/api/uploads/zip-one/prepare',{port:3000,allowAI:true});
});

it('shows the 30-minute approved trial and the Rs49 coming-soon pass without checkout',async()=>{
 render(<App/>);await userEvent.click(screen.getByRole('button',{name:'Pricing'}));
 expect(screen.getByText('30 minutes from Ready / Green.')).toBeTruthy();expect(screen.getByText(String.fromCharCode(8377)+'49')).toBeTruthy();
 expect((screen.getByRole('button',{name:'Coming soon'}) as HTMLButtonElement).disabled).toBe(true);
 expect(api.backend).not.toHaveBeenCalled();
});

it('provides a header sign-out control for the mobile layout',async()=>{
 api.auth.getCurrentUser.mockResolvedValue({data:{user:{id:'owner',email:'owner@example.test'}},error:null});
 api.auth.signOut.mockResolvedValue({error:null});
 api.database.from.mockReturnValue({select:()=>({order:()=>({limit:async()=>({data:[],error:null})})})});
 render(<App/>);await screen.findByText('Signed in');
 await userEvent.click(screen.getByRole('button',{name:'Sign out of account'}));
 expect(api.auth.signOut).toHaveBeenCalledOnce();expect(await screen.findByRole('button',{name:'Log in'})).toBeTruthy();
});
