import React,{useState} from 'react';
import DocumentEditor from './DocumentEditor.jsx';
import DocumentReader from './DocumentReader.jsx';

export default function DocumentView(props) {
  const [editing,setEditing]=useState(false);
  // Switching presentation never discards edits, saves, shares, or regenerates.
  // The parent retains the draft and its leave/permission/version guards.
  return editing && !props.readOnly
    ? <DocumentEditor {...props} onPreview={()=>setEditing(false)}/>
    : <DocumentReader {...props} onEdit={!props.readOnly?()=>setEditing(true):undefined}/>;
}
