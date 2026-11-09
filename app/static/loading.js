/* Loading presentation is separate from application data and never invents progress. */
window.PostdeskLoading=(()=>{
  const states=new Map();
  const line=(size='medium')=>`<span class="sk sk-line sk-${size}"></span>`;
  const row=()=>`<div class="sk-event"><span class="sk sk-avatar"></span><div>${line('medium')}${line('short')}</div>${line('short')}</div>`;
  const card=()=>`<div class="panel sk-card">${line('short')}${line('title')}${line('full')}${line('medium')}${line('full')}${line('short')}</div>`;
  const layouts={
    overview:`<div class="stats">${Array.from({length:4},()=>`<div class="stat sk-stat">${line('medium')}<span class="sk sk-number"></span>${line('short')}</div>`).join('')}</div><div class="overview-grid"><div class="panel sk-upcoming">${line('title')}<div class="sk-event"><span class="sk sk-avatar"></span><div>${line('medium')}${line('short')}</div></div>${line('full')}${line('full')}${line('medium')}<div class="sk-schedule">${line('short')}${line('short')}</div></div>${card()}</div><div class="panel">${line('title')}${row()}${row()}${row()}</div>`,
    queue:`<div class="sk-toolbar">${line('title')}${line('medium')}</div><div class="panel sk-table"><div class="sk-table-heading">${line('short')}${line('short')}${line('short')}${line('short')}</div>${Array.from({length:6},()=>`<div class="sk-table-row"><span class="sk sk-avatar"></span><div>${line('full')}${line('medium')}</div><span class="sk sk-badge"></span>${line('short')}<span class="sk sk-action"></span></div>`).join('')}</div>`,
    calendar:`<div class="calendar-layout"><div class="panel"><div class="sk-toolbar">${line('title')}${line('short')}</div><div class="sk-weekdays">${Array.from({length:7},()=>line('short')).join('')}</div><div class="sk-calendar">${Array.from({length:42},(_,i)=>`<div class="sk-day"><span class="sk sk-date"></span>${i%3===0?line('full'):''}</div>`).join('')}</div></div><div class="panel agenda-panel">${line('title')}${row()}${row()}${row()}</div></div>`,
    activity:`<div class="panel">${line('title')}${line('medium')}${Array.from({length:7},row).join('')}</div>`,
    import:`<div class="import-layout"><div class="panel sk-import"><span class="sk sk-upload"></span>${line('title')}${line('full')}${line('medium')}<span class="sk sk-input"></span>${line('short')}<span class="sk sk-button"></span></div>${card()}</div>`,
    settings:`<div class="system-grid">${card()}${card()}</div><div class="system-grid"><div class="panel">${line('title')}${row()}${row()}${row()}</div><div class="panel">${line('title')}${line('medium')}${row()}${row()}</div></div>`
  };
  function init(labels,retry){
    document.querySelectorAll('.view').forEach(view=>{
      const name=view.id.replace('view-','');
      const content=document.createElement('div');content.className='view-content';
      while(view.firstChild)content.append(view.firstChild);
      const skeleton=document.createElement('div');skeleton.className='page-skeleton';skeleton.hidden=true;
      skeleton.setAttribute('aria-hidden','true');skeleton.inert=true;skeleton.innerHTML=layouts[name];
      const failure=document.createElement('div');failure.className='panel load-failure';failure.hidden=true;
      const title=document.createElement('h2');title.textContent=`Couldn’t load ${labels[name][0].toLowerCase()}`;
      const message=document.createElement('p');message.className='muted';
      const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Try again';button.addEventListener('click',retry);
      failure.append(title,message,button);view.append(skeleton,content,failure);
      view.setAttribute('aria-label',labels[name][0]);view.setAttribute('aria-busy','false');
      states.set(name,{view,content,skeleton,failure,message,ready:false});
    });
  }
  function start(name,{background=false}={}){
    const state=states.get(name);if(!state)return;
    state.view.setAttribute('aria-busy','true');state.failure.hidden=true;
    state.skeleton.hidden=state.ready;state.content.hidden=!state.ready;
    state.view.classList.toggle('is-initial-loading',!state.ready);
    state.view.classList.toggle('is-refreshing',state.ready&&!background);
    // Keep navigation/filter controls usable; old data rows cannot be clicked during a refresh.
    state.content.querySelectorAll('#queue-list,#calendar-grid,#calendar-agenda,#activity-list').forEach(e=>e.inert=!background);
  }
  function finish(name,{success=false,error=null}={}){
    const state=states.get(name);if(!state)return;
    if(success)state.ready=true;
    state.skeleton.hidden=true;state.content.hidden=!state.ready;
    state.view.setAttribute('aria-busy','false');state.view.classList.remove('is-initial-loading','is-refreshing');
    state.content.querySelectorAll('[inert]').forEach(e=>e.inert=false);
    if(error&&!state.ready){state.message.textContent=error.message;state.failure.hidden=false;}
  }
  function reset(){for(const [name,state] of states){state.ready=false;finish(name);state.failure.hidden=true;}}
  return {init,start,finish,reset,hasContent:name=>states.get(name)?.ready===true};
})();
