const extensions = new Set(['jpg', 'jpeg', 'png', 'webp', 'mp4', 'webm', 'mov', 'mp3', 'wav', 'm4a', 'ogg']);
export const MAX_IMPORT_FILES = 500;
const MAX_FILE = 100 * 1024 * 1024;
const MAX_EXPANDED = 250 * 1024 * 1024;
const natural = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
export const supportedMedia = name => extensions.has(name.split('.').pop().toLowerCase());

export function folderFiles(files) {
  if (files.length > 5000) throw new Error('Choose a folder with at most 5000 files.');
  const ordered = [...files].sort((a, b) => natural.compare(a.webkitRelativePath || a.name, b.webkitRelativePath || b.name));
  const skipped = ordered.filter(file => !supportedMedia(file.name)).map(file => file.webkitRelativePath || file.name);
  const supported = ordered.filter(file => supportedMedia(file.name));
  if (supported.length > MAX_IMPORT_FILES) throw new Error('Import at most 500 supported assets at a time.');
  for (const file of supported) {
    if (!file.size || file.size > MAX_FILE) throw new Error(file.name + ' must contain between 1 byte and 100 MB.');
  }
  return { files: supported.map(file => ({ file, label: file.webkitRelativePath || file.name, slides: [] })), skipped };
}

function xml(bytes, name) {
  const source = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  if (/<!DOCTYPE|<!ENTITY/i.test(source)) throw new Error(name + ': XML entities are not supported.');
  const document = new DOMParser().parseFromString(source, 'application/xml');
  if (document.getElementsByTagName('parsererror').length) throw new Error(name + ': invalid PowerPoint XML.');
  return document;
}
function nodes(document, name) { return [...document.getElementsByTagNameNS('*', name)]; }
function relationshipId(node) {
  return [...node.attributes].find(attribute => attribute.localName === 'id' && attribute.namespaceURI?.includes('relationships'))?.value;
}
function targetPath(base, target) {
  if (!target || /^[a-z]+:|^[/\\]/i.test(target) || target.includes('\\') || target.includes('\0')) {
    throw new Error('Invalid embedded PowerPoint media path.');
  }
  const parts = base.split('/').slice(0, -1);
  for (const part of target.split('/')) {
    if (part === '..') {
      if (!parts.length) throw new Error('PowerPoint relationship escapes the document.');
      parts.pop();
    } else if (part && part !== '.') parts.push(part);
  }
  return parts.join('/');
}
function relationships(document, base) {
  const result = new Map();
  for (const node of nodes(document, 'Relationship')) {
    const id = node.getAttribute('Id');
    if (!id || result.has(id)) throw new Error('Invalid or duplicate PowerPoint relationship.');
    result.set(id, node.getAttribute('TargetMode') === 'External' ? null : targetPath(base, node.getAttribute('Target')));
  }
  return result;
}

const crcTable = Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  return value >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
  return (crc ^ 0xffffffff) >>> 0;
}

// Read only ZIP members needed for extraction; never write archive paths to disk.
async function openZip(file) {
  if (!file.size || file.size > MAX_FILE) throw new Error('Choose a .pptx document of at most 100 MB.');
  const bytes = new Uint8Array(await file.arrayBuffer()), view = new DataView(bytes.buffer);
  const bounds = (at, length) => {
    if (at < 0 || at + length > bytes.length) throw new Error('The PowerPoint archive is truncated.');
  };
  let end = -1;
  for (let at = bytes.length - 22; at >= Math.max(0, bytes.length - 65557); at--) {
    if (view.getUint32(at, true) === 0x06054b50 && at + 22 + view.getUint16(at + 20, true) === bytes.length) {
      end = at; break;
    }
  }
  if (end < 0) throw new Error('Not a readable .pptx ZIP archive. Resave it as PowerPoint (.pptx), not .ppt.');
  const count = view.getUint16(end + 10, true), offset = view.getUint32(end + 16, true);
  const directorySize = view.getUint32(end + 12, true);
  if (view.getUint16(end + 4, true) || view.getUint16(end + 6, true) ||
      view.getUint16(end + 8, true) !== count || count > 10000 ||
      offset + directorySize > end) throw new Error('Split, ZIP64 or unusually large PowerPoint archives are not supported.');
  const entries = new Map();
  let cursor = offset, expanded = 0;
  for (let index = 0; index < count; index++) {
    bounds(cursor, 46);
    if (view.getUint32(cursor, true) !== 0x02014b50) throw new Error('Invalid PowerPoint ZIP directory.');
    const nameSize = view.getUint16(cursor + 28, true), extraSize = view.getUint16(cursor + 30, true), commentSize = view.getUint16(cursor + 32, true);
    bounds(cursor + 46, nameSize + extraSize + commentSize);
    const name = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(cursor + 46, cursor + 46 + nameSize));
    if (name.includes('\\') || name.startsWith('/') || name.split('/').includes('..') || name.includes('\0') || entries.has(name)) {
      throw new Error('Invalid or duplicate file path in PowerPoint.');
    }
    const entry = { name, flags: view.getUint16(cursor + 8, true), method: view.getUint16(cursor + 10, true),
      crc: view.getUint32(cursor + 16, true), compressed: view.getUint32(cursor + 20, true),
      size: view.getUint32(cursor + 24, true), offset: view.getUint32(cursor + 42, true) };
    if (entry.flags & 1 || ![0, 8].includes(entry.method)) throw new Error('Encrypted or unsupported PowerPoint ZIP compression.');
    expanded += entry.size;
    if (expanded > MAX_EXPANDED || entry.size > MAX_FILE || entry.offset >= offset) {
      throw new Error('PowerPoint expands beyond the 250 MB document / 100 MB per-file limits.');
    }
    entries.set(name, entry);
    cursor += 46 + nameSize + extraSize + commentSize;
  }
  if (cursor !== offset + directorySize) throw new Error('Invalid PowerPoint directory length.');
  async function read(name) {
    const entry = entries.get(name);
    if (!entry) throw new Error('PowerPoint is missing ' + name + '.');
    bounds(entry.offset, 30);
    if (view.getUint32(entry.offset, true) !== 0x04034b50 ||
        view.getUint16(entry.offset + 8, true) !== entry.method || view.getUint16(entry.offset + 6, true) !== entry.flags) {
      throw new Error('Invalid embedded PowerPoint member.');
    }
    const nameSize = view.getUint16(entry.offset + 26, true), extraSize = view.getUint16(entry.offset + 28, true);
    bounds(entry.offset + 30, nameSize + extraSize);
    const localName = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(entry.offset + 30, entry.offset + 30 + nameSize));
    const start = entry.offset + 30 + nameSize + extraSize;
    bounds(start, entry.compressed);
    if (localName !== name || start + entry.compressed > offset) throw new Error('PowerPoint member paths or sizes disagree.');
    const data = bytes.subarray(start, start + entry.compressed);
    let result;
    if (entry.method === 0) result = data;
    else {
      if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot extract PowerPoint. Use a current Chrome, Edge, Firefox or Safari.');
      let decompressor;
      try { decompressor = new DecompressionStream('deflate-raw'); }
      catch (error) { throw new Error('This browser cannot extract PowerPoint ZIP compression. Update your browser or import exported slide PNGs. ' + error.message); }
      const reader = new Blob([data]).stream().pipeThrough(decompressor).getReader();
      const chunks = [];
      let size = 0;
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.length;
          if (size > entry.size) throw new Error('PowerPoint member exceeds its declared size.');
          chunks.push(chunk.value);
        }
      } finally { await reader.cancel(); }
      result = new Uint8Array(size);
      let at = 0;
      for (const chunk of chunks) { result.set(chunk, at); at += chunk.length; }
    }
    if (result.length !== entry.size || crc32(result) !== entry.crc) throw new Error(name + ': damaged PowerPoint member.');
    return result;
  }
  return { entries, read };
}

export async function powerpointFiles(file) {
  if (!/\.pptx$/i.test(file.name)) throw new Error('Choose a .pptx PowerPoint document. Legacy .ppt files must be resaved first.');
  const zip = await openZip(file);
  const readXml = async name => {
    if ((zip.entries.get(name)?.size || 0) > 5 * 1024 * 1024) throw new Error('PowerPoint XML is too large to import safely.');
    return xml(await zip.read(name), name);
  };
  const document = await readXml('ppt/presentation.xml');
  const slideLinks = relationships(await readXml('ppt/_rels/presentation.xml.rels'), 'ppt/presentation.xml');
  const used = new Map(), warnings = new Set();
  const slides = nodes(document, 'sldId');
  if (slides.length > 1000) throw new Error('Import a PowerPoint with at most 1000 slides.');
  for (const [index, slide] of slides.entries()) {
    const path = slideLinks.get(relationshipId(slide));
    if (!path || !/^ppt\/slides\/[^/]+\.xml$/.test(path)) throw new Error('PowerPoint has an invalid slide reference.');
    const slideDocument = await readXml(path);
    const relPath = path.replace(/([^/]+)$/, '_rels/$1.rels');
    const links = zip.entries.has(relPath) ? relationships(await readXml(relPath), path) : new Map();
    for (const node of [...slideDocument.getElementsByTagName('*')]) {
      for (const attribute of [...node.attributes]) {
        if (!attribute.namespaceURI?.includes('relationships')) continue;
        if (!links.has(attribute.value)) throw new Error('Slide ' + (index + 1) + ': missing PowerPoint relationship ' + attribute.value + '.');
        const target = links.get(attribute.value);
        if (target === null) { warnings.add('Slide ' + (index + 1) + ': external links are not imported.'); continue; }
        if (!target?.startsWith('ppt/media/')) continue;
        if (!zip.entries.has(target)) throw new Error('PowerPoint is missing embedded media: ' + target);
        if (!used.has(target)) used.set(target, []);
        if (!used.get(target).includes(index + 1)) used.get(target).push(index + 1);
      }
    }
  }
  const media = [...zip.entries.keys()].filter(name => name.startsWith('ppt/media/') && !name.endsWith('/'));
  const ordered = [...used.keys(), ...media.filter(name => !used.has(name)).sort(natural.compare)];
  const skipped = ordered.filter(name => !supportedMedia(name));
  const supported = ordered.filter(supportedMedia);
  if (supported.length > MAX_IMPORT_FILES) throw new Error('PowerPoint contains more than 500 supported assets. Split it into smaller documents.');
  const files = [];
  for (const name of supported) {
    const data = await zip.read(name);
    if (!data.length) { skipped.push(name + ' (empty)'); continue; }
    const slideNumbers = used.get(name) || [];
    files.push({ file: new File([data], name.split('/').pop()), slides: slideNumbers,
      label: name.split('/').pop() + (slideNumbers.length ? ' — slides ' + slideNumbers.join(', ') : ' — shared/unused media') });
  }
  return { files, skipped, warnings: [...warnings] };
}
