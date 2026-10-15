import React, {Children, useLayoutEffect, useRef, useState} from 'react';
import {Button, Popover} from 'antd';
import {visibleControlCount} from './toolbar-layout.js';

// The original controls move into the popover; values and business callbacks stay owned by the page.
export default function FilterBar({children, search, actions, className = '', label = '筛选条件', moreLabel = '更多筛选'}) {
  const root = useRef(null), actionRef = useRef(null);
  const [size, setSize] = useState({width: 0, actions: 0});
  const [open, setOpen] = useState(false);
  const [popover, setPopover] = useState({placement: 'bottomRight', maxHeight: 520});
  const items = Children.toArray(children).filter(React.isValidElement);
  const widths = items.map(item => Number(item.props?.['data-toolbar-width']) || 144);
  useLayoutEffect(() => {
    const measure = () => setSize({width: root.current?.clientWidth || 0, actions: actionRef.current?.offsetWidth || 0});
    measure();
    const observer = new ResizeObserver(measure);
    if (root.current) observer.observe(root.current);
    if (actionRef.current) observer.observe(actionRef.current);
    return () => observer.disconnect();
  }, [Boolean(actions)]);
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const rect = root.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const placement = below >= above ? 'bottomRight' : 'topRight';
      // Reserve the popup padding and anchor gap; the remaining controls scroll inside it.
      setPopover({placement, maxHeight: Math.max(80, Math.min(520, Math.max(below, above) - 36))});
    };
    measure();
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [open]);
  const searchWidth = search ? Math.min(180, Math.max(100, size.width - size.actions - 128)) : 0;
  const count = visibleControlCount(size.width, widths, searchWidth, size.actions);
  const renderItem = (item, index) => <div key={item.key || index} className="pc-filterbar-item" style={{'--filter-width': `${widths[index]}px`}}>{item}</div>;
  return <div className={`pc-filterbar ${className}`} role="group" aria-label={label}>
    <div className="pc-filterbar-row" ref={root}>
      {search && <div className="pc-filterbar-search" style={{minWidth: searchWidth}}>{search}</div>}
      {items.slice(0, count).map(renderItem)}
      {count < items.length && <Popover trigger="click" placement={popover.placement} open={open} onOpenChange={setOpen}
        classNames={{root: 'department-ui pc-filterbar-popover'}}
        content={<div className="pc-filterbar-overflow" style={{'--overflow-width': `${Math.max(280, ...widths.slice(count))}px`, maxHeight: popover.maxHeight}} role="group" aria-label={moreLabel} onKeyDown={event => { if (event.key === 'Escape') setOpen(false); }}>
          {items.slice(count).map((item, index) => renderItem(item, count + index))}
          <Button className="pc-filterbar-done" onClick={() => setOpen(false)}>收起</Button>
        </div>}>
        <Button className="pc-filterbar-more" aria-expanded={open}>{moreLabel}<span className="pc-filterbar-count">{items.length - count}</span></Button>
      </Popover>}
      {actions && <div className="pc-filterbar-actions" ref={actionRef}>{actions}</div>}
    </div>
  </div>;
}
