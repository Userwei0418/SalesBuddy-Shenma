const DOCUMENT_TYPES = new Set(['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf']);
const IMAGE_TYPES = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']);
const MIME_EXTENSIONS = {
  'application/msword': 'doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
  'application/pdf': 'pdf',
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/bmp': 'bmp',
};

function extension(file = {}) {
  const name = typeof file.filename === 'string' ? file.filename : '';
  const match = /\.([A-Za-z0-9]{1,12})$/.exec(name);
  const fromName = match ? match[1].toLowerCase() : '';
  if (fromName) return fromName;
  const mediaType = typeof file.media_type === 'string' ? file.media_type.toLowerCase().split(';', 1)[0] : '';
  return MIME_EXTENSIONS[mediaType] || '';
}

function documentType(file = {}) {
  const ext = extension(file);
  return DOCUMENT_TYPES.has(ext) ? ext : '';
}

function isImage(file = {}) {
  const ext = extension(file);
  if (IMAGE_TYPES.has(ext)) return true;
  return typeof file.media_type === 'string' && file.media_type.toLowerCase().startsWith('image/');
}

function canOpen(file = {}) {
  return Boolean(documentType(file) || isImage(file));
}

function shouldPreview(file = {}) {
  // The server only permits preview for a narrow set of media. PDF is the
  // only format that wx.openDocument can render from that endpoint directly.
  return Boolean(file.preview_supported === true && extension(file) === 'pdf');
}

function safeFilename(file = {}, fallback = 'knowledge-file') {
  const raw = typeof file.filename === 'string' && file.filename.trim() ? file.filename.trim() : fallback;
  return raw.replace(/[\\/\x00-\x1f\x7f]/g, '_').slice(0, 180) || fallback;
}

module.exports = { DOCUMENT_TYPES, IMAGE_TYPES, extension, documentType, isImage, canOpen, shouldPreview, safeFilename };
