/* Video to Image — minimal vanilla front-end. No build step, no framework. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    jobId: null, images: [], objectUrl: null, blobUrl: null, file: null, busy: false,
    step: 'upload', hasResults: false,
    // In stateless mode the conversion request has to name its own source, and
    // a link never becomes a local File - so the link is kept alongside it.
    sourceUrl: null,
    // Previews that failed every recovery attempt, and the notice text the
    // result itself asked for, so the two can share one banner.
    missingPreviews: 0, truncatedNotice: ''
  };

  /* Where consecutive requests are not guaranteed to reach the same instance,
     a job cannot be kept on disk between them: the previews and downloads that
     follow an upload would 404 on a machine that never saw it. In that mode the
     whole conversion happens in one request and the ZIP comes straight back. */
  var STATELESS = document.getElementById('main').dataset.stateless === 'true';

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
    // A job id in the normal flow; in stateless mode there is no job, and the
    // file we are about to convert is held client-side instead.
    if (step === 'configure') return !!(state.jobId || state.file);
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
    if (STATELESS) return inspectLocally(file);
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

  /* Describe the file without a server round trip, since a stateless host
     would not keep an upload between requests anyway.

     Best-effort on purpose. A <video> can only report duration and dimensions
     for formats the browser itself decodes, and MKV, AVI and MPEG are accepted
     here but decodable by no browser. The server computes the extraction plan
     from its own probe regardless, so unknown values here are cosmetic and must
     never block the flow. */
  var LOCAL_PROBE_TIMEOUT_MS = 2500;

  function inspectLocally(file) {
    state.file = file;
    state.sourceUrl = null;

    var url = URL.createObjectURL(file);
    var probe = document.createElement('video');
    probe.preload = 'metadata';
    var settled = false;

    function proceed(video) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      URL.revokeObjectURL(url);
      onUploaded(file, { job_id: null, video: video });
    }

    function describe(duration, width, height) {
      return {
        filename: file.name,
        size_bytes: file.size,
        duration: duration || 0,
        width: width || 0,
        height: height || 0,
        fps: 0,
        format_name: (file.name.split('.').pop() || '').toUpperCase(),
        codec: ''
      };
    }

    var timer = setTimeout(function () {
      proceed(describe(0, 0, 0));
    }, LOCAL_PROBE_TIMEOUT_MS);

    probe.addEventListener('loadedmetadata', function () {
      proceed(describe(probe.duration, probe.videoWidth, probe.videoHeight));
    });
    probe.addEventListener('error', function () {
      proceed(describe(0, 0, 0));
    });

    probe.src = url;
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
        // No local file: the preview plays the copy on the server, and in
        // stateless mode the link is what the conversion request will send.
        state.file = null;
        state.sourceUrl = value;
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
      ['Duration', info.duration ? formatDuration(info.duration) : 'Unknown'],
      ['Resolution', info.width && info.height
        ? info.width + ' x ' + info.height
        : 'Unknown'],
      ['Frame rate', info.fps ? info.fps.toFixed(2) + ' fps' : 'Unknown'],
      ['Format', info.codec
        ? info.format_name + ' / ' + String(info.codec).toUpperCase()
        : info.format_name]
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
    if (state.busy) return;
    if (STATELESS ? !(state.file || state.sourceUrl) : !state.jobId) return;
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
      if (STATELESS) {
        await convertInOneRequest(payload);
      } else {
        var response = await fetch('/api/process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error(await readError(response));
        renderResults(await response.json());
      }
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
    $('generate-label').textContent = busy
      ? 'Generating…'
      : (STATELESS ? 'Generate & download ZIP' : 'Generate images');
  }

  /* Upload and convert in one request, because nothing can be kept on the
     server between requests. The archive comes back as the response body. */
  async function convertInOneRequest(payload) {
    var body = new FormData();
    if (state.file) {
      body.append('file', state.file, state.file.name);
    } else {
      // Nothing was kept from the fetch that filled in the options step, so the
      // server resolves the link again as part of this one request.
      body.append('url', state.sourceUrl);
    }
    Object.keys(payload).forEach(function (key) {
      if (key === 'job_id' || key === 'zip_only') return;
      var value = payload[key];
      if (value === undefined || value === null || value === '') return;
      body.append(key, String(value));
    });

    var response = await fetch('/api/convert', { method: 'POST', body: body });
    if (!response.ok) throw new Error(await readError(response));

    var blob = await response.blob();
    var name = filenameFrom(response.headers.get('content-disposition')) || 'images.zip';
    if (state.blobUrl) URL.revokeObjectURL(state.blobUrl);
    state.blobUrl = URL.createObjectURL(blob);

    // Hand it over immediately, and leave the link in place to click again.
    var link = document.createElement('a');
    link.href = state.blobUrl;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();

    renderArchiveOnly(blob, name);
  }

  function filenameFrom(header) {
    if (!header) return null;
    var match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
    return match ? decodeURIComponent(match[1]) : null;
  }

  /* There is nothing to preview, so the results step is just the archive. */
  function renderArchiveOnly(blob, name) {
    state.images = [];
    state.hasResults = true;
    $('results-grid').innerHTML = '';
    hide($('download-all'));
    hide($('results-notice'));
    show($('results-zip'));
    $('results-summary').textContent = 'Your ZIP file is ready';
    $('results-zip-detail').textContent = name + ' · ' + formatBytes(blob.size);
    $('download-zip').href = state.blobUrl;
    $('download-zip').setAttribute('download', name);
    goToStep('results');
    announce('Conversion finished. Your ZIP file has been downloaded.');
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
    state.missingPreviews = 0;
    state.truncatedNotice = result.truncated
      ? 'Your request exceeded the limit of ' + result.limit +
        ' images, so extraction stopped there.'
      : '';
    if (state.truncatedNotice) {
      notice.textContent = state.truncatedNotice;
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
        ' data-thumb="' + escapeAttr(image.thumbnail_url) + '"' +
        ' data-full="' + escapeAttr(image.url) + '"' +
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

  /* A thumbnail that fails to load would otherwise sit there as a broken-image
     icon for the rest of the session: nothing ever retries it. A single dropped
     request is enough - a connection reset, a load the browser cancelled while
     the grid was still painting - and it looks like the conversion skipped a
     frame, which it did not.

     So each tile gets three chances: the thumbnail again (cache-busted, since a
     failed response can itself be cached), then the full-size image, and only
     then a labelled placeholder that keeps the grid's shape. Error events do not
     bubble, so this listens in the capture phase on the grid, which survives the
     innerHTML rebuild that replaces the images themselves. */

  var THUMB_RETRY_DELAY_MS = 400;

  function recoverThumbnail(img) {
    var stage = img.getAttribute('data-retry') || '';

    if (stage === '') {
      img.setAttribute('data-retry', 'retried');
      window.setTimeout(function () {
        img.src = img.getAttribute('data-thumb') + '?retry=' + Date.now();
      }, THUMB_RETRY_DELAY_MS);
      return;
    }

    if (stage === 'retried' && img.getAttribute('data-full')) {
      // The preview is gone but the image behind it may not be. It is heavier
      // than a thumbnail, which is why it is the fallback and not the source.
      img.setAttribute('data-retry', 'full');
      img.src = img.getAttribute('data-full');
      return;
    }

    replaceWithPlaceholder(img);
  }

  function replaceWithPlaceholder(img) {
    var placeholder = document.createElement('span');
    placeholder.className = 'flex aspect-video w-full items-center justify-center ' +
      'bg-slate-100 px-2 text-center text-xs text-slate-500 ' +
      'dark:bg-slate-800 dark:text-slate-400';
    placeholder.textContent = 'Preview unavailable';
    placeholder.setAttribute('role', 'img');
    placeholder.setAttribute('aria-label', img.alt + ' - preview unavailable');
    img.replaceWith(placeholder);
    state.missingPreviews += 1;
    reportMissingPreviews();
  }

  /* One message for the lot, rather than the user counting gaps in the grid.
     A result that has expired or been removed loses every preview at once, and
     that is worth saying plainly - the images themselves are gone too. */
  function reportMissingPreviews() {
    if (!state.missingPreviews) return;
    var total = state.images.length;
    var message = state.missingPreviews >= total
      ? 'The previews could not be loaded. This result may have expired or been ' +
        'removed \u2014 convert the video again to get it back.'
      : state.missingPreviews + ' of ' + total + ' previews could not be loaded. ' +
        'The images themselves may still download.';
    var notice = $('results-notice');
    notice.textContent = state.truncatedNotice
      ? state.truncatedNotice + ' ' + message
      : message;
    show(notice);
  }

  $('results-grid').addEventListener('error', function (event) {
    var img = event.target;
    if (img && img.tagName === 'IMG') recoverThumbnail(img);
  }, true);

  $('start-over').addEventListener('click', async function () {
    if (state.jobId) {
      try {
        await fetch('/api/job/' + state.jobId, { method: 'DELETE' });
      } catch (e) { /* the retention sweep will clean up regardless */ }
    }
    state.jobId = null;
    state.images = [];
    state.file = null;
    state.sourceUrl = null;
    state.missingPreviews = 0;
    state.truncatedNotice = '';
    if (state.blobUrl) {
      URL.revokeObjectURL(state.blobUrl);
      state.blobUrl = null;
    }
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
