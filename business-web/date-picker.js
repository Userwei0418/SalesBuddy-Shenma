(function (global) {
  'use strict';
  let active = null;
  const pad = n => String(n).padStart(2, '0');
  const dateText = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  function parse(text, mode) {
    const match = String(text || '').match(mode === 'time' ? /^(\d{2}):(\d{2})$/ : /^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    if (mode === 'time') {
      const hour = Number(match[1]), minute = Number(match[2]);
      return hour < 24 && minute < 60 ? new Date(2000, 0, 1, hour, minute) : null;
    }
    const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
    if (year < 1) return null;
    const date = new Date(2000, month - 1, day); date.setFullYear(year);
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
  }
  function valid(text, options) {
    return !!parse(text, options.mode) && (!options.min || text >= options.min) && (!options.max || text <= options.max);
  }
  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }
  function button(text, className, action) {
    const el = element('button', className, text); el.type = 'button'; el.addEventListener('click', action); return el;
  }
  function close({restoreFocus = true} = {}) {
    const record = active; if (!record) return;
    active = null;
    record.anchor.setAttribute('aria-expanded', 'false');
    record.resizeObserver?.disconnect(); record.picker?.destroy(); record.dialog.close(); record.dialog.remove();
    global.removeEventListener('resize', record.position);
    if (restoreFocus && record.anchor.isConnected && !record.anchor.disabled) record.anchor.focus({preventScroll: true});
  }
  function reconcile() {
    if (!active) return;
    const {anchor, originalValue, min, max} = active;
    if (!anchor.isConnected || anchor.disabled || anchor.value !== originalValue ||
        (anchor.dataset.min || '') !== min || (anchor.dataset.max || '') !== max) close({restoreFocus: false});
    else anchor.setAttribute('aria-expanded', 'true');
  }
  function open(options) {
    const {anchor, mode, value = '', min = '', max = '', onCommit} = options;
    if (!anchor.isConnected || anchor.disabled) return;
    close({restoreFocus: false});
    if (!global.flatpickr) throw new Error('日期组件未加载，请刷新页面后重试。');
    const dialog = element('dialog', 'wx-date-dialog'); dialog.id = 'web-date-dialog';
    dialog.setAttribute('aria-labelledby', 'web-date-title'); dialog.setAttribute('aria-describedby', 'web-date-help');
    const header = element('header', 'wx-date-header');
    const title = element('h2', '', mode === 'time' ? '选择时间' : '选择日期'); title.id = 'web-date-title';
    const dismiss = button('×', 'wx-date-dismiss', () => close()); dismiss.setAttribute('aria-label', '关闭日期选择');
    header.append(title, dismiss);
    const help = element('p', 'wx-date-help', mode === 'time' ? '24 小时制，精确到分钟' : '选择一天即可回填，也可直接输入日期'); help.id = 'web-date-help';
    const input = element('input', 'wx-date-input'); input.type = 'text'; input.autocomplete = 'off';
    input.placeholder = mode === 'time' ? 'HH:mm，例如 17:00' : 'YYYY-MM-DD，例如 2026-09-15';
    input.setAttribute('aria-label', mode === 'time' ? '输入时间，24小时制' : '输入日期，年-月-日');
    input.setAttribute('aria-describedby', 'web-date-error'); input.spellcheck = false;
    const error = element('p', 'wx-date-error'); error.id = 'web-date-error'; error.setAttribute('role', 'alert'); error.hidden = true;
    const mount = element('div', 'wx-date-calendar');
    // Keep manual entry separate: Flatpickr's input blur must never commit while clicking Cancel.
    const calendarInput = element('input'); calendarInput.hidden = true; calendarInput.tabIndex = -1;
    calendarInput.setAttribute('aria-hidden', 'true'); mount.append(calendarInput);
    const shortcuts = element('div', 'wx-date-shortcuts'); shortcuts.setAttribute('aria-label', '快捷选择');
    const footer = element('footer', 'wx-date-footer');
    const record = {anchor, dialog, originalValue: String(value), mode, min, max, picker: null}; active = record;
    const showError = () => {
      input.setAttribute('aria-invalid', 'true'); error.hidden = false;
      error.textContent = !parse(input.value.trim(), mode) ? (mode === 'time' ? '请输入有效时间，例如 17:00。' : '请输入有效日期，例如 2026-09-15。') :
        `请选择${min ? '不早于 ' + min : ''}${min && max ? '、' : ''}${max ? '不晚于 ' + max : ''}的${mode === 'time' ? '时间' : '日期'}。`;
    };
    const commit = text => {
      if (active !== record) return;
      if (!valid(text, record)) { showError(); return; }
      if (!anchor.isConnected || anchor.disabled) { close({restoreFocus: false}); return; }
      close(); onCommit(text);
    };
    const syncDraft = text => { input.value = text; input.removeAttribute('aria-invalid'); error.hidden = true; };
    input.addEventListener('input', () => {
      input.removeAttribute('aria-invalid'); error.hidden = true;
      if (mode === 'time' && valid(input.value.trim(), record)) record.picker?.setDate(parse(input.value.trim(), mode), false);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault(); event.stopImmediatePropagation(); commit(input.value.trim());
      }
    }, true);
    // Reject impossible calendar dates instead of normalizing e.g. February 31 into March.
    input.addEventListener('blur', event => {
      if (input.value && !valid(input.value.trim(), record)) { event.stopImmediatePropagation(); showError(); }
    }, true);
    footer.append(button('取消', 'wx-date-cancel', () => close()), button(mode === 'time' ? '确定时间' : '使用输入日期', 'wx-date-apply', () => commit(input.value.trim())));
    dialog.append(header, help, input, error, mount, shortcuts, footer); document.body.append(dialog);
    record.position = () => {
      if (global.innerWidth <= 600) return;
      const bounds = anchor.getBoundingClientRect(), width = dialog.offsetWidth, height = dialog.offsetHeight;
      dialog.style.left = Math.max(8, Math.min(bounds.left, global.innerWidth - width - 8)) + 'px';
      const below = bounds.bottom + 8;
      dialog.style.top = Math.max(8, Math.min(below + height <= global.innerHeight - 8 ? below : bounds.top - height - 8, global.innerHeight - height - 8)) + 'px';
    };
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.target.matches('.flatpickr-monthDropdown-months') && event.key !== 'Tab') {
        event.stopPropagation();
        if (!event.altKey && !event.ctrlKey && !event.metaKey && ['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const select = event.target, choices = [...select.options].filter(option => !option.disabled);
          const index = choices.findIndex(option => option.value === select.value);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 :
            Math.max(0, Math.min(choices.length - 1, index + (event.key === 'ArrowUp' ? -1 : 1)));
          if (choices[next] && select.value !== choices[next].value) { select.value = choices[next].value; select.dispatchEvent(new Event('change', {bubbles: true})); }
        }
      }
      if (event.key === 'Tab') {
        // Flatpickr assumes its source input is focusable. This dialog uses a separate manual field.
        const stops = [...dialog.querySelectorAll('button,input,select,[tabindex]')].filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
        if (!stops.length) return;
        const focused = document.activeElement, index = stops.indexOf(focused);
        let next;
        if (index >= 0) next = stops[(index + (event.shiftKey ? -1 : 1) + stops.length) % stops.length];
        else next = event.shiftKey ? stops.filter(el => el.compareDocumentPosition(focused) & Node.DOCUMENT_POSITION_FOLLOWING).at(-1) || stops.at(-1) :
          stops.find(el => focused.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) || stops[0];
        event.preventDefault(); event.stopImmediatePropagation(); next.focus();
      }
    }, true);
    dialog.addEventListener('change', event => {
      if (event.target.matches('.flatpickr-monthDropdown-months')) queueMicrotask(() => {
        if (active === record) record.picker.monthsDropdownContainer.focus();
      });
    }, true);
    dialog.addEventListener('click', event => {
      const box = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) close();
    });
    anchor.setAttribute('aria-expanded', 'true');
    dialog.showModal();
    const initial = parse(value, mode);
    input.value = initial ? String(value) : '';
    record.picker = global.flatpickr(calendarInput, {
      locale: {...global.flatpickr.l10ns.zh, firstDayOfWeek: 1},
      inline: true, appendTo: mount, clickOpens: false, allowInput: false, disableMobile: true,
      dateFormat: mode === 'time' ? 'H:i' : 'Y-m-d', ariaDateFormat: 'Y年n月j日 l',
      noCalendar: mode === 'time', enableTime: mode === 'time', time_24hr: true, minuteIncrement: 1,
      defaultDate: initial || undefined,
      minDate: mode === 'date' ? parse(min, mode) || undefined : undefined,
      maxDate: mode === 'date' ? parse(max, mode) || undefined : undefined,
      minTime: mode === 'time' ? min || undefined : undefined,
      maxTime: mode === 'time' ? max || undefined : undefined,
      parseDate: text => parse(text, mode) || undefined,
      errorHandler: () => {},
      onReady: (dates, text, picker) => enableMonthKeyboard(picker),
      onMonthChange: (dates, text, picker) => queueMicrotask(() => { if (active === record) enableMonthKeyboard(picker); }),
      onYearChange: (dates, text, picker) => queueMicrotask(() => { if (active === record) enableMonthKeyboard(picker); }),
      // onValueUpdate is synchronous; time's onChange is debounced and can arrive after Confirm.
      onValueUpdate: (dates, text) => { if (mode === 'time' && active === record && dates.length) syncDraft(text); },
      onChange: (dates, text) => {
        if (active !== record || !dates.length || mode !== 'date') return;
        syncDraft(text); queueMicrotask(() => commit(text));
      }
    });
    function enableMonthKeyboard(picker) {
      if (mode !== 'date') return;
      picker.currentYearElement.tabIndex = 0; picker.monthsDropdownContainer.tabIndex = 0;
      picker.currentYearElement.setAttribute('aria-label', '年份'); picker.monthsDropdownContainer.setAttribute('aria-label', '月份');
    }
    if (mode === 'date') {
      [['今天', 0], ['明天', 1], ['一周后', 7]].forEach(([label, offset]) => {
        const date = new Date(); date.setDate(date.getDate() + offset); const text = dateText(date);
        const shortcut = button(label, '', () => commit(text)); shortcut.disabled = !valid(text, record); shortcut.title = text; shortcuts.append(shortcut);
      });
      if (min || max) {
        const bounds = element('p', 'wx-date-range', `${min || '不限起始'} 至 ${max || '不限结束'}`);
        bounds.setAttribute('aria-label', '可选日期范围'); shortcuts.after(bounds);
      }
    } else {
      ['09:00', '14:00', '18:00'].forEach(text => {
        const shortcut = button(text, '', () => { record.picker.setDate(parse(text, mode), false); input.value = text; input.removeAttribute('aria-invalid'); error.hidden = true; });
        shortcut.disabled = !valid(text, record); shortcuts.append(shortcut);
      });
      record.picker.hourElement?.setAttribute('aria-label', '小时，0到23');
      record.picker.minuteElement?.setAttribute('aria-label', '分钟，0到59');
      record.picker.hourElement.tabIndex = 0; record.picker.minuteElement.tabIndex = 0;
      const syncTimeFields = () => syncDraft(`${pad(record.picker.hourElement.value)}:${pad(record.picker.minuteElement.value)}`);
      // Typing in a time field must update the draft before blur or an immediate Confirm click.
      record.picker.timeContainer.addEventListener('input', syncTimeFields);
      record.picker.timeContainer.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault(); event.stopImmediatePropagation(); syncTimeFields(); commit(input.value.trim());
        }
        // Inline Flatpickr cycles Tab inside its time fields. Let the dialog's native focus order proceed.
        if (event.key === 'Tab') event.stopPropagation();
      }, true);
    }
    record.position(); global.addEventListener('resize', record.position);
    record.resizeObserver = new ResizeObserver(record.position); record.resizeObserver.observe(dialog);
    // The first focus goes to the selected day on desktop; mobile keeps the keyboard closed.
    if (mode === 'date' && record.picker.selectedDateElem && global.innerWidth > 600) record.picker.selectedDateElem.focus();
    else dismiss.focus({preventScroll: true});
  }
  global.SalesDatePicker = Object.freeze({open, close, reconcile});
})(window);
