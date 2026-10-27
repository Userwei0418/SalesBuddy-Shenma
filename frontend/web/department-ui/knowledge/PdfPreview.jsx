import React, {useEffect, useRef, useState} from 'react';
import {getDocument, GlobalWorkerOptions, AnnotationMode, version} from 'pdfjs-dist/legacy/build/pdf.mjs';
import './pdf-preview.css';

// Resolved relative to our own entry, including the production /web/ mount.
// Only the controlled response bytes go to PDF.js; no provider URL, link layer,
// scripting manager or XFA UI is created.
const entry = typeof document !== 'undefined' && document.currentScript?.src;
const assets = typeof document !== 'undefined' ? new URL(`pdfjs/${version}/`, entry ? new URL('../', entry) : document.baseURI).href : '';
GlobalWorkerOptions.workerSrc = assets + 'pdf.worker.min.js';

export default function PdfPreview({blob}) {
  const host = useRef(null), canvas = useRef(null);
  const [pdf, setPdf] = useState(null), [page, setPage] = useState(1), [width, setWidth] = useState(320);
  const [loading, setLoading] = useState(true), [rendering, setRendering] = useState(false), [error, setError] = useState('');
  useEffect(() => {
    let active = true, task;
    setPdf(null); setPage(1); setLoading(true); setError('');
    (async () => {
      try {
        const data = new Uint8Array(await blob.arrayBuffer());
        if (!active) return;
        task = getDocument({data, cMapUrl: assets + 'cmaps/', standardFontDataUrl: assets + 'standard_fonts/', wasmUrl: assets + 'wasm/', iccUrl: assets + 'iccs/',
          enableXfa: false, stopAtErrors: true, maxImageSize: 32 * 1024 * 1024, canvasMaxAreaInBytes: 16 * 1024 * 1024});
        task.onPassword = () => {if (active) {setError('此 PDF 需要密码，请联系分享人提供可查看的版本。'); setLoading(false);} void task.destroy().catch(() => {});};
        const document = await task.promise;
        if (active) {setPdf(document); setLoading(false);}
      } catch (_) {if (active) {setError(previous => previous || 'PDF 加载失败，请关闭后重新读取；若仍失败，请联系资料提供人。'); setLoading(false);}}
    })();
    return () => {active = false; if (task) void task.destroy().catch(() => {});};
  }, [blob]);
  useEffect(() => {
    const node = host.current;
    const resize = () => setWidth(Math.max(1, Math.floor(node.clientWidth)));
    resize(); const observer = new ResizeObserver(resize); observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!pdf) return;
    let active = true, task;
    // Each render owns its canvas, so cancelled/older pages cannot paint over a
    // new page even if PDF.js takes another microtask to finish cancelling.
    const scratch = document.createElement('canvas'), visible = canvas.current;
    visible.width = 0; visible.height = 0; setRendering(true); setError('');
    (async () => {
      try {
        const current = await pdf.getPage(page); if (!active) return;
        const natural = current.getViewport({scale: 1});
        const scale = Math.min(width / natural.width, 2, Math.sqrt(8_000_000 / (natural.width * natural.height)));
        const viewport = current.getViewport({scale});
        const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8_000_000 / (viewport.width * viewport.height)));
        scratch.width = Math.ceil(viewport.width * ratio); scratch.height = Math.ceil(viewport.height * ratio);
        task = current.render({canvasContext: scratch.getContext('2d'), viewport, transform: [ratio, 0, 0, ratio, 0, 0], annotationMode: AnnotationMode.DISABLE});
        await task.promise; if (!active) return;
        visible.width = scratch.width; visible.height = scratch.height;
        visible.style.width = `${Math.floor(viewport.width)}px`; visible.style.height = `${Math.floor(viewport.height)}px`;
        visible.getContext('2d').drawImage(scratch, 0, 0); setRendering(false);
      } catch (_) {if (active) {setError('这一页无法显示，请切换页面或关闭后重新读取。'); setRendering(false);}}
      finally {scratch.width = 0; scratch.height = 0;}
    })();
    return () => {active = false; task?.cancel(); visible.width = 0; visible.height = 0;};
  }, [pdf, page, width]);
  return <div className="kb-pdf" ref={host}>
    {loading && <p role="status">正在加载 PDF…</p>}
    {pdf && <div className="kb-pdf-toolbar"><button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span aria-live="polite">第 {page} / {pdf.numPages} 页</span><button disabled={page >= pdf.numPages} onClick={() => setPage(value => value + 1)}>下一页</button></div>}
    {rendering && <p role="status">正在绘制页面…</p>}{error && <p role="alert">{error}</p>}
    <canvas ref={canvas} width={0} height={0} aria-label={`PDF 第 ${page} 页`} />
  </div>;
}
