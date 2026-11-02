// Shrinks photos on the phone itself, for uploading to a project repo.
// Nothing leaves the device: each photo is decoded, scaled with a canvas and
// re-encoded as JPEG, which also drops its metadata (GPS location included).

const QUALITY = 0.82;          // JPEG quality; dropped to 0.72 if a photo is still big
const MAX_BYTES = 900 * 1024;  // aim to keep each photo under this

const input = document.getElementById('pick');
const sizeSelect = document.getElementById('size');
const list = document.getElementById('results');
const actions = document.getElementById('actions');
const saveAll = document.getElementById('save-all');
const summary = document.getElementById('summary');

let done = [];  // { file: File, url: string }

const kb = (n) => n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

// "IMG_1234.HEIC" -> "img-1234.jpg": lowercase, no spaces, safe in a web address.
function outName(name, used) {
  let base = name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'photo';
  let candidate = `${base}.jpg`;
  for (let i = 2; used.has(candidate); i++) candidate = `${base}-${i}.jpg`;
  used.add(candidate);
  return candidate;
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => resolve({ img, url });
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('unreadable')); };
    img.src = url;  // browsers apply the photo's rotation (EXIF orientation) for us
  });
}

function toBlob(canvas, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}

async function shrink(file, longEdge) {
  const { img, url } = await loadImage(file);
  try {
    const w0 = img.naturalWidth, h0 = img.naturalHeight;
    const scale = Math.min(1, longEdge / Math.max(w0, h0));
    const w = Math.round(w0 * scale), h = Math.round(h0 * scale);

    // Big reductions look smoother done in two steps, and the half-way canvas
    // stays under the canvas size limit iPhones impose.
    let source = img;
    if (scale < 0.5) {
      const mid = document.createElement('canvas');
      mid.width = w * 2; mid.height = h * 2;
      const c = mid.getContext('2d');
      c.imageSmoothingQuality = 'high';
      c.drawImage(img, 0, 0, mid.width, mid.height);
      source = mid;
    }
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(source, 0, 0, w, h);

    let blob = await toBlob(canvas, QUALITY);
    if (blob.size > MAX_BYTES) blob = await toBlob(canvas, 0.72);
    if (source !== img) source.width = source.height = 0;  // free the memory now
    canvas.width = canvas.height = 0;
    return { blob, w, h };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function row(name) {
  const li = document.createElement('li');
  li.innerHTML = `<div class="thumb"></div><div class="info"><b></b><span class="mono muted">Shrinking…</span></div>`;
  li.querySelector('b').textContent = name;
  list.append(li);
  return li;
}

input.addEventListener('change', async () => {
  const files = [...input.files];
  if (!files.length) return;
  done.forEach((d) => URL.revokeObjectURL(d.url));
  done = [];
  list.innerHTML = '';
  actions.hidden = true;

  const used = new Set();
  let before = 0, after = 0;
  const longEdge = Number(sizeSelect.value);

  // One at a time, so a phone never holds more than one full-size photo in memory.
  for (const file of files) {
    const li = row(file.name);
    const status = li.querySelector('.mono');
    try {
      const { blob, w, h } = await shrink(file, longEdge);
      const out = new File([blob], outName(file.name, used), { type: 'image/jpeg' });
      const url = URL.createObjectURL(out);
      done.push({ file: out, url });
      before += file.size; after += out.size;

      li.querySelector('b').textContent = out.name;
      li.querySelector('.thumb').innerHTML = `<img src="${url}" alt="">`;
      status.textContent = `${kb(file.size)} → ${kb(out.size)} · ${w}×${h}`;
      const save = document.createElement('a');
      save.className = 'save';
      save.href = url;
      save.download = out.name;
      save.textContent = 'Save';
      li.append(save);
    } catch {
      li.classList.add('failed');
      status.textContent = "Couldn't read this photo. On Android, HEIC photos may need converting first.";
    }
  }

  if (done.length) {
    actions.hidden = false;
    summary.textContent = `${done.length} photo${done.length === 1 ? '' : 's'} · ${kb(before)} → ${kb(after)}`;
    saveAll.textContent = done.length === 1 ? 'Save photo' : `Save all ${done.length}`;
  }
  input.value = '';  // lets the same photos be picked again
});

// The share sheet is how a phone saves images to the camera roll ("Save Images"
// on iPhone, "Save to device"/Gallery on Android). Elsewhere, download each file.
saveAll.addEventListener('click', async () => {
  const files = done.map((d) => d.file);
  if (navigator.canShare && navigator.canShare({ files })) {
    try {
      await navigator.share({ files });
      return;
    } catch (e) {
      if (e.name === 'AbortError') return;  // they closed the share sheet
    }
  }
  for (const d of done) {
    const a = document.createElement('a');
    a.href = d.url;
    a.download = d.file.name;
    document.body.append(a);
    a.click();
    a.remove();
    await new Promise((r) => setTimeout(r, 300));  // browsers block rapid-fire downloads
  }
});
