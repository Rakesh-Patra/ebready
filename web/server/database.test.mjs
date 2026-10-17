import {expect,it} from 'vitest';
import {databasePlan,databaseRegions,validateSettings} from './database.mjs';
const user={id:'owner',email:'owner@example.test'};
const config={evaluatorEmails:[user.email],databaseTemplates:{'us-east-2':{clusterName:'eb-existing',subnetIds:['subnet-aaa','subnet-bbb']}}};
const variables=[{name:'POSTGRES_URL',required:true}];
it('requires persistent access and operator networking before offering RDS',()=>{
 expect(databaseRegions(config,user)).toEqual(['us-east-2']);
 expect(databaseRegions({...config,evaluatorEmails:[],testingEmails:[user.email]},user)).toEqual([]);
 expect(()=>databasePlan({mode:'managed',variables:['POSTGRES_URL'],confirmCosts:true},variables,{},config,{email:'other@example.test'},'us-east-2')).toThrow(/approved account/);
});
it('uses only the operator network plan and requires a cost confirmation',()=>{
 const choice={mode:'managed',variables:['POSTGRES_URL'],confirmCosts:true,clusterName:'attacker'};
 expect(databasePlan(choice,variables,{},config,user,'us-east-2')).toEqual({...config.databaseTemplates['us-east-2'],variables:['POSTGRES_URL']});
 expect(()=>databasePlan({...choice,confirmCosts:false},variables,{},config,user,'us-east-2')).toThrow(/charges/);
 expect(()=>databasePlan({...choice,variables:['MYSQL_URL']},variables,{},config,user,'us-east-2')).toThrow(/PostgreSQL/);
 expect(()=>databasePlan({...choice,variables:['DATABASE_URL']},variables,{},config,user,'us-east-2')).toThrow(/every required/);
 expect(()=>databasePlan(choice,variables,{POSTGRES_URL:'postgresql://db.example/app'},config,user,'us-east-2')).toThrow(/Remove existing/);
});
it.each(['localhost','127.0.0.1','0.0.0.0','[::1]'])('rejects a local database host %s before AWS',host=>{
 expect(()=>validateSettings({POSTGRES_URL:`postgresql://user:private@${host}/app`})).toThrow(/local address/);
});
it('does not include connection values in validation errors',()=>{
 try{validateSettings({POSTGRES_URL:'private-password'});}catch(error){expect(error.message).not.toContain('private-password');}
 expect(()=>validateSettings({POSTGRES_URL:'mysql://db.example/app'})).toThrow(/protocol/);
 expect(()=>validateSettings({KEY:'embedded"quote'})).toThrow(/Double quotes/);
 expect(()=>validateSettings([])).toThrow(/KEY=VALUE/);
 expect(validateSettings({POSTGRES_URL:'postgresql://user:pass@db.example/app?sslmode=require&x=1'})).toBeTruthy();
});
