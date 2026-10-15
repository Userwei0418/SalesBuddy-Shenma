import {identity, runtime, session} from '../../pc/data.js';
import {createAlignedTasks} from './api.js';
import {normalizeTaskAlignment} from './model.js';

// Scope the additive payload to this Page. Original submitTask still owns form
// validation, recipient checks, confirmation and the immutable retry intent.
export function submitAlignedTask(page, alignment, invoke) {
  if (!page.__reviewTaskAdapter) {
    const originalSend = page.sendSubmission, originalSignature = page.submissionSignature;
    const writes = runtime().require('utils/pageWriteContext').createPageWriteContext(session);
    page.submissionSignature = function () {return JSON.stringify([originalSignature.call(this), this.__reviewAlignment]);};
    page.sendSubmission = async function () {
      const pending = this.pendingSubmission;
      if (!pending) return;
      if (!Object.hasOwn(pending, 'reviewAlignment')) pending.reviewAlignment = this.__reviewAlignment;
      if (pending.reviewAlignment == null) return originalSend.call(this);
      if (this.isUnloading || this.data.submitting || this._submitted || pending.identity !== identity() || this.writeHidden) return;
      const write = writes.begin(this, 'submission');
      if (!write) return;
      const current = () => write.current() && this.pendingSubmission === pending;
      this.setData({submitting: true, submissionPending: true});
      try {
        const tasks = await createAlignedTasks(pending.inputs, pending.source, pending.reviewAlignment);
        if (!current()) return;
        if (!Array.isArray(tasks) || tasks.length !== pending.inputs.length || tasks.some(task => !task?.id)) throw Error('发送结果尚未确认，请重试核对');
        this._submitted = true; this.pendingSubmission = null; this.completedSubmission = writes.capture(this);
        try {runtime().wx.setStorageSync('lastManagementTaskCreated', {id: tasks[0].id, ids: tasks.map(task => task.id), ownerUserId: pending.session.userId, workspaceId: pending.session.workspaceId});} catch (_) { /* Server readback remains authoritative. */ }
        if (write.visible()) runtime().wx.showToast({title: `已发送${tasks.length}条待办`, icon: 'success'});
        this.returnTimer = setTimeout(() => {if (write.settledVisible()) this.returnAfterSubmission();}, 850);
      } catch (error) {
        if (!current()) return;
        const rejected = [400, 403, 404, 409, 422].includes(error.statusCode);
        if (rejected) this.pendingSubmission = null;
        this.setData({submitting: false, submissionPending: !rejected});
        if (write.visible()) runtime().wx.showToast({title: error.message || '发送结果未确认，请重试', icon: 'none'});
      } finally {write.finish('submitting');}
    };
    page.__reviewTaskAdapter = true;
  }
  // A pending retry always uses the original alignment and original task inputs.
  if (!page.pendingSubmission) page.__reviewAlignment = normalizeTaskAlignment(alignment);
  return invoke('submitTask');
}
