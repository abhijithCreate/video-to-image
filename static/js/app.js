/* Video to Image — minimal vanilla front-end. No build step, no framework. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    jobId: null, images: [], objectUrl: null, busy: false,
    step: 'upload', hasResults: false
  };

  var LOSSY = { jpg: true, jpeg: true, webp: true };
  var FORMAT_NOTES = {
    jpg: 'Small files, no transparency. Best for photos.',
    png: 'Lossless with transparency — the quality dial does not apply, PNG uses compression instead.',
    webp: 'Modern format: smaller than JPG at the same quality, with transparency.',
    bmp: 'Uncompressed. Very large files; use only if a tool requires BMP.',
    tiff: 'Lossless, LZW-compressed. Common in print and archival workflows.',
    gif: 'Limited to 256 colours per image. Best for simple graphics.'
  };

  /* ---------- helpers ---------- */

  function show(el) { el.classList.remove('hidden'); el.removeAttribute('aria-hidden'); }
  function hide(el) { el.classList.add('hidden'); el.setAttribute('aria-hidden', 'true'); }

  function announce(message) { $('live-status').textContent = message; }

  function showError(message) {
    var box = $('alert');
    box.textContent = message;
    show(box);
    announce(message);
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function clearError() { hide($('alert')); $('alert').textContent = ''; }

  /* ---------- steps ---------- */

  var STEPS = ['upload', 'configure', 'results'];
  var STEP_HEADINGS = {
    upload: 'upload-heading',
    configure: 'configure-heading',
    results: 'results-heading'
  };

  function reachable(step) {
    if (step === 'upload') return true;
    if (step === 'configure') return !!state.jobId;
    return state.hasResults;
  }

  function goToStep(name, options) {
    if (!reachable(name)) return;
    state.step = name;
    clearError();

    STEPS.forEach(function (step) {
      var section = document.querySelector('[data-step="' + step + '"]');
      if (step === name) { show(section); } else { hide(section); }
    });

    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (!options || options.focus !== false) {
      // Move focus to the step's heading so the change is announced.
      var heading = $(STEP_HEADINGS[name]);
      if (heading) heading.focus({ preventScroll: true });
    }
  }

  document.addEventListener('click', function (event) {
    var trigger = event.target.closest('[data-goto]');
    if (!trigger || trigger.disabled) return;
    event.preventDefault();
    goToStep(trigger.dataset.goto);
  });

  function formatBytes(bytes) {
    if (!bytes) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    var value = bytes / Math.pow(1024, i);
    return (value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)) + ' ' + units[i];
  }

  function formatDuration(seconds) {
    var total = Math.max(0, Math.round(seconds || 0));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  function formatTimestamp(seconds) {
    var value = Math.max(0, seconds || 0);
    var m = Math.floor(value / 60);
    var s = value - m * 60;
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(2);
  }

  async function readError(response) {
    try {
      var data = await response.json();
      if (data && data.error) return data.error;
    } catch (e) { /* fall through */ }
    return 'The server could not complete that request.';
  }

  /* ---------- theme ---------- */

  $('theme-toggle').addEventListener('click', function () {
    var dark = document.documentElement.classList.toggle('dark');
    try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (e) {}
  });

  /* ---------- upload ---------- */

  var dropzone = $('dropzone');
  var fileInput = $('file-input');

  ['dragenter', 'dragover'].forEach(function (type) {
    dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      dropzone.classList.add('border-brand-500', 'bg-brand-50/40');
    });
  });
  ['dragleave', 'drop'].forEach(function (type) {
    dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      dropzone.classList.remove('border-brand-500', 'bg-brand-50/40');
    });
  });
  dropzone.addEventListener('drop', function (event) {
    var files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length) startUpload(files[0]);
  });
  fileInput.addEventListener('change', function () {
    if (fileInput.files && fileInput.files.length) startUpload(fileInput.files[0]);
  });

  function startUpload(file) {
    clearError();
    if (state.busy) return;
    state.busy = true;

    var progress = $('upload-progress');
    var bar = $('upload-bar');
    var percent = $('upload-percent');
    show(progress);
    bar.style.width = '0%';
    percent.textContent = '0%';
    announce('Uploading video.');

    var body = new FormData();
    body.append('file', file, file.name);

    var request = new XMLHttpRequest();
    request.open('POST', '/api/upload');
    request.responseType = 'json';

    request.upload.addEventListener('progress', function (event) {
      if (!event.lengthComputable) return;
      var value = Math.round((event.loaded / event.total) * 100);
      bar.style.width = value + '%';
      percent.textContent = value + '%';
    });

    request.addEventListener('load', function () {
      state.busy = false;
      hide(progress);
      var data = request.response;
      if (request.status !== 200) {
        showError((data && data.error) || 'The video could not be uploaded.');
        fileInput.value = '';
        return;
      }
      onUploaded(file, data);
    });

    request.addEventListener('error', function () {
      state.busy = false;
      hide(progress);
      showError('The upload failed. Check your connection and try again.');
    });

    request.send(body);
  }

  /* ---------- fetch from a URL ---------- */

  var urlForm = $('url-form');
  if (urlForm) {
    urlForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (state.busy) return;

      var value = $('url-input').value.trim();
      if (!value) return showError('Enter a video link.');

      clearError();
      setUrlBusy(true);
      announce('Fetching the video.');

      /* A video-site link is resolved by an extractor before any video moves,
         so this can run for a minute. Give up a little after the server would,
         and say something in the meantime - a silent spinner reads as broken. */
      var budgetMs = (parseInt(urlForm.dataset.timeoutSeconds, 10) || 135) * 1000;
      var controller = new AbortController();
      var timers = [
        setTimeout(function () { controller.abort(); }, budgetMs),
        setTimeout(function () { setUrlStatus('Still working — this can take a few moments.'); }, 5000),
        setTimeout(function () { setUrlStatus('Still working — video sites are slower than direct links.'); }, 20000)
      ];

      try {
        var response = await fetch('/api/upload-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: value }),
          signal: controller.signal
        });
        if (!response.ok) throw new Error(await readError(response));
        // No local file: the preview plays the copy on the server.
        onUploaded(null, await response.json());
      } catch (error) {
        showError(
          error.name === 'AbortError'
            ? 'That link took too long and was stopped. Try again, or download the video and upload the file.'
            : error.message || 'That video could not be fetched.'
        );
      } finally {
        timers.forEach(clearTimeout);
        setUrlBusy(false);
      }
    });
  }

  function setUrlStatus(message) {
    var status = $('url-status');
    if (!status) return;
    status.textContent = message || '';
    status.classList.toggle('hidden', !message);
    if (message) announce(message);
  }

  function setUrlBusy(busy) {
    state.busy = busy;
    $('url-submit').disabled = busy;
    $('url-spinner').classList.toggle('hidden', !busy);
    $('url-label').textContent = busy ? 'Fetching…' : 'Fetch video';
    if (!busy) setUrlStatus('');
  }

  function onUploaded(file, data) {
    state.jobId = data.job_id;

    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
    if (file) {
      state.objectUrl = URL.createObjectURL(file);
      $('preview').src = state.objectUrl;
    } else {
      $('preview').src = '/api/preview/' + data.job_id;
    }

    var info = data.video;
    $('video-info').innerHTML = [
      ['Filename', info.filename],
      ['Size', formatBytes(info.size_bytes)],
      ['Duration', formatDuration(info.duration)],
      ['Resolution', info.width + ' x ' + info.height],
      ['Frame rate', (info.fps || 0).toFixed(2) + ' fps'],
      ['Format', info.format_name + ' / ' + String(info.codec).toUpperCase()]
    ].map(function (row) {
      return '<dt class="text-slate-500 dark:text-slate-400">' + row[0] + '</dt>' +
        '<dd class="truncate font-medium" title="' + escapeAttr(String(row[1])) + '">' +
        escapeHtml(String(row[1])) + '</dd>';
    }).join('');

    state.hasResults = false;
    state.images = [];
    goToStep('configure');
    announce('Video ready. Step 2 of 3: choose your conversion options.');
  }

  function escapeHtml(value) {
    return value.replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function escapeAttr(value) { return escapeHtml(value); }

  /* ---------- options ---------- */

  var methodInputs = Array.prototype.slice.call(
    document.querySelectorAll('input[name="method"]')
  );

  function syncMethod() {
    var selected = methodInputs.filter(function (input) { return input.checked; })[0];
    var value = selected ? selected.value : 'fps';
    document.querySelectorAll('[data-method-panel]').forEach(function (panel) {
      panel.classList.toggle('hidden', panel.dataset.methodPanel !== value);
    });
  }
  methodInputs.forEach(function (input) { input.addEventListener('change', syncMethod); });

  function bindCustomSelect(selectId, key) {
    var select = $(selectId);
    select.addEventListener('change', function () {
      var wrap = document.querySelector('[data-custom="' + key + '"]');
      wrap.classList.toggle('hidden', select.value !== 'custom');
    });
  }
  bindCustomSelect('fps-select', 'fps');
  bindCustomSelect('interval-select', 'interval');

  $('resolution-select').addEventListener('change', function () {
    var custom = this.value === 'custom';
    document.querySelector('[data-custom="resolution"]').classList.toggle('hidden', !custom);
  });

  var qualitySelect = $('quality-select');
  var qualityRange = $('quality-range');
  var qualityValue = $('quality-value');
  var formatSelect = $('format-select');

  function syncQuality() {
    var lossy = !!LOSSY[formatSelect.value];
    var custom = qualitySelect.value === 'custom';
    var presets = { low: 50, medium: 70, high: 85, very_high: 95 };

    if (!custom) {
      qualityRange.value = presets[qualitySelect.value] || 85;
    }
    qualityValue.textContent = qualityRange.value;
    qualityRange.disabled = !custom || !lossy;
    qualitySelect.disabled = !lossy;
    $('quality-slider-wrap').classList.toggle('opacity-50', !lossy);
    $('format-note').textContent = FORMAT_NOTES[formatSelect.value] || '';
  }
  qualitySelect.addEventListener('change', syncQuality);
  formatSelect.addEventListener('change', syncQuality);
  qualityRange.addEventListener('input', function () {
    qualityValue.textContent = qualityRange.value;
  });

  /* ---------- image title ---------- */

  var titleInput = $('title-input');

  /* Mirrors image_service.sanitise_title so the hint previews the real name.
     The server remains authoritative - this is presentation only. */
  function safeTitle(value) {
    return String(value || '').trim()
      .replace(/[^A-Za-z0-9_-]+/g, '-')
      .replace(/([_-])[_-]+/g, '$1')
      .replace(/^[_-]+|[_-]+$/g, '')
      .slice(0, 60)
      .replace(/^[_-]+|[_-]+$/g, '');
  }

  function syncTitleExample() {
    /* Every selectable format's extension is just its own name. */
    $('title-example').textContent =
      (safeTitle(titleInput.value) || 'frame') + '_00001.' + formatSelect.value;
  }
  titleInput.addEventListener('input', syncTitleExample);
  formatSelect.addEventListener('change', syncTitleExample);

  /* ---------- remembered settings ---------- */

  var SETTINGS_KEY = 'v2i:settings';

  function readSettings() {
    var raw = null;
    try { raw = localStorage.getItem(SETTINGS_KEY); } catch (e) { return null; }
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (e) { return null; }
  }

  function currentSettings() {
    var checked = methodInputs.filter(function (i) { return i.checked; })[0];
    return {
      method: checked ? checked.value : 'fps',
      fps: $('fps-select').value,
      fpsCustom: $('fps-custom').value,
      count: $('count-input').value,
      interval: $('interval-select').value,
      intervalCustom: $('interval-custom').value,
      format: formatSelect.value,
      qualityPreset: qualitySelect.value,
      quality: qualityRange.value,
      resolution: $('resolution-select').value,
      width: $('width-input').value,
      height: $('height-input').value,
      maintainAspect: $('aspect-input').checked,
      zipOnly: $('zip-only-input').checked,
      title: titleInput.value
    };
  }

  function applySettings(saved) {
    methodInputs.forEach(function (input) {
      input.checked = input.value === saved.method;
    });
    setValue('fps-select', saved.fps);
    setValue('fps-custom', saved.fpsCustom);
    setValue('count-input', saved.count);
    setValue('interval-select', saved.interval);
    setValue('interval-custom', saved.intervalCustom);
    setValue('format-select', saved.format);
    setValue('quality-select', saved.qualityPreset);
    setValue('quality-range', saved.quality);
    setValue('resolution-select', saved.resolution);
    setValue('width-input', saved.width);
    setValue('height-input', saved.height);
    setValue('title-input', saved.title);
    if (typeof saved.maintainAspect === 'boolean') {
      $('aspect-input').checked = saved.maintainAspect;
    }
    if (typeof saved.zipOnly === 'boolean') {
      $('zip-only-input').checked = saved.zipOnly;
    }
  }

  function setValue(id, value) {
    if (value === undefined || value === null || value === '') return;
    var el = $(id);
    if (el.tagName === 'SELECT') {
      // Ignore stored values that no longer exist as options.
      var exists = Array.prototype.some.call(el.options, function (option) {
        return option.value === value;
      });
      if (!exists) return;
    }
    el.value = value;
  }

  function persistSettings() {
    try {
      if ($('remember-input').checked) {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(currentSettings()));
      } else {
        localStorage.removeItem(SETTINGS_KEY);
      }
    } catch (e) { /* private mode or full storage - not worth surfacing */ }
  }

  var restored = readSettings();
  if (restored) {
    $('remember-input').checked = true;
    applySettings(restored);
  }

  syncMethod();
  syncQuality();
  syncTitleExample();
  syncCustomPanels();

  $('options-form').addEventListener('change', persistSettings);
  $('quality-range').addEventListener('change', persistSettings);

  /* Dependent panels also need to follow restored values. */
  function syncCustomPanels() {
    [['fps-select', 'fps'], ['interval-select', 'interval']].forEach(function (pair) {
      document.querySelector('[data-custom="' + pair[1] + '"]')
        .classList.toggle('hidden', $(pair[0]).value !== 'custom');
    });
    document.querySelector('[data-custom="resolution"]')
      .classList.toggle('hidden', $('resolution-select').value !== 'custom');
  }

  /* ---------- process ---------- */

  function collectPayload() {
    var method = methodInputs.filter(function (i) { return i.checked; })[0].value;
    var payload = {
      job_id: state.jobId,
      method: method,
      image_format: formatSelect.value,
      quality_preset: qualitySelect.value,
      quality: parseInt(qualityRange.value, 10),
      maintain_aspect: $('aspect-input').checked,
      zip_only: $('zip-only-input').checked,
      title: titleInput.value
    };

    if (method === 'fps') {
      var fpsChoice = $('fps-select').value;
      payload.fps = parseFloat(fpsChoice === 'custom' ? $('fps-custom').value : fpsChoice);
    } else if (method === 'count') {
      payload.count = parseInt($('count-input').value, 10);
    } else if (method === 'interval') {
      var choice = $('interval-select').value;
      payload.interval = parseFloat(choice === 'custom' ? $('interval-custom').value : choice);
    }

    var resolution = $('resolution-select').value;
    if (resolution === 'custom') {
      var width = parseInt($('width-input').value, 10);
      var height = parseInt($('height-input').value, 10);
      if (width > 0) payload.width = width;
      if (height > 0) payload.height = height;
    } else if (resolution !== 'original') {
      var parts = resolution.split('x');
      payload.width = parseInt(parts[0], 10);
      payload.height = parseInt(parts[1], 10);
    }
    return payload;
  }

  $('options-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    if (state.busy || !state.jobId) return;
    clearError();

    var payload = collectPayload();
    if (payload.method === 'fps' && !(payload.fps > 0)) {
      return showError('Enter a frame rate greater than 0.');
    }
    if (payload.method === 'count' && !(payload.count > 0)) {
      return showError('Enter how many images you want.');
    }
    if (payload.method === 'interval' && !(payload.interval > 0)) {
      return showError('Enter an interval greater than 0 seconds.');
    }

    setBusy(true);
    hide($('step-configure'));
    show($('step-loading'));
    announce('Extracting frames.');

    try {
      var response = await fetch('/api/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(await readError(response));
      renderResults(await response.json());
    } catch (error) {
      showError(error.message || 'The images could not be generated.');
    } finally {
      hide($('step-loading'));
      // Failures return to the options step so settings can be adjusted.
      if (state.step === 'configure') show($('step-configure'));
      setBusy(false);
    }
  });

  function setBusy(busy) {
    state.busy = busy;
    var button = $('generate');
    button.disabled = busy;
    $('generate-spinner').classList.toggle('hidden', !busy);
    $('generate-label').textContent = busy ? 'Generating…' : 'Generate images';
  }

  /* ---------- results ---------- */

  function renderResults(result) {
    state.images = result.images || [];

    var descriptor = result.options.width + ' x ' + result.options.height +
      ' · ' + String(result.options.format).toUpperCase() +
      ' · ' + formatBytes(result.total_size_bytes);
    $('results-summary').textContent =
      result.count + (result.count === 1 ? ' image' : ' images') +
      ' generated · ' + descriptor;
    $('download-all').href = result.download_all_url;
    $('download-zip').href = result.download_all_url;

    var notice = $('results-notice');
    if (result.truncated) {
      notice.textContent = 'Your request exceeded the limit of ' + result.limit +
        ' images, so extraction stopped there.';
      show(notice);
    } else {
      hide(notice);
    }

    if (result.zip_only) {
      // Nothing to preview: show the archive, and skip building a grid at all.
      $('results-grid').innerHTML = '';
      hide($('download-all'));
      show($('results-zip'));
      $('results-zip-detail').textContent =
        result.count + (result.count === 1 ? ' image' : ' images') + ' · ' + descriptor +
        (result.archive_size_bytes ? ' · ZIP ' + formatBytes(result.archive_size_bytes) : '');
      state.hasResults = true;
      goToStep('results');
      announce(result.count + ' images generated. Your ZIP file is ready to download.');
      return;
    }

    hide($('results-zip'));
    show($('download-all'));

    $('results-grid').innerHTML = state.images.map(function (image) {
      var label = 'Frame ' + image.frame + ' at ' + formatTimestamp(image.timestamp);
      return '' +
        '<li>' +
        '<a href="' + escapeAttr(image.url) + '" data-lightbox="frames"' +
        ' data-caption="' + escapeAttr(captionFor(image)) + '"' +
        ' data-alt="' + escapeAttr(label) + '"' +
        ' data-width="' + image.width + '" data-height="' + image.height + '"' +
        ' aria-label="' + escapeAttr(label) + '"' +
        ' class="group block w-full overflow-hidden rounded-xl border border-slate-200 bg-white text-left transition hover:border-brand-500 hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 dark:border-slate-800 dark:bg-slate-900">' +
        '<img src="' + escapeAttr(image.thumbnail_url) + '" alt="' + escapeAttr(label) + '"' +
        ' loading="lazy" decoding="async"' +
        ' class="aspect-video w-full bg-slate-100 object-cover dark:bg-slate-800">' +
        '<span class="flex items-baseline justify-between gap-2 px-3 py-2 text-xs">' +
        '<span class="font-medium">Frame ' + image.frame + '</span>' +
        '<span class="text-slate-500 dark:text-slate-400">' + formatTimestamp(image.timestamp) + '</span>' +
        '</span></a></li>';
    }).join('');

    state.hasResults = true;
    goToStep('results');
    announce(state.images.length + ' images generated.');
  }

  $('start-over').addEventListener('click', async function () {
    if (state.jobId) {
      try {
        await fetch('/api/job/' + state.jobId, { method: 'DELETE' });
      } catch (e) { /* the retention sweep will clean up regardless */ }
    }
    state.jobId = null;
    state.images = [];
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
    $('preview').removeAttribute('src');
    fileInput.value = '';
    state.hasResults = false;
    if ($('url-input')) $('url-input').value = '';
    goToStep('upload');
    announce('Ready for a new conversion.');
  });

  $('reset-upload').addEventListener('click', function () { fileInput.click(); });

  /* ---------- lightbox ---------- */

  /* Image viewing is handled by Lightbox3 (static/vendor/lightbox3), which owns
     the overlay, prev/next, Escape, focus and touch gestures. It binds one
     delegated click listener on document and resolves the gallery from
     [data-lightbox] at open time, so links rendered later still work and this
     only has to be initialised once.

     It has no download button, so the per-image metadata and the download link
     ride along in the caption, which the library injects as HTML. */

  function captionFor(image) {
    return 'Frame ' + image.frame +
      ' &middot; ' + escapeHtml(formatTimestamp(image.timestamp)) +
      ' &middot; ' + image.width + ' &times; ' + image.height +
      ' &middot; ' + escapeHtml(String(image.format).toUpperCase()) +
      ' &middot; ' + escapeHtml(formatBytes(image.size_bytes)) +
      ' &middot; <a href="' + escapeAttr(image.url) + '" download="' +
      escapeAttr(image.filename) + '">Download image</a>';
  }

  /* The UMD bundle exports the class as window.Lightbox3.Lightbox. Its own
     auto-init is no use here: it only runs if a [data-lightbox] element exists
     at DOMContentLoaded, and the grid is empty until a conversion finishes. */
  var Lightbox3 = (window.Lightbox3 && window.Lightbox3.Lightbox) || window.Lightbox;
  if (Lightbox3 && Lightbox3.init) {
    Lightbox3.init({ loop: true });
  }

})();
