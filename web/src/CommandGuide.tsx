import {useState} from 'react';

const commands = [
 ['init','Prepare your project','Generate deployment files and review your Dockerfile.','Prepare'],
 ['doctor','Check deployment readiness','Find source and Dockerfile issues. Add --verify --scout to build, run and scan locally.','Prepare'],
 ['deploy','Deploy or update an app','Build and deploy a GitHub repository or reviewed ZIP to AWS.','Deploy'],
 ['watch','Deploy GitHub changes automatically','Keep this command running to deploy new commits to the same environment.','Deploy'],
 ['status','Check deployment status','See the current version, health and application URL.','Monitor'],
 ['logs','Read application logs','Retrieve output to investigate startup and application errors.','Monitor'],
 ['health','Check app health','Inspect detailed environment health.','Monitor'],
 ['events','View recent activity','See deployment and infrastructure events.','Monitor'],
 ['diagnose','Investigate a problem','Collect diagnostic information for a deployment.','Monitor'],
 ['envlist','List environments','Find environments in your AWS account and region.','Monitor'],
 ['versions','List deployment versions','Find an existing version label before restoring a release.','Releases'],
 ['rollback','Restore a previous version','Deploy an existing version. Database migrations are not reversed.','Releases'],
 ['clone','Copy an environment','Create another environment; additional AWS charges apply.','Releases'],
 ['swap','Switch live traffic','Swap URLs between two existing ready environments.','Releases'],
 ['version-delete','Delete an unused version','Remove a version that is not used by an active environment.','Releases'],
 ['scale','Change app capacity','Set the number of running replicas. More replicas increase compute charges.','Settings'],
 ['config','Manage app variables','Read variable names or update application environment settings.','Settings'],
 ['options','Manage AWS options','Inspect or change detailed Elastic Beanstalk configuration.','Settings'],
 ['config-save','Save a configuration template','Save Standard environment settings as a named template.','Settings'],
 ['config-apply','Apply a configuration template','Apply a saved template to a Standard environment.','Settings'],
 ['resources','List AWS resources','Inspect infrastructure associated with the environment.','Cleanup'],
 ['cleanup-status','Check cleanup progress','Check what remains after an environment is terminated.','Cleanup'],
 ['destroy','Remove a deployment','Take the app offline and clean eligible resources. Persistent resources may remain.','Cleanup'],
 ['operations','Use infrastructure tools','Monitoring, databases, budgets and deployment workflow generation.','Infrastructure'],
];
const operations = [
 ['metrics','View performance history'],['alarm','Configure a monitoring alert'],['autoscale','Configure automatic scaling'],
 ['cost','View environment-tagged AWS spending'],['budget','Create a spending budget and email alert'],['leftovers','Find retained AWS resources'],
 ['database-create','Create a private PostgreSQL database'],['database-status','Check managed database status'],
 ['database-network','Grant application network access'],['database-attach','Pass database connection settings to the app'],
 ['database-check','Test the database connection'],['database-migrate','Run reviewed schema migrations'],
 ['database-snapshot','Back up the database'],['database-delete','Delete the managed database with a final snapshot'],
 ['pipeline','Generate a GitHub Actions deployment workflow'],
];
export default function CommandGuide(){
 const [query,setQuery]=useState(''),[copied,setCopied]=useState('');
 const matches=(...values:string[])=>values.join(' ').toLowerCase().includes(query.toLowerCase().trim());
 async function copy(command:string){try{await navigator.clipboard.writeText(command);setCopied(command);}catch{setCopied('Copy unavailable. Select the command text to copy it.');}}
 return <section className="command-guide" aria-label="CLI guide">
  <div className="guide-intro"><p className="eyebrow">START HERE</p><h2>A few steps to your first deployment</h2><p>Prefer clicking? Open Projects → New deployment, choose GitHub or ZIP, review your source, then deploy. Use the terminal steps below when deploying into your own AWS account.</p></div>
  <ol className="quick-start">
   <li><strong>Prepare your app</strong><p>Install EBKit, configure your AWS credentials locally, then generate and review your deployment files.</p><code>ebkit init --path ./my-project</code></li>
   <li><strong>Deploy to AWS</strong><p>Commit your reviewed root Dockerfile to GitHub. Replace the example URL with your public repository.</p><code>ebkit deploy https://github.com/OWNER/REPO</code><small>Deployment creates billable AWS resources.</small></li>
   <li><strong>Open and check your app</strong><p>Find your application URL with status. Use logs if the app does not start.</p><code>ebkit status</code><code>ebkit logs</code></li>
  </ol>
  <details className="settings-group update-guide"><summary>How do I update or remove my app?</summary><p>On the website, GitHub changes deploy automatically while the deployment is active. For ZIP projects, choose Upload ZIP &amp; redeploy.</p><p>In your terminal, run <code>ebkit watch REPOSITORY_URL</code> and leave it running for automatic GitHub updates, or run deploy again manually.</p><p>When finished, use <code>ebkit destroy</code>, then <code>ebkit cleanup-status</code>. Databases and shared infrastructure may remain billable.</p></details>
  <details className="settings-group command-reference"><summary>Professional tools · all 24 commands + 15 infrastructure actions</summary>
   <p>Find a task, then copy its help command for the complete options. These examples display help; they do not change AWS resources. In project Settings, expand Advanced AWS settings for the available web controls. Some changes require operator-approved access; the CLI uses your own AWS permissions.</p>
   <label>Find a command<input type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Try rollback, database, budget…"/></label>
   <div className="command-cards">{commands.filter(row=>matches(...row)).map(([name,title,description,group])=><article className="command-card" key={name}><small>{group}</small><h3>{title}</h3><code>ebkit {name}</code><p>{description}</p><button className="button small" onClick={()=>void copy(`ebkit ${name} --help`)}>Copy {name} help</button></article>)}
   {operations.filter(row=>matches(...row,'operations','infrastructure')).map(([name,title])=><article className="command-card" key={name}><small>Infrastructure action</small><h3>{title}</h3><code>ebkit operations {name}</code><button className="button small" onClick={()=>void copy(`ebkit operations ${name} --help`)}>Copy {name} help</button></article>)}</div>
   {!commands.some(row=>matches(...row))&&!operations.some(row=>matches(...row,'operations','infrastructure'))&&<p role="status">No commands found. Try deployment, health or database.</p>}
   <p className="field-help">For multiple environments, specify --app APP --environment ENV --region REGION. Use ebkit --profile PROFILE COMMAND to select AWS credentials. For approval-based CI/CD, configure GitHub required reviewers and stop the polling watcher for that environment.</p>
   {copied&&<p role="status">{copied.startsWith('ebkit')?`Copied: ${copied}`:copied}</p>}
  </details>
 </section>;
}
