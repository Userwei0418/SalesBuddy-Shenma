const apiClient = require('../../utils/apiClient');
const { canCancelTask, cancellationIdentity } = require('../../utils/taskCancellation');

function session() {
  return getApp().globalData.session;
}

Component({
  options: { addGlobalClass: true },
  properties: {
    open: Boolean,
    taskId: String,
    // Optional host revision. The version used for writes always comes from getTask.
    taskVersion: { type: Number, value: 0 },
  },
  data: { task: null, note: '', loading: false, submitting: false, confirming: false,
    canSubmit: false, error: '', needsRefresh: false },
  observers: {
    'open, taskId': function (open) {
      if (open) this.begin(false);
      else this.invalidate();
    },
    taskVersion() {
      if (!this.properties.open || !this._context || this._context.hostVersion === this.properties.taskVersion) return;
      this.invalidate();
      this.setData({ task: null, loading: false, submitting: false, confirming: false,
        canSubmit: false, needsRefresh: true, error: '任务已更新，请刷新任务后重新确认。取消原因已保留。' });
    },
  },
  lifetimes: {
    detached() { this._detached = true; this.invalidate(); },
  },
  pageLifetimes: {
    hide() { this.invalidate(); },
  },
  methods: {
    noop() {},
    invalidate() {
      this._generation = (this._generation || 0) + 1;
      this._context = null;
      this._confirmation = null;
    },
    current(context, task) {
      return !this._detached && this.properties.open && this._context === context &&
        context.generation === this._generation && context.taskId === this.properties.taskId &&
        context.hostVersion === this.properties.taskVersion && context.identity === cancellationIdentity(session()) &&
        (!task || (this.data.task && this.data.task.id === task.id && this.data.task.version_no === task.version_no &&
          canCancelTask(this.data.task, session())));
    },
    async begin(preserveNote) {
      this.invalidate();
      if (!this.properties.open || this._detached) return;
      const context = this._context = { generation: this._generation, taskId: this.properties.taskId,
        hostVersion: this.properties.taskVersion, identity: cancellationIdentity(session()) };
      this.setData({ task: null, note: preserveNote ? this.data.note : '', loading: true,
        submitting: false, confirming: false, canSubmit: false, error: '', needsRefresh: false });
      if (this._pendingWrite && this._pendingWrite.taskId === context.taskId && this._pendingWrite.identity === context.identity) {
        this.setData({ loading: false, needsRefresh: true, error: '取消请求正在处理，请稍后刷新确认结果。' });
        return;
      }
      try {
        const task = await apiClient.getTask(context.taskId);
        if (!this.current(context)) return;
        if (!task || task.id !== context.taskId || !Number.isInteger(task.version_no) || task.version_no < 1) {
          this.setData({ error: '未取得任务最新版本，请刷新后重试。', needsRefresh: true });
          return;
        }
        const canSubmit = canCancelTask(task, session());
        this.setData({ task, canSubmit,
          error: canSubmit ? '' : '当前任务状态或权限已变化，无法取消。' });
        if (!canSubmit) {
          // A write may have succeeded even when its response was lost or the
          // dialog was closed. Publish the verified read without claiming a new write.
          this.setData({ loading: false });
          this.invalidate();
          this.triggerEvent('refreshed', task);
        }
      } catch (error) {
        if (this.current(context)) this.setData({ error: error.message || '任务加载失败，请重试。', needsRefresh: true });
      } finally {
        if (this.current(context)) this.setData({ loading: false });
      }
    },
    refresh() {
      if (this.data.loading || this.data.submitting || this.data.confirming) return;
      return this.begin(true);
    },
    inputNote(event) {
      if (!this.properties.open || this.data.submitting || this.data.confirming) return;
      this.setData({ note: String(event.detail.value || '').slice(0, 500) });
    },
    close() {
      this.invalidate();
      this.triggerEvent('close');
    },
    submit() {
      const context = this._context, task = this.data.task && { ...this.data.task };
      if (!context || !task || this.data.loading || this.data.submitting || this.data.confirming ||
          this._confirmation || this._pendingWrite || this.data.needsRefresh || !this.data.canSubmit) return;
      if (!this.current(context, task)) {
        this.invalidate();
        this.setData({ canSubmit: false, needsRefresh: true, error: '身份、权限或任务已变化，请刷新后重新确认。' });
        return;
      }
      const note = String(this.data.note || '').trim();
      if (!note || note.length > 500) {
        this.setData({ error: !note ? '请填写取消原因。' : '取消原因不能超过 500 字。' });
        return;
      }
      const confirmation = this._confirmation = {};
      this.setData({ error: '', confirming: true });
      wx.showModal({
        title: '确认取消任务？',
        content: '取消后不会标记为完成，任务及取消原因会保留，后续仍可查看。',
        confirmText: '确认取消', cancelText: '继续编辑', confirmColor: '#AC2F28',
        success: async result => {
          if (this._confirmation !== confirmation) return;
          this._confirmation = null;
          if (!this.current(context, task)) return;
          this.setData({ confirming: false });
          if (!result.confirm || this._pendingWrite) return;
          const pending = this._pendingWrite = { taskId: task.id, identity: context.identity };
          this.setData({ submitting: true, error: '' });
          try {
            const updated = await apiClient.coordinateTask(task.id, { event_type: 'cancel', note, version_no: task.version_no });
            if (!this.current(context, task)) return;
            if (!updated || updated.id !== task.id || updated.status !== 'cancelled' || updated.last_event_type !== 'cancel' ||
                !Number.isInteger(updated.version_no) || updated.version_no <= task.version_no) {
              this.setData({ error: '取消请求已返回，但结果尚未确认。请刷新任务状态，勿重复提交。', needsRefresh: true, canSubmit: false });
              return;
            }
            this.invalidate();
            this.setData({ submitting: false, canSubmit: false });
            this.triggerEvent('cancelled', updated);
          } catch (error) {
            if (!this.current(context, task)) return;
            const refresh = [401, 403, 404, 409].includes(error.statusCode);
            this.setData({ error: error.statusCode === 409 ? '任务已更新，请刷新任务后重新确认。取消原因已保留。' :
              error.message || '取消失败，原因已保留，请重试。', needsRefresh: refresh, canSubmit: !refresh });
          } finally {
            if (this._pendingWrite === pending) this._pendingWrite = null;
            if (this.current(context, task)) this.setData({ submitting: false });
          }
        },
        fail: () => {
          if (this._confirmation !== confirmation) return;
          this._confirmation = null;
          if (this.current(context, task)) this.setData({ confirming: false, error: '确认弹窗未打开，请重试。' });
        },
      });
    },
  },
});
