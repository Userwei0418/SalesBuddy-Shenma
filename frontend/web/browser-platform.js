(function (global) {
  'use strict';
  const files = new Map();
  let serial = 0;
  let activeRecorder = null;
  let localLoginScope = null;

  function withLocalLogin(callback) {
    const host = new URL(global.location.href).hostname;
    if (global.SALES_MODE === 'preview' || !['localhost', '127.0.0.1', '[::1]'].includes(host)) throw new Error('一键登录仅可在本机企业工作区使用');
    if (localLoginScope) throw new Error('登录正在处理中，请稍候');
    const scope = {used: false}; localLoginScope = scope;
    try { return callback(); }
    finally { if (localLoginScope === scope) localLoginScope = null; }
  }
  function loginTransportState(pending) {
    if (typeof global.dispatchEvent === 'function' && typeof global.CustomEvent === 'function') global.dispatchEvent(new global.CustomEvent('sales:login-transport', {detail: {pending}}));
  }

  function registerLocalFile(file, options = {}) {
    if (!(file instanceof Blob)) throw new Error('请选择有效文件');
    const path = typeof URL.createObjectURL === 'function' ? URL.createObjectURL(file) : `browser-file:${++serial}`;
    files.set(path, { file, recorded: options.recorded === true });
    return path;
  }

  function install(wx, hooks = {}) {
    const mode = global.SALES_MODE === 'preview' ? 'preview' : 'live';
    const prefix = `sales-web:${mode}:v1:`;
    // Persist only explicit remembered accounts and actor-scoped visit drafts.
    // Credentials and all other application state remain tab-local.
    const rememberedLoginKey = 'salesRememberedLoginV1';
    const rememberedLoginStorageKey = `${prefix}remembered-login:${encodeURIComponent(new URL('/api/v1', global.location.href).href)}`;
    const accountHistoryKey='salesAccountHistoryV3';
    const accountHistoryStorageKey=`${prefix}account-history:${encodeURIComponent(new URL('/api/v1', global.location.href).href)}`;
    const storageFor = key => [rememberedLoginKey,accountHistoryKey].includes(key) ? localStorage : sessionStorage;
    const storageKey = key => key === rememberedLoginKey ? rememberedLoginStorageKey : key===accountHistoryKey ? accountHistoryStorageKey : prefix + key;
    const visitDraftKey = key => /^(visitEntryV2|visitConfirmV2|visitStructuredV2|visitStructureRun|visitSupplementV1):/.test(key);
    const draftLocks = new Map(), draftReads = new Map();
    let draftReleaseBarrier = null;
    const draftError = (code, message) => Object.assign(new Error(message), {code});
    const draftEvent = () => global.dispatchEvent?.(new global.CustomEvent('sales:visit-draft-storage'));
    const draftActor = () => {
      try { const actor = JSON.parse(sessionStorage.getItem(prefix + 'salesSession') || '{}');
        if (!actor.workspaceId || !actor.userId) return '';
        const role = ['fde', 'fde_lead'].includes(actor.role) ? `:${actor.role}:${actor.permissionVersion || 'unverified'}` : '';
        return `${actor.workspaceId}:${actor.userId}${role}:service:${encodeURIComponent('/api/v1')}`;
      } catch (_) { return ''; }
    };
    function checkDraftScope(key) {
      const actor = draftActor(), suffix = key.slice(key.indexOf(':') + 1);
      if (!actor || !(suffix === actor || suffix.startsWith(actor + ':'))) throw draftError('DRAFT_STORAGE_SCOPE', '账号已变化，请重新打开跟进记录');
      return (key.startsWith('visitSupplementV1:') ? 'edit:' : 'entry:') + suffix;
    }
    function observeDraft(key) {
      checkDraftScope(key);
      if (!draftReads.has(key)) {
        try { draftReads.set(key, {raw: localStorage.getItem(storageKey(key)), legacy: sessionStorage.getItem(storageKey(key))}); }
        catch (_) { throw draftError('DRAFT_STORAGE_UNAVAILABLE', '浏览器无法保存本机草稿，请允许本站存储或先复制内容'); }
      }
      return draftReads.get(key);
    }
    function acquireDraft(key) {
      const group = checkDraftScope(key);
      let record = draftLocks.get(group);
      if (record) return record;
      record = {state: 'pending'}; draftLocks.set(group, record);
      if (!navigator.locks?.request) {record.state = 'unsupported'; return record;}
      // Web Locks serializes whole draft writes across tabs; a get/set pair in
      // localStorage alone is not an atomic compare-and-swap.
      // A same-tab route change releases asynchronously. Wait for our own old
      // requests to finish before asking ifAvailable, or they look like another tab.
      const requestLock = () => {
        if (draftLocks.get(group) !== record) return;
        return navigator.locks.request(prefix + 'visit-draft-lock:' + group, {ifAvailable: true}, async lock => {
          if (draftLocks.get(group) !== record) return;
          if (!lock) {record.state = 'blocked'; draftEvent(); return;}
          record.state = 'held';
          const released = new Promise(resolve => {record.release = resolve;});
          draftEvent();
          await released;
        });
      };
      record.finished = (draftReleaseBarrier ? draftReleaseBarrier.then(requestLock) : requestLock())
        .catch(() => {if (draftLocks.get(group) === record) {record.state = 'unsupported'; draftEvent();}});
      return record;
    }
    function writableDraft(key) {
      const observed = observeDraft(key), lock = acquireDraft(key);
      if (lock.state === 'pending') throw draftError('DRAFT_STORAGE_PENDING', '正在连接本机草稿存储…');
      if (lock.state === 'blocked') throw draftError('DRAFT_STORAGE_CONFLICT', '另一标签页正在编辑此草稿。本页修改尚未保存，请关闭另一页面后重试保存');
      if (lock.state !== 'held') throw draftError('DRAFT_STORAGE_UNAVAILABLE', '浏览器无法保护本机草稿，请使用最新版 Chrome 或先复制内容');
      const content = raw => {if (raw === null) return null;const value = JSON.parse(raw);delete value.savedAt;return JSON.stringify(value);};
      if (content(localStorage.getItem(storageKey(key))) !== content(observed.raw)) throw draftError('DRAFT_STORAGE_CONFLICT', '另一页面已更新此草稿。本页内容仍保留，请先复制需要保留的内容，再重新打开');
      return observed;
    }
    function saveVisitDraft(key, value, remove = false) {
      try {
        const observed = writableDraft(key), raw = remove ? null : JSON.stringify(value);
        if (remove) localStorage.removeItem(storageKey(key)); else localStorage.setItem(storageKey(key), raw);
        observed.raw = raw; observed.legacy = null;
        sessionStorage.removeItem(storageKey(key));
      } catch (error) {
        if (error.code?.startsWith('DRAFT_STORAGE_')) throw error;
        throw draftError('DRAFT_STORAGE_UNAVAILABLE', '本机草稿未保存，浏览器存储不可用或已满。请先复制内容再处理');
      }
    }
    function releaseDraftLocks() {
      const pending = [...draftLocks.values()].map(record => record.finished);
      for (const record of draftLocks.values()) record.release?.();
      draftLocks.clear();
      if (pending.length) {
        const barrier = Promise.allSettled([draftReleaseBarrier, ...pending]);
        draftReleaseBarrier = barrier;
        barrier.then(() => {if (draftReleaseBarrier === barrier) draftReleaseBarrier = null;});
      }
    }
    wx.__webVisitDrafts = {
      beginPage() {releaseDraftLocks(); draftReads.clear();},
      release: releaseDraftLocks,
      retry() {
        for (const [group, record] of draftLocks) if (record.state !== 'held') draftLocks.delete(group);
        for (const key of draftReads.keys()) acquireDraft(key);
      },
    };
    wx.readLegacyVisitDraftSync = key => {
      const actor = draftActor().split(':service:')[0];
      if (!actor || !['visitEntryV2:', 'visitConfirmV2:'].some(p => key === p + actor)) throw draftError('DRAFT_STORAGE_SCOPE', '旧稿不属于当前账号');
      const raw = sessionStorage.getItem(storageKey(key));
      try {return raw === null ? '' : JSON.parse(raw);} catch (_) {return '';}
    };
    let savedFilesDatabase;
    const savedScope = () => {
      const session = wx.getStorageSync('salesSession');
      if (!session?.workspaceId || !session?.userId) throw new Error('请先登录后保存或读取文件');
      return `${mode}:${encodeURIComponent(session.workspaceId)}:${encodeURIComponent(session.userId)}:`;
    };
    function savedDatabase() {
      if (!global.indexedDB) return Promise.reject(new Error('浏览器暂不支持保存文件'));
      if (!savedFilesDatabase) savedFilesDatabase = new Promise((resolve, reject) => {
        const request = global.indexedDB.open('sales-web-saved-files-v1', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('files', {keyPath: 'path'});
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(new Error('浏览器无法打开文件存储'));
      }).catch(error => {savedFilesDatabase = null; throw error;});
      return savedFilesDatabase;
    }
    async function savedFileOperation(mode, operation) {
      const database = await savedDatabase();
      return new Promise((resolve, reject) => {
        const transaction = database.transaction('files', mode), request = operation(transaction.objectStore('files'));
        transaction.oncomplete = () => resolve(request.result);
        transaction.onerror = transaction.onabort = () => reject(new Error('文件保存或读取失败，请重试'));
      });
    }
    async function localFile(path) {
      // Browser file inputs hand us File/Blob directly; native/shared callers use paths.
      if (path instanceof Blob) return {file:path, recorded:false};
      if (!String(path || '').startsWith('browser-saved:')) return files.get(path);
      const scope = savedScope();
      if (!path.startsWith('browser-saved:' + scope)) throw new Error('文件不属于当前账号');
      if (files.has(path)) return files.get(path);
      const record = await savedFileOperation('readonly', store => store.get(path));
      if (scope !== savedScope()) throw new Error('账号已切换，请重新打开草稿');
      if (!record) return null;
      const local = {file: new File([record.blob], record.name, {type: record.blob.type}), recorded: record.recorded};
      files.set(path, local); return local;
    }
    const notify = message => hooks.toast?.(message);
    function call(fn, value) { try { fn?.(value); } catch (error) { hooks.report?.(error); } }
    function success(options, value) { call(options.success, value); call(options.complete, value); }
    function fail(options, message) { const error = { errMsg: message }; call(options.fail, error); call(options.complete, error); }
    function urlFor(raw) {
      const url = new URL(raw, global.location.href);
      if (url.origin !== global.location.origin || !/^\/api\/v1(?:\/|$)/.test(url.pathname)) {
        throw new Error('Web 接口只允许通过本站 /api/v1 访问');
      }
      return url;
    }
    function transport(options, upload) {
      if (mode === 'preview') {
        const method = upload ? 'uploadFile' : 'request';
        if (typeof global.SalesPreview?.[method] === 'function') return global.SalesPreview[method](options);
        queueMicrotask(() => fail(options, '预览模式尚未提供此操作，未发送真实接口请求'));
        return { abort() {}, onProgressUpdate() {}, offProgressUpdate() {} };
      }
      const controller = new AbortController();
      const progressListeners = new Set();
      let timedOut = false, settled = false, isLogin = false;
      const finish = (error, result) => {
        if (settled) return;
        settled = true; clearTimeout(timer);
        if (isLogin) loginTransportState(false);
        if (error) fail(options, `${upload ? 'uploadFile' : 'request'}:fail ${error}`);
        else success(options, result);
        progressListeners.clear();
      };
      const cancel = timeout => {
        if (settled) return;
        timedOut = timeout; controller.abort();
        finish(timeout ? '请求超时，请稍后重试' : '请求已取消');
      };
      const timer = setTimeout(() => cancel(true), options.timeout || (upload ? 120000 : 15000));
      const task = {
        abort() { cancel(false); },
        onProgressUpdate(callback) { progressListeners.add(callback); },
        offProgressUpdate(callback) { callback ? progressListeners.delete(callback) : progressListeners.clear(); },
      };
      (async () => {
        let result, error;
        try {
          let url = urlFor(options.url);
          const method = String(options.method || (upload ? 'POST' : 'GET')).toUpperCase();
          let headers = new Headers(options.header || {});
          isLogin = !upload && method === 'POST' && url.pathname === '/api/v1/auth/password/login' && !url.search && !url.hash;
          const useLocalLogin = isLogin && localLoginScope && !localLoginScope.used;
          if (useLocalLogin) {
            localLoginScope.used = true;
            url = new URL('/local-login', global.location.href);
            headers = new Headers();
          }
          if (isLogin) loginTransportState(true);
          let body;
          if (useLocalLogin) {
            // The local server reads the configured account. Browser form values never cross this boundary.
          } else if (upload) {
            const local = await localFile(options.filePath);
            if (!local) throw new Error('本地文件已失效，请重新选择或录音');
            const file = local.file;
            const filename = file.name || 'attachment';
            body = new FormData();
            body.append(options.name || 'file', file, filename);
            for (const [key, value] of Object.entries(options.formData || {})) {
              // MediaRecorder produces WebM/MP4, never rename those bytes to MP3.
              body.append(key, key === 'original_filename' && local.recorded ? filename : String(value));
            }
            headers.delete('Content-Type');
          } else if (method === 'GET' || method === 'HEAD') {
            for (const [key, value] of Object.entries(options.data || {})) {
              if (value === undefined || value === null) continue;
              (Array.isArray(value) ? value : [value]).forEach(item => url.searchParams.append(key, String(item)));
            }
          } else if (options.data !== undefined) {
            body = typeof options.data === 'string' ? options.data : JSON.stringify(options.data);
            if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
          }
          if (settled) return;
          const response = await fetch(url, { method, headers, body, signal: controller.signal, credentials: useLocalLogin ? 'omit' : 'same-origin', redirect: 'error' });
          let data;
          if (!upload && options.responseType === 'arraybuffer' && response.ok) {
            data = await response.arrayBuffer();
          } else {
            const text = await response.text();
            data = text;
            // Error JSON stays readable for auth refresh and normal business errors.
            if (!upload && options.dataType !== 'text') { try { data = text ? JSON.parse(text) : null; } catch (_) {} }
          }
          result = { data, statusCode: response.status, header: Object.fromEntries(response.headers.entries()), errMsg: `${upload ? 'uploadFile' : 'request'}:ok` };
          // Fetch has no upload-byte progress events; only report completion once it actually finishes.
          if (upload && !settled) progressListeners.forEach(callback => call(callback, { progress: 100, totalBytesSent: (options.filePath instanceof Blob ? options.filePath.size : files.get(options.filePath)?.file.size) || 0, totalBytesExpectedToSend: (options.filePath instanceof Blob ? options.filePath.size : files.get(options.filePath)?.file.size) || 0 }));
        } catch (cause) {
          error = timedOut ? '请求超时，请稍后重试' : controller.signal.aborted ? '请求已取消' : cause.message || '网络连接失败';
        }
        finish(error, result);
      })();
      return task;
    }

    wx.getStorageSync = key => {
      if (key === 'salesApiBaseUrl') return '/api/v1';
      if (visitDraftKey(key)) {
        // An explicit shared-controller recovery action may inspect the same
        // actor's old tab-local text. Never migrate remote IDs or write old keys.
        const observed = observeDraft(key); acquireDraft(key);
        const raw = localStorage.getItem(storageKey(key)) ?? observed.legacy;
        if (raw === null) return '';
        try {return JSON.parse(raw);} catch (_) {throw draftError('DRAFT_STORAGE_UNAVAILABLE', '本机草稿无法读取，请先保留当前内容');}
      }
      const value = storageFor(key).getItem(storageKey(key));
      if (value === null) return '';
      try { return JSON.parse(value); } catch (_) { return ''; }
    };
    wx.setStorageSync = (key, value) => {
      if (visitDraftKey(key)) return saveVisitDraft(key, value);
      const previousActor = key === 'salesSession' ? draftActor() : null;
      if (key !== 'salesApiBaseUrl') storageFor(key).setItem(storageKey(key), JSON.stringify(value));
      if (key === 'salesSession' && previousActor !== draftActor()) releaseDraftLocks();
    };
    wx.removeStorageSync = key => {if (visitDraftKey(key)) return saveVisitDraft(key, null, true); if (key === 'salesSession') releaseDraftLocks(); storageFor(key).removeItem(storageKey(key));};
    // Logging out retains scoped local drafts for the same account's next login.
    wx.clearStorageSync = () => {releaseDraftLocks(); Object.keys(sessionStorage).filter(key => key.startsWith(prefix)).forEach(key => sessionStorage.removeItem(key)); localStorage.removeItem(rememberedLoginStorageKey); localStorage.removeItem(accountHistoryStorageKey);};
    wx.request = options => transport(options, false);
    wx.uploadFile = options => transport(options, true);
    wx.env = { USER_DATA_PATH: `browser-files:${mode}` };
    wx.nextTick = callback => requestAnimationFrame(callback);
    wx.openPrivacyContract = (options = {}) => {
      const notice = global.SALES_BUNDLE?.privacyNotice;
      const unavailable = () => {
        const reportUnavailable = () => fail(options, 'openPrivacyContract:fail 隐私指引暂不可用，请联系运营');
        return hooks.withToastSource ? hooks.withToastSource('privacy', reportUnavailable) : reportUnavailable();
      };
      if (!notice || notice.schemaVersion !== 1 || typeof notice.revision !== 'string' || !notice.revision.trim()) return unavailable();
      const text = typeof notice.text === 'string' ? notice.text.trim() : '';
      let url = '';
      if (notice.url) {
        try {
          const parsed = new URL(notice.url, global.location.href);
          if (parsed.username || parsed.password || !(parsed.protocol === 'https:' || parsed.protocol === 'http:' && parsed.origin === global.location.origin)) return unavailable();
          url = parsed.href;
        } catch (_) { return unavailable(); }
      }
      if (!text && !url) return unavailable();
      try {
        return wx.showModal({
          title: typeof notice.title === 'string' && notice.title.trim() || '隐私与数据使用说明',
          content: text, showCancel: false, confirmText: '关闭', webPrivacyNotice: true, webPrivacyUrl: url,
          success: () => success(options, {errMsg: 'openPrivacyContract:ok'}),
          fail: unavailable,
        });
      } catch (_) { return unavailable(); }
    };
    wx.vibrateShort = options => { navigator.vibrate?.(15); success(options || {}, {}); };
    const systemInfo = () => ({ platform: 'web', system: navigator.platform || 'Web', SDKVersion: '3.0.0', windowWidth: global.innerWidth, windowHeight: global.innerHeight, screenWidth: screen.width, screenHeight: screen.height, pixelRatio: global.devicePixelRatio || 1, safeArea: { top: 0, left: 0, right: global.innerWidth, bottom: global.innerHeight, width: global.innerWidth, height: global.innerHeight }, ...hooks.windowInfo?.() });
    wx.getSystemInfoSync = systemInfo;
    wx.getWindowInfo = systemInfo;
    wx.getSystemInfo = options => success(options, systemInfo());
    wx.setClipboardData = async options => { try { await navigator.clipboard.writeText(options.data); success(options, {}); } catch (_) { fail(options, '无法写入剪贴板，请手动复制'); } };
    wx.getClipboardData = async options => { try { success(options, { data: await navigator.clipboard.readText() }); } catch (_) { fail(options, '无法读取剪贴板'); } };
    wx.chooseMessageFile = options => {
      const input = document.createElement('input');
      input.type = 'file'; input.multiple = (options.count || 1) > 1;
      input.accept = (options.extension || []).map(extension => `.${extension}`).join(',');
      input.style.display = 'none'; document.body.append(input);
      input.onchange = () => {
        const tempFiles = Array.from(input.files || []).slice(0, options.count || 1).map(file => ({ path: registerLocalFile(file), name: file.name, size: file.size, type: file.type }));
        input.remove();
        tempFiles.length ? success(options, { tempFiles }) : fail(options, 'chooseMessageFile:fail cancel');
      };
      input.oncancel = () => { input.remove(); fail(options, 'chooseMessageFile:fail cancel'); };
      input.click();
    };
    wx.openDocument = options => {
      if (!files.has(options.filePath)) return fail(options, '文件已失效，请重新选择');
      const anchor = document.createElement('a');
      anchor.href = options.filePath; anchor.download = files.get(options.filePath).file.name || 'attachment'; anchor.click();
      success(options, {});
    };
    wx.getFileSystemManager = () => ({
      async saveFile(options) {
        try {
          const scope = savedScope(), local = await localFile(options.tempFilePath);
          if (!local) throw new Error('文件不存在，请重新选择或录音');
          const path = `browser-saved:${scope}${global.crypto?.randomUUID?.() || Date.now() + '-' + Math.random().toString(36).slice(2)}`;
          await savedFileOperation('readwrite', store => store.put({path, blob: local.file, name: local.file.name || 'attachment', recorded: local.recorded}));
          let currentScope = ''; try {currentScope = savedScope();} catch (_) {}
          if (currentScope !== scope) {
            await savedFileOperation('readwrite', store => store.delete(path));
            throw new Error('账号已切换，请重新保存文件');
          }
          files.set(path, local); success(options, {savedFilePath: path});
        } catch (error) {fail(options, error.message);}
      },
      async removeSavedFile(options) {
        try {
          if (!String(options.filePath || '').startsWith('browser-saved:' + savedScope())) throw new Error('文件不属于当前账号');
          await savedFileOperation('readwrite', store => store.delete(options.filePath));
          files.delete(options.filePath); success(options, {});
        } catch (error) {fail(options, error.message);}
      },
      async copyFile(options) {try {const file = await localFile(options.srcPath); if (!file) return fail(options, '文件不存在'); files.set(options.destPath, file); success(options, {});} catch (error) {fail(options, error.message);}},
      async readFile(options) { try { const file = (await localFile(options.filePath))?.file; if (!file) throw new Error('文件不存在'); success(options, { data: options.encoding === 'utf8' ? await file.text() : await file.arrayBuffer() }); } catch (error) { fail(options, error.message); } },
      writeFile(options) { files.set(options.filePath, { file: new Blob([options.data]), recorded: false }); success(options, {}); },
      unlink(options) {if (String(options.filePath || '').startsWith('browser-saved:')) return wx.getFileSystemManager().removeSavedFile(options); const file = files.get(options.filePath); if (file) URL.revokeObjectURL?.(options.filePath); files.delete(options.filePath); success(options, {}); },
      readdir(options) { success(options, { files: [...files.keys()].filter(path => path.startsWith(options.dirPath + '/')).map(path => path.slice(options.dirPath.length + 1)) }); },
      async getFileInfo(options) {try {const file = (await localFile(options.filePath))?.file; file ? success(options, {size: file.size}) : fail(options, '文件不存在');} catch (error) {fail(options, error.message);}},
    });
    async function recordPermission() {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器无法录音，请使用 localhost 或 HTTPS');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop());
    }
    wx.getSetting = async options => {
      const authSetting = {};
      try { const permission = await navigator.permissions?.query({ name: 'microphone' }); if (permission?.state !== 'prompt' && permission?.state) authSetting['scope.record'] = permission.state === 'granted'; } catch (_) {}
      success(options, { authSetting });
    };
    wx.authorize = async options => {
      if (options.scope !== 'scope.record') return fail(options, '浏览器暂不支持此授权范围');
      try { await recordPermission(); success(options, {}); } catch (error) { fail(options, error.message); }
    };
    wx.openSetting = async options => { notify('请通过浏览器地址栏的站点设置允许麦克风'); wx.getSetting(options); };
    wx.getRecorderManager = () => {
      const listeners = new Map();
      let media, stream, chunks, startedAt, durationTimer, starting = false, stoppedWhileStarting = false;
      const recorder = {};
      const emit = (event, value) => listeners.get(event)?.forEach(callback => call(callback, value));
      for (const event of ['Start', 'Stop', 'Error', 'Pause', 'Resume', 'InterruptionBegin', 'InterruptionEnd']) {
        listeners.set(event, new Set());
        recorder[`on${event}`] = callback => listeners.get(event).add(callback);
        recorder[`off${event}`] = callback => callback ? listeners.get(event).delete(callback) : listeners.get(event).clear();
      }
      recorder.start = async (options = {}) => {
        if (starting || media?.state === 'recording') return;
        if (activeRecorder && activeRecorder !== recorder) { emit('Error', { errMsg: '另一处录音尚未结束，请先停止录音' }); return; }
        starting = true; stoppedWhileStarting = false; activeRecorder = recorder;
        try {
          if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('当前浏览器无法录音，请使用支持录音的浏览器和 HTTPS/localhost');
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          if (stoppedWhileStarting) { stream.getTracks().forEach(track => track.stop()); activeRecorder = null; return; }
          const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => MediaRecorder.isTypeSupported(type));
          media = new MediaRecorder(stream, mimeType ? { mimeType } : undefined); chunks = [];
          media.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
          media.onstop = () => {
            clearTimeout(durationTimer); stream.getTracks().forEach(track => track.stop()); activeRecorder = null;
            const type = media.mimeType || chunks[0]?.type || 'audio/webm';
            const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
            const file = new File(chunks, `拜访录音.${extension}`, { type });
            emit('Stop', { tempFilePath: registerLocalFile(file, { recorded: true }), duration: Date.now() - startedAt, fileSize: file.size, fileName: file.name });
          };
          media.onerror = () => { clearTimeout(durationTimer); stream.getTracks().forEach(track => track.stop()); activeRecorder = null; emit('Error', { errMsg: '录音未完成，请检查麦克风后重试' }); };
          media.onpause = () => emit('Pause', {}); media.onresume = () => emit('Resume', {});
          media.start(); startedAt = Date.now();
          durationTimer = setTimeout(() => recorder.stop(), Math.min(3600000, options.duration || 600000));
          emit('Start', {});
        } catch (error) { stream?.getTracks().forEach(track => track.stop()); activeRecorder = null;
          const denied = ['NotAllowedError', 'PermissionDeniedError', 'SecurityError'].includes(error?.name);
          const missing = ['NotFoundError', 'DevicesNotFoundError'].includes(error?.name);
          emit('Error', {errMsg: denied ? '麦克风权限未开启，请在浏览器设置中允许录音'
            : missing ? '未找到可用的麦克风，请连接设备后重试' : '暂时无法录音，请检查麦克风及浏览器设置后重试'});
        }
        finally { starting = false; }
      };
      recorder.stop = () => { stoppedWhileStarting = true; if (media && ['recording', 'paused'].includes(media.state)) media.stop(); };
      recorder.pause = () => { if (media?.state === 'recording') media.pause(); };
      recorder.resume = () => { if (media?.state === 'paused') media.resume(); };
      // No onFrameRecorded: WebM chunks cannot satisfy the mini-program's MP3-frame contract.
      return recorder;
    };
    wx.createInnerAudioContext = () => {
      const audio = new Audio(); const api = {
        play() { audio.play().catch(() => audio.dispatchEvent(new Event('error'))); }, pause() { audio.pause(); }, stop() { audio.pause(); audio.currentTime = 0; }, seek(time) {if(Number.isFinite(time))audio.currentTime=time;}, destroy() { audio.pause(); audio.removeAttribute('src'); audio.load(); },
      };
      for (const [name, event] of Object.entries({ Play: 'play', Pause: 'pause', Ended: 'ended', Error: 'error', TimeUpdate: 'timeupdate', Canplay: 'canplay' })) api[`on${name}`] = callback => audio.addEventListener(event, callback);
      Object.defineProperties(api, { src: { get: () => audio.src, set: value => { audio.src = value; } }, currentTime: { get: () => audio.currentTime }, duration: { get: () => audio.duration } });
      return api;
    };
    return wx;
  }
  global.SalesPlatform = { install, registerLocalFile, withLocalLogin };
})(window);
