import React, {useEffect, useState} from 'react';
import PdfPreview from './PdfPreview.jsx';

function TextPreview({blob}) {
  const [text, setText] = useState(null), [error, setError] = useState('');
  useEffect(() => {let active = true; setText(null); setError(''); blob.text().then(value => {if (active) setText(value);}, () => {if (active) setError('正文读取失败，请关闭后重新读取。');}); return () => {active = false;};}, [blob]);
  return error ? <p role="alert">{error}</p> : text === null ? <p role="status">正在读取正文…</p> : <pre>{text}</pre>;
}
function ImagePreview({blob, name}) {
  const [url, setUrl] = useState('');
  useEffect(() => {const next = URL.createObjectURL(blob); setUrl(next); return () => URL.revokeObjectURL(next);}, [blob]);
  return url ? <img className="kb-file-preview-image" src={url} alt={name || '资料图片'} /> : <p role="status">正在读取图片…</p>;
}
export default function FilePreview({name, blob, onClose}) {
  const mime = (blob?.type || '').split(';')[0].trim().toLowerCase();
  return <section className="kb-file-preview" aria-label="资料正文"><div className="kb-file-preview-header"><h3>{name}</h3><button onClick={onClose}>关闭正文</button></div>
    {!blob ? <p role="status">正在读取文件…</p> : mime === 'application/pdf' ? <PdfPreview blob={blob}/> : /^text\/(plain|markdown|csv)$/.test(mime) ? <TextPreview blob={blob}/> : /^image\/(png|jpeg|webp)$/.test(mime) ? <ImagePreview blob={blob} name={name}/> : <p role="alert">此格式暂不支持在线预览，请按资料权限下载查看。</p>}
  </section>;
}
