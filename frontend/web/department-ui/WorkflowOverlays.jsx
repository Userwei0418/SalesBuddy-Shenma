import React from 'react';
import {flushSync} from 'react-dom';
import {Drawer, Modal} from 'antd';
import './workflow-overlays.css';

function useNativeConfirmation(open) {
  const [active, setActive] = React.useState(false);
  React.useLayoutEffect(() => {
    const host = document.getElementById('wx-overlays');
    if (!open || !host) {setActive(false);return;}
    let current = Boolean(host.querySelector('.wx-modal-mask'));
    setActive(current);
    const observer = new MutationObserver(() => {
      const next = Boolean(host.querySelector('.wx-modal-mask'));
      if (next === current) return;
      current = next;
      // Native wx.showModal focuses on the next frame. Hand focus to that
      // confirmation first, avoiding two simultaneous focus traps.
      flushSync(() => setActive(next));
    });
    observer.observe(host, {childList: true});
    return () => observer.disconnect();
  }, [open]);
  return active;
}

// Web presentation only; callers retain the shared draft and action handlers.
export function WorkflowDialog({open, title, onClose, footer, children, width = 640}) {
  const nativeConfirmation = useNativeConfirmation(open);
  return <Modal open={open} title={title} centered width={width} onCancel={onClose}
    focusable={{trap: !nativeConfirmation}} closable={Boolean(onClose)} keyboard={Boolean(onClose) && !nativeConfirmation} maskClosable={Boolean(onClose)}
    rootClassName="department-ui ds-workflow-overlay" className="ds-workflow-dialog"
    footer={<div className="ds-workflow-footer">{footer}</div>} destroyOnHidden>
    {children}
  </Modal>;
}

export function PeopleDialog(props) {
  return <WorkflowDialog {...props} width={560} />;
}

export function WorkflowDrawer({open, title, onClose, footer, children, width = 640, className = ''}) {
  const nativeConfirmation = useNativeConfirmation(open);
  return <Drawer open={open} title={title} placement="right" size={width} onClose={onClose}
    focusable={{trap: !nativeConfirmation}} closable={Boolean(onClose)} keyboard={Boolean(onClose) && !nativeConfirmation} maskClosable={Boolean(onClose)}
    rootClassName={`department-ui ds-workflow-drawer ${className}`} destroyOnHidden
    footer={<div className="ds-workflow-footer">{footer}</div>}>
    {children}
  </Drawer>;
}

export function AdviceDrawer(props) {
  return <WorkflowDrawer {...props} width={560} className="ds-advice-drawer" />;
}
