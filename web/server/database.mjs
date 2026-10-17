// Database choice validation happens before reserving a project or starting AWS.
const postgres=new Set(['DATABASE_URL','POSTGRES_URL','POSTGRESQL_URL']);
const protocols={DATABASE_URL:['postgres:','postgresql:','mysql:','mongodb:','mongodb+srv:','sqlite:'],POSTGRES_URL:['postgres:','postgresql:'],POSTGRESQL_URL:['postgres:','postgresql:'],MYSQL_URL:['mysql:'],MONGODB_URI:['mongodb:','mongodb+srv:'],MONGO_URI:['mongodb:','mongodb+srv:'],REDIS_URL:['redis:','rediss:'],DB_URL:['postgres:','postgresql:','mysql:','mongodb:','mongodb+srv:']};
const fail=(message,status=400)=>Object.assign(new Error(message),{status});
export function validateSettings(values={}){
 if(!values||typeof values!=='object'||Array.isArray(values))throw fail('Application settings must be a KEY=VALUE object.');
 const entries=Object.entries(values);
 if(entries.length>100||entries.some(([key,value])=>!/^[_A-Za-z][_A-Za-z0-9]*$/.test(key)||typeof value!=='string'||/[\r\n\0"]/.test(value)||value.length>4096))throw fail('Use up to 100 single-line application settings. Double quotes inside values are not supported.');
 for(const [name,value] of entries){
  if(!protocols[name]||!value.trim())continue;
  let url;try{url=new URL(value);}catch{throw fail(`${name} must contain a valid connection URL. Its value was not printed.`);}
  if(!protocols[name].includes(url.protocol))throw fail(`${name} uses an unsupported database protocol.`);
  if(url.protocol==='sqlite:')throw fail('Local SQLite files do not provide durable shared storage for this hosted deployment. Configure an external database supported by your app.');
  if(!url.hostname||/^(localhost|.*\.localhost|127\..*|0\.0\.0\.0|\[::1\])$/i.test(url.hostname))throw fail(`${name} points to a local address. Use a database reachable from the deployed AWS application.`);
 }
 return values;
}
export function databaseRegions(config,user){
 const email=user.email?.toLowerCase();
 const persistent=(config.evaluatorEmails??[]).includes(email)||(config.trialsEnabled===false&&(config.testingEmails??[]).includes(email));
 return persistent?Object.keys(config.databaseTemplates??{}).filter(region=>config.databaseTemplates[region]?.clusterName&&Array.isArray(config.databaseTemplates[region]?.subnetIds)):[];
}
export function databasePlan(choice,variables,values,config,user,region){
 if(choice===undefined||choice?.mode==='existing')return null;
 if(!choice||choice.mode!=='managed')throw fail('Choose an existing database or managed PostgreSQL.');
 if(!databaseRegions(config,user).includes(region))throw fail('Managed PostgreSQL requires operator-configured networking and an approved account without a trial deadline. Use an existing database URL.',403);
 if(choice.confirmCosts!==true)throw fail('Confirm the separate, persistent RDS database charges.');
 const names=choice.variables;
 if(!Array.isArray(names)||!names.length||names.some(name=>typeof name!=='string'||!postgres.has(name))||new Set(names).size!==names.length)throw fail('Choose the PostgreSQL variable names your application reads.');
 const expected=variables.filter(item=>item.required&&postgres.has(item.name)).map(item=>item.name);
 if(expected.some(name=>!names.includes(name)))throw fail('Include every required PostgreSQL connection variable in the managed database choice.');
 if(names.some(name=>values[name]?.trim()))throw fail('Remove existing URLs for the variables supplied by managed PostgreSQL.');
 return {...config.databaseTemplates[region],variables:names};
}
