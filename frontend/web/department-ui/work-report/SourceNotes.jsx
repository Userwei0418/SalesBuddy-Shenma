import React from 'react';
import {sourceDisplayText} from './source-display.js';

// Only render notes already authorized in the saved revision. Opening a note
// performs no fetch or mutation; all fields remain escaped, literal text.
export default function SourceNotes({notes}) {
  if (!notes?.length) return null;
  return <div className="wr-source-notes" aria-label="来源说明">
    {notes.map((note,index)=><section className="wr-source-note" key={index}>
      <div className="wr-source-note-heading"><span>来源说明</span><strong>{note.title}</strong></div>
      <p className="wr-source-note-notice">{note.notice_text}</p>
      <details className="wr-source-note-details" data-presentation-interactive>
        <summary>事实与来源<span className="wr-source-note-hint"> · 展开核对冻结材料</span></summary>
        <p>{sourceDisplayText(note.text,note.source_refs)}</p>
      </details>
    </section>)}
  </div>;
}
