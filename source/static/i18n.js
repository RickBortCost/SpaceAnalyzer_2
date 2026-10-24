/* Operator-interface localization. Protocol values and operator data stay opaque. */
(() => {
  'use strict';
  const catalogs=window.SPACE_ANALYZER_MESSAGES||{},supported=['en','it','de','fr','es'];
  const normalize=s=>String(s).replace(/\s+/g,' ').trim(),reverse=new Map();
  for(const catalogue of Object.values(catalogs))for(const [source,target] of Object.entries(catalogue))if(!reverse.has(normalize(target)))reverse.set(normalize(target),source);
  const meta=document.querySelector('meta[name="space-analyzer-language"]');
  let language=supported.includes(meta?.content)?meta.content:'it',revision=0,pending=Promise.resolve();
  const bindings=new WeakMap(),composites=new Map(),locales={en:'en-GB',it:'it-IT',de:'de-DE',fr:'fr-FR',es:'es-ES'};
  const sourceOf=text=>Object.hasOwn(catalogs.it||{},normalize(text))?normalize(text):reverse.get(normalize(text));
  function t(text){
    if(typeof text!=='string')return text;
    const source=sourceOf(text);if(!source)return text;
    const translated=language==='it'?source:catalogs[language]?.[source]||source;
    return (text.match(/^\s*/)?.[0]||'')+translated+(text.match(/\s*$/)?.[0]||'');
  }
  function join(parts){
    const text=parts.map(p=>p.text!==undefined?t(p.text):String(p.value)).join('');
    if(composites.has(text))composites.delete(text);composites.set(text,parts);
    if(composites.size>2048)composites.delete(composites.keys().next().value);
    return text;
  }
  function message(value){
    const parts=composites.get(value);
    return parts?join(parts):t(value);
  }
  function remember(node,key,render,ui,value){
    let map=bindings.get(node);if(!map){map=new Map();bindings.set(node,map);node.setAttribute('data-i18n-dynamic','');}
    map.set(key,{render,ui,initial:value,parts:ui?composites.get(value):null});
  }
  function bind(node,property,render,ui=false){
    const value=render();node[property]=ui?message(value):value;
    remember(node,property,render,ui,value);return value;
  }
  function attr(node,name,render){
    const value=render();node.setAttribute(name,message(value));remember(node,'@'+name,render,true,value);
  }
  function option(render,value,defaultSelected=false,selected=false,ui=false){
    const element=new Option('',value,defaultSelected,selected);
    bind(element,'textContent',render,ui||['auto','manual','left','right','yes','no'].includes(String(value)));
    return element;
  }
  function refresh(){
    document.documentElement.lang=language;
    for(const node of document.querySelectorAll('[data-i18n]'))node.textContent=t(node.getAttribute('data-i18n'));
    for(const name of ['title','aria-label','placeholder'])for(const node of document.querySelectorAll('[data-i18n-'+name+']'))node.setAttribute(name,t(node.getAttribute('data-i18n-'+name)));
    for(const node of document.querySelectorAll('[data-i18n-dynamic]')){
      for(const [property,record] of bindings.get(node)||[]){
        try{const value=record.render(),text=record.ui?(record.parts&&value===record.initial?join(record.parts):message(value)):value;if(property[0]==='@')node.setAttribute(property.slice(1),text);else node[property]=text;}catch{/* A removed control may refer to an obsolete chart. */}
      }
    }
    const select=document.getElementById('ui-language');if(select){select.value=language;select.setAttribute('aria-label',t('Lingua'));}
    document.dispatchEvent(new CustomEvent('space-analyzer-languagechange',{detail:{language}}));
  }
  function apply(code,{user=false}={}){
    if(!supported.includes(code))throw new Error('Unsupported interface language');
    if(user)revision++;language=code;
    try{localStorage.setItem('space-analyzer.language',code);}catch{}
    refresh();return code;
  }
  async function save(code){
    const response=await fetch('/api/ui/language',{method:'POST',headers:{'Content-Type':'application/json','X-OPCUA-Token':document.querySelector('meta[name="opcua-token"]').content},body:JSON.stringify({ui_language:code})});
    const result=await response.json();if(!response.ok||result.error)throw Error(result.error||response.statusText);
    return result;
  }
  window.I18N={t,message,join,bind,attr,option,refresh,get language(){return language;},get revision(){return revision;},locale:()=>locales[language],
    restore(code){if(!revision&&supported.includes(code))apply(code);},
    setLanguage(code,{persist=true}={}){
      apply(code,{user:true});
      if(!persist)return Promise.resolve();
      pending=pending.catch(()=>{}).then(()=>save(code));
      return pending;
    },whenSaved:()=>pending};
  const select=document.getElementById('ui-language');
  select?.addEventListener('change',()=>{
    window.I18N.setLanguage(select.value).catch(error=>{
      const text=t('Impossibile salvare la lingua:')+' '+error.message;
      if(typeof window.notice==='function')window.notice(text,true);else select.title=text;
    });
  });
  refresh();
})();
