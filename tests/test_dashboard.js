// Exercise the actual rendering functions without browser dependencies.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('gnvitop/dashboard.py','utf8');
const select = (a,b) => source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const context = vm.createContext({});
vm.runInContext(`let dashboardPreferences={hosts:{}}; let currentMode='normal';`+
 select('function escapeText(', 'function renderOrganizer(')+
 select('function usageClass(', 'function renderSummary(')+
 select('const openProcessDetails =', 'function renderHosts('),context);
const run = code => vm.runInContext(code,context);
run(`var host={alias:'test',user:'alice'}; var gpu={index:0,name:'Test GPU',memory_total_mb:10000,memory_used_mb:4000,memory_free_mb:6000,gpu_utilization_pct:60,temperature_c:45,power_draw_w:80,power_limit_w:200,processes:[{pid:1,user:'bob',gpu_memory_mb:1000,sm_utilization_pct:null,command:'python'},{pid:2,user:'alice',gpu_memory_mb:2000,sm_utilization_pct:40,command:'<script>bad</script>'}]};`);
let html=run('renderGPU(gpu,host)');
assert.match(html,/Your workload/); assert.match(html,/You 2.0 GB/);
assert.match(html,/Others 1000 MB/); assert.match(html,/Unattributed 1000 MB/);
assert.match(html,/80.0 W \/ 200 W/); assert.match(html,/width:40%/);
assert.match(html,/&lt;script&gt;/); assert.ok(!html.includes('<script>bad'));
assert.ok(html.indexOf('<td>You · alice')<html.indexOf('<td>bob'));
assert.match(html,/40%/); assert.match(html,/<td>N\/A<\/td>/);
run(`dashboardPreferences.hosts.test={my_users:['bob']}; currentMode='compact';`);
html=run('renderGPU(gpu,host)'); assert.match(html,/You 1000 MB/); assert.match(html,/80.0 W/);
run(`gpu.power_draw_w=null; gpu.processes=[];`);
html=run('renderGPU(gpu,host)'); assert.ok(!html.includes('Your workload')); assert.match(html,/Not reported by device/);
run(`gpu.processes=[{user:'bob',gpu_memory_mb:20000}];`);
html=run('renderMemory(gpu,myUsers(host),false)'); assert.match(html,/width:40%/); assert.match(html,/segments scaled/);
assert.ok(!html.includes('NaN'));
console.log('Dashboard ownership, power, escaping, unknown and sampling tests passed');
