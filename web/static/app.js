/**
 * Any to Markdown - Frontend Application
 */

// State
let currentTaskId = null;
let currentOutputFormat = null;
let pollInterval = null;

// DOM Elements
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const urlInput = document.getElementById('urlInput');
const fetchBtn = document.getElementById('fetchBtn');
const uploadSection = document.getElementById('uploadSection');
const formatSection = document.getElementById('formatSection');
const progressSection = document.getElementById('progressSection');
const resultSection = document.getElementById('resultSection');
const fileName = document.getElementById('fileName');
const fileFormat = document.getElementById('fileFormat');
const formatOptions = document.getElementById('formatOptions');
const convertBtn = document.getElementById('convertBtn');
const clearBtn = document.getElementById('clearBtn');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const resultSuccess = document.getElementById('resultSuccess');
const resultError = document.getElementById('resultError');
const errorMessage = document.getElementById('errorMessage');
const downloadBtn = document.getElementById('downloadBtn');
const newConversionBtn = document.getElementById('newConversionBtn');
const retryBtn = document.getElementById('retryBtn');

// Format labels
const FORMAT_LABELS = {
    'md_single': 'Markdown (Single File)',
    'md_chapters': 'Markdown (Chapters)',
    'html': 'HTML',
    'pdf': 'PDF'
};

// Event Listeners
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', handleDragOver);
dropZone.addEventListener('dragleave', handleDragLeave);
dropZone.addEventListener('drop', handleDrop);
fileInput.addEventListener('change', handleFileSelect);
fetchBtn.addEventListener('click', handleFetchUrl);
urlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleFetchUrl();
});
convertBtn.addEventListener('click', startConversion);
clearBtn.addEventListener('click', resetToUpload);
downloadBtn.addEventListener('click', downloadResult);
newConversionBtn.addEventListener('click', resetToUpload);
retryBtn.addEventListener('click', resetToUpload);

// Drag and Drop handlers
function handleDragOver(e) {
    e.preventDefault();
    dropZone.classList.add('dragover');
}

function handleDragLeave(e) {
    e.preventDefault();
    dropZone.classList.remove('dragover');
}

function handleDrop(e) {
    e.preventDefault();
    dropZone.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        uploadFile(files[0]);
    }
}

function handleFileSelect(e) {
    const files = e.target.files;
    if (files.length > 0) {
        uploadFile(files[0]);
    }
}

// Upload file
async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    try {
        showProgress('Uploading file...', 0);

        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Upload failed');
        }

        const data = await response.json();
        handleUploadSuccess(data);
    } catch (error) {
        showError(error.message);
    }
}

// Fetch URL
async function handleFetchUrl() {
    const url = urlInput.value.trim();
    if (!url) return;

    try {
        showProgress('Fetching URL...', 0);

        const response = await fetch('/api/fetch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ url })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Fetch failed');
        }

        const data = await response.json();
        handleUploadSuccess(data);
    } catch (error) {
        showError(error.message);
    }
}

// Handle successful upload
function handleUploadSuccess(data) {
    currentTaskId = data.task_id;

    // Update file info
    fileName.textContent = data.filename || data.url;
    fileFormat.textContent = data.input_format.toUpperCase();

    // Populate format options
    populateFormatOptions(data.available_outputs);

    // Show format section
    hideAllSections();
    formatSection.classList.remove('hidden');
}

// Populate format options
function populateFormatOptions(formats) {
    formatOptions.innerHTML = '';
    currentOutputFormat = formats[0];

    formats.forEach((format, index) => {
        const option = document.createElement('label');
        option.className = 'radio-option' + (index === 0 ? ' selected' : '');
        option.innerHTML = `
            <input type="radio" name="format" value="${format}" ${index === 0 ? 'checked' : ''}>
            <span class="radio-circle"></span>
            <span class="radio-label">${FORMAT_LABELS[format] || format}</span>
        `;

        option.addEventListener('click', () => {
            document.querySelectorAll('.radio-option').forEach(opt => opt.classList.remove('selected'));
            option.classList.add('selected');
            currentOutputFormat = format;
        });

        formatOptions.appendChild(option);
    });
}

// Start conversion
async function startConversion() {
    if (!currentTaskId || !currentOutputFormat) return;

    try {
        convertBtn.disabled = true;
        showProgress('Starting conversion...', 0);

        const response = await fetch('/api/convert', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                task_id: currentTaskId,
                output_format: currentOutputFormat
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Conversion failed');
        }

        // Start polling for status
        startPolling();
    } catch (error) {
        convertBtn.disabled = false;
        showError(error.message);
    }
}

// Poll for status
function startPolling() {
    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/status/${currentTaskId}`);
            const data = await response.json();

            updateProgress(data.progress, data.message);

            if (data.status === 'completed') {
                stopPolling();
                showSuccess();
            } else if (data.status === 'failed') {
                stopPolling();
                showError(data.message);
            }
        } catch (error) {
            stopPolling();
            showError('Connection lost');
        }
    }, 500);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

// UI Updates
function hideAllSections() {
    uploadSection.classList.add('hidden');
    formatSection.classList.add('hidden');
    progressSection.classList.add('hidden');
    resultSection.classList.add('hidden');
}

function showProgress(message, progress) {
    hideAllSections();
    progressSection.classList.remove('hidden');
    updateProgress(progress, message);
}

function updateProgress(progress, message) {
    progressFill.style.width = `${progress}%`;
    progressText.textContent = message;
}

function showSuccess() {
    hideAllSections();
    resultSection.classList.remove('hidden');
    resultSuccess.classList.remove('hidden');
    resultError.classList.add('hidden');
}

function showError(message) {
    hideAllSections();
    resultSection.classList.remove('hidden');
    resultSuccess.classList.add('hidden');
    resultError.classList.remove('hidden');
    errorMessage.textContent = message;
    convertBtn.disabled = false;
}

function resetToUpload() {
    stopPolling();
    currentTaskId = null;
    currentOutputFormat = null;
    fileInput.value = '';
    urlInput.value = '';
    convertBtn.disabled = false;

    hideAllSections();
    uploadSection.classList.remove('hidden');
}

// Download result
function downloadResult() {
    if (!currentTaskId) return;
    window.location.href = `/api/download/${currentTaskId}`;
}
