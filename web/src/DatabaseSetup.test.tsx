// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DatabaseSetup,{databaseProblem} from './DatabaseSetup';
afterEach(cleanup);
const props={names:['POSTGRES_URL'],required:true,text:'POSTGRES_URL=',onText:vi.fn(),mode:'existing' as const,onMode:vi.fn(),managedAllowed:false,confirmed:false,onConfirm:vi.fn(),replacement:false};
it('explains existing URLs and disables RDS on short access',()=>{
 render(<DatabaseSetup {...props}/>);
 expect(screen.getByLabelText('Connection URL (POSTGRES_URL)')).toBeTruthy();
 expect((screen.getByRole('option',{name:'Create AWS PostgreSQL before deployment'}) as HTMLOptionElement).disabled).toBe(true);
 expect(screen.getByText(/Short trials use an existing/)).toBeTruthy();
});
it('removes pasted URLs when selecting managed creation and requires explicit acknowledgement',async()=>{
 const user=userEvent.setup(),onText=vi.fn(),onMode=vi.fn(),onConfirm=vi.fn();
 const view=render(<DatabaseSetup {...props} text={'POSTGRES_URL=postgresql://db.example/app\nOTHER=keep'} managedAllowed onText={onText} onMode={onMode} onConfirm={onConfirm}/>);
 await user.selectOptions(screen.getByLabelText('Database option'),'managed');
 expect(onMode).toHaveBeenCalledWith('managed');expect(onText).toHaveBeenCalledWith('OTHER=keep');
 view.rerender(<DatabaseSetup {...props} mode="managed" managedAllowed onConfirm={onConfirm}/>);
 expect(screen.queryByLabelText('Connection URL (POSTGRES_URL)')).toBeNull();
 await user.click(screen.getByRole('checkbox'));
 expect(onConfirm).toHaveBeenLastCalledWith(true);
});
it('blocks unsupported schemes and localhost without displaying passwords',()=>{
 expect(databaseProblem({POSTGRES_URL:'postgresql://secret@localhost/db'})).toContain('localhost');
 expect(databaseProblem({POSTGRES_URL:'my-secret'})).not.toContain('my-secret');
 expect(databaseProblem({POSTGRES_URL:'postgresql://user:pass@db.example/app'})).toBe('');
});
