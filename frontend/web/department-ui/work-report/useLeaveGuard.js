import {useCallback, useEffect, useRef} from 'react';
import {App} from 'antd';
import {identity} from '../pc/data.js';

// Shares runtime's existing _requestWebExit entry point with other editors.
// Report content stays in React state and is never copied to browser storage.
export function useLeaveGuard({page, dirty, saving, owner, getIdentity = identity}) {
  const {modal,message} = App.useApp();
  const state = useRef({dirty,saving,owner,getIdentity});state.current = {dirty,saving,owner,getIdentity};
  const active = useRef(false), dialog = useRef(null);
  const requestLeave = useCallback(action => {
    const current = state.current;
    if (!active.current || current.owner !== current.getIdentity()) return;
    if (current.saving) {message.info('正在保存，请稍候');return;}
    if (!current.dirty) {action();return;}
    if (dialog.current) return;
    const capturedOwner = current.owner;
    const valid = () => active.current && capturedOwner === state.current.owner && capturedOwner === state.current.getIdentity() && !(page?._destroyed);
    dialog.current = modal.confirm({title:'离开当前编辑内容？',content:'还有尚未保存的修改。离开后，这些修改不会保留。',okText:'放弃修改并离开',cancelText:'继续编辑',
      onOk:()=>{dialog.current=null;if(valid() && !state.current.saving) action();},
      onCancel:()=>{dialog.current=null;},
    });
  },[modal,message,page]);
  useEffect(()=>{
    active.current=true;
    const previous = page?._requestWebExit;
    if(page) page._requestWebExit=requestLeave;
    const beforeUnload=event=>{
      if(!state.current.dirty && !state.current.saving)return;
      event.preventDefault();event.returnValue='';
    };
    window.addEventListener('beforeunload',beforeUnload);
    return ()=>{
      active.current=false;dialog.current?.destroy();dialog.current=null;
      if(page?._requestWebExit===requestLeave) {if(previous)page._requestWebExit=previous;else delete page._requestWebExit;}
      window.removeEventListener('beforeunload',beforeUnload);
    };
  },[page,requestLeave,owner]);
  return requestLeave;
}
