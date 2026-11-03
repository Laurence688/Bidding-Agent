import {build,transformWithEsbuild} from 'vite';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('.',import.meta.url));
export async function generateTools(){

 const css=await readFile(root+'tools/tools.css','utf8');
 const bundle=async name=>{const result=await build({configFile:false,logLevel:'error',publicDir:false,build:{write:false,minify:true,target:'es2022',lib:{entry:root+'tools/'+name+'.ts',name:'LauraOffline',formats:['iife']}}});return (Array.isArray(result)?result:[result]).flatMap(r=>r.output).filter(x=>x.type==='chunk').map(x=>x.code).join('\n');};
 const escape=code=>code.replace(/<\/script/gi,'<\\/script');
 const oldCore=(await transformWithEsbuild(await readFile(root+'tools/legacy-core.ts','utf8'),'legacy-core.ts',{loader:'ts',target:'es2022'})).code;
 const oldUI=(await transformWithEsbuild(await readFile(root+'tools/legacy-ui.ts','utf8'),'legacy-ui.ts',{loader:'ts',target:'es2022'})).code;
 let buffer=await readFile(root+'tools/buffer.template.html','utf8');
 buffer=buffer.replace('</head>','<style>'+css+'</style></head>').replace('</body>','<script id="core-source">'+escape(oldCore)+'</script><script>'+escape(oldUI)+'</script><script>'+escape(await bundle('buffer-app'))+'</script></body>');
 const forward='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; script-src &apos;unsafe-inline&apos; blob:; style-src &apos;unsafe-inline&apos;; img-src data:; worker-src blob:; connect-src &apos;none&apos;; base-uri &apos;none&apos;; form-action &apos;none&apos;"><title>包转发率计算器</title><style>'+css+'</style></head><body><main id="forward-app" class="network-tool"></main><script>'+escape(await bundle('forwarding-app'))+'</script></body></html>';
 return {'port-buffer.html':buffer,'packet-forwarding.html':forward};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){await mkdir(root+'dist/tools',{recursive:true});for(const [name,html] of Object.entries(await generateTools()))await writeFile(root+'dist/tools/'+name,html);}
