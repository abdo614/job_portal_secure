/* PHASE13 - Unified UX utilities. No business/payment logic lives here. */
(function(){
  'use strict';
  const $=(s,r=document)=>r.querySelector(s);
  function ensureToastContainer(){
    let c=$('.ux-toast-container');
    if(!c){c=document.createElement('div');c.className='ux-toast-container';c.setAttribute('aria-live','polite');c.setAttribute('aria-atomic','false');document.body.appendChild(c)}
    return c;
  }
  window.uxToast=function(message,type='info',timeout=4500){
    const c=ensureToastContainer(), el=document.createElement('div');
    el.className='ux-toast '+type; el.setAttribute('role',type==='error'?'alert':'status');
    const text=document.createElement('span');text.textContent=String(message??'');el.appendChild(text);
    const close=document.createElement('button');close.className='ux-toast-close';close.type='button';close.setAttribute('aria-label','Close');close.textContent='×';close.onclick=()=>el.remove();el.appendChild(close);c.appendChild(el);
    if(timeout>0)setTimeout(()=>{if(el.isConnected)el.remove()},timeout); return el;
  };
  window.uxSetLoading=function(active){
    let o=$('.ux-loading-overlay');
    if(!o){o=document.createElement('div');o.className='ux-loading-overlay';o.setAttribute('aria-hidden','true');o.innerHTML='<div class="ux-spinner" role="status" aria-label="Loading"></div>';document.body.appendChild(o)}
    o.classList.toggle('is-active',!!active); o.setAttribute('aria-hidden',active?'false':'true');
  };
  window.uxSubmitLoading=function(form){
    if(!form||form.dataset.uxBusy==='1')return false; form.dataset.uxBusy='1';
    const buttons=form.querySelectorAll('button[type="submit"],input[type="submit"]');buttons.forEach(b=>{b.disabled=true;b.dataset.uxOldText=b.tagName==='BUTTON'?b.textContent:'';if(b.tagName==='BUTTON')b.textContent='جاري التنفيذ...'});return true;
  };
  document.addEventListener('DOMContentLoaded',function(){
    const main=$('#main-content'); if(!main){const c=document.querySelector('.container'); if(c){c.id='main-content'}}
    document.querySelectorAll('table').forEach(t=>{if(!t.parentElement.classList.contains('ux-mobile-table')){const w=document.createElement('div');w.className='ux-mobile-table';t.parentNode.insertBefore(w,t);w.appendChild(t)}});
    document.querySelectorAll('a[href="#"],a[href="javascript:void(0)"]').forEach(a=>{if(a.getAttribute('href')==='#'&&!a.hasAttribute('aria-label'))a.setAttribute('aria-label','Link')});
  });
})();
