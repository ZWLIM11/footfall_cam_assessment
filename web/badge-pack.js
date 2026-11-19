const names = ['front.png', 'dark.png', 'light.png'];
const storageKey = 'footfallcam.badge-pack.v1';

export function parseBadgePack(text) {
  if (text.length > 50_000) throw new Error('The badge pack is too large. Use the three small PNG badge crops.');
  let pack;
  try { pack = JSON.parse(text.replace(/^\uFEFF/, '')); }
  catch { throw new Error('Choose a valid badge-pack JSON file.'); }
  const badges = {};
  for (const name of names) {
    const encoded = pack?.[name];
    try {
      if (typeof encoded !== 'string' || encoded.length > 16_000) throw new Error();
      const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
      if (bytes.length < 40 || ![137, 80, 78, 71, 13, 10, 26, 10].every((value, i) => bytes[i] === value) ||
          String.fromCharCode(...bytes.subarray(12,16)) !== 'IHDR') throw new Error();
      const header = new DataView(bytes.buffer);
      const width = header.getUint32(16), height = header.getUint32(20);
      if (Math.min(width,height) < 5 || Math.max(width,height) > 256) throw new Error();
      badges[name] = bytes;
    } catch { throw new Error(`The badge pack needs a valid small PNG named ${name}.`); }
  }
  return badges;
}

export function loadSavedBadgePack(storage) {
  try {
    const saved = (storage ?? globalThis.localStorage).getItem(storageKey);
    return saved ? parseBadgePack(saved) : undefined;
  } catch { return undefined; }
}

export function rememberBadgePack(badges, storage) {
  const crops = Object.fromEntries(names.map(name => [name, btoa(String.fromCharCode(...badges[name]))]));
  (storage ?? globalThis.localStorage).setItem(storageKey, JSON.stringify(crops));
}

export function forgetSavedBadgePack(storage) {
  (storage ?? globalThis.localStorage).removeItem(storageKey);
}
