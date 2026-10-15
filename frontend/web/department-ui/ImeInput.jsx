import React, {createContext, forwardRef, useContext, useLayoutEffect, useRef, useState} from 'react';
import {Input as AntInput} from 'antd';
import {SbSearch as DepartmentSearch, SbTextarea as DepartmentTextarea} from '@shandiant/ui-react';

// A new token means the host has applied the business handler's result. Local
// keystrokes must render immediately, without waiting for its animation frame.
export const InputSyncContext = createContext(null);
const text = value => value == null ? '' : String(value);
const isComposing = event => Boolean(event?.isComposing || event?.nativeEvent?.isComposing || event?.keyCode === 229 || event?.nativeEvent?.keyCode === 229);

function useImeValue(value, onChange, valueCallback = false) {
  const revision = useContext(InputSyncContext);
  const [draft, setDraft] = useState(() => text(value));
  const composing = useRef(false);
  const committed = useRef(null);
  const composingTarget = useRef(null);
  const flush = useRef(null);
  useLayoutEffect(() => {
    const onFlush = () => flush.current?.();
    window.addEventListener('sales:flush-draft-inputs', onFlush);
    return () => window.removeEventListener('sales:flush-draft-inputs', onFlush);
  }, []);
  useLayoutEffect(() => {
    if (!composing.current) setDraft(text(value));
  }, [value, revision]);
  const publish = (next, event) => {
    if (valueCallback) onChange?.(next, event);
    else onChange?.(event);
  };
  const change = (next, event) => {
    next = text(next);
    setDraft(next);
    if (composing.current || isComposing(event)) return;
    const duplicate = committed.current === next;
    committed.current = null;
    if (!duplicate) publish(next, event);
  };
  const finish = event => {
    if (!composing.current) return;
    composing.current = false;
    const next = text(event.target.value);
    setDraft(next);
    committed.current = next; // Some browsers emit another input after compositionend.
    publish(next, event);
  };
  // A tab can close while the Chinese IME is still composing, before blur or
  // compositionend. Only visit draft lifecycle dispatches this explicit flush.
  flush.current = () => {if (composing.current && composingTarget.current) finish({target: composingTarget.current});};
  return {
    value: draft,
    change,
    start(event) { composing.current = true; committed.current = null; composingTarget.current = event?.target; },
    finish,
    blockEnter(event) {
      if (event.key === 'Enter' && (composing.current || isComposing(event))) {
        // Let the IME confirm its candidate; only stop application key handlers.
        event.stopPropagation();
        return true;
      }
      return false;
    },
    searching() { return composing.current; },
  };
}

function adapt(Component, valueCallback = false) {
  return forwardRef(function ImeInput({value, onChange, onCompositionStart, onCompositionEnd, onKeyDownCapture, onBlur, onSearch, ...props}, ref) {
    const ime = useImeValue(value, onChange, valueCallback);
    return <Component {...props} ref={ref} value={ime.value}
      onChange={valueCallback ? (next, event) => ime.change(next, event) : event => ime.change(event.target.value, event)}
      onCompositionStart={event => {ime.start(event); onCompositionStart?.(event);}}
      onCompositionEnd={event => {ime.finish(event); onCompositionEnd?.(event);}}
      onKeyDownCapture={event => {if (!ime.blockEnter(event)) onKeyDownCapture?.(event);}}
      onBlur={event => {ime.finish(event); onBlur?.(event);}}
      {...(onSearch ? {onSearch: (...args) => {if (!ime.searching()) onSearch(...args);}} : {})} />;
  });
}

export const Input = adapt(AntInput);
Input.Password = adapt(AntInput.Password);
Input.TextArea = adapt(AntInput.TextArea);
Input.Search = adapt(AntInput.Search);
export const SbTextarea = adapt(DepartmentTextarea, true);

// SbSearch 0.7.0 does not forward composition/key props. Capture them on this
// layout-neutral React boundary while retaining the installed Sb component.
export function SbSearch({value, onChange, onSearch, ...props}) {
  const ime = useImeValue(value, onChange, true);
  return <span style={{display: 'contents'}} onCompositionStartCapture={ime.start} onCompositionEndCapture={ime.finish}
    onKeyDownCapture={ime.blockEnter} onBlurCapture={ime.finish}>
    <DepartmentSearch {...props} value={ime.value} onChange={next => ime.change(next)}
      onSearch={next => {if (!ime.searching()) onSearch?.(next);}} />
  </span>;
}
