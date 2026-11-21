/* Storage location is independent of the database policy cards. */
(function () {
    'use strict';
    const form = document.getElementById('storageLocationForm');
    if (!form) return;
    const current = document.getElementById('storageLocationCurrent');
    const pending = document.getElementById('storageLocationPending');
    const status = document.getElementById('storageLocationError');
    const destination = document.getElementById('storageLocationDestination');
    const schedule = form.querySelector('[type="submit"]');
    const cancel = document.getElementById('storageLocationCancel');
    const retry = document.getElementById('storageLocationRetry');
    let location = null;
    let busy = false;

    function controls() {
        const enabled = !!location && location.available && !busy;
        destination.disabled = !enabled;
        schedule.disabled = !enabled;
        cancel.hidden = !location || !location.pending;
        cancel.disabled = !enabled;
        retry.disabled = busy;
    }
    function render(value) {
        location = value;
        current.textContent = value.current;
        pending.hidden = !value.pending;
        pending.textContent = value.pending ? 'Scheduled destination: ' + value.pending : '';
        destination.value = value.pending || '';
        status.textContent = [value.error, !value.available ? (value.reason || 'Moving storage is unavailable.') : ''].filter(Boolean).join(' ');
        retry.hidden = true;
        controls();
    }
    async function request(body) {
        const response = await fetch('/api/settings/storage/location', {
            credentials: 'include', cache: 'no-store',
            ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
        });
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('Could not read the storage location response.'); }
        if (!response.ok || !data.success) throw new Error(data.error || 'Could not update storage location.');
        if (!data.location || data.location.version !== 1) throw new Error('Unsupported storage location response.');
        return data.location;
    }
    async function update(body) {
        busy = true;
        controls();
        status.textContent = '';
        try {
            render(await request(body));
        } catch (error) {
            status.textContent = error.message || 'Could not load storage location.';
            if (!location) {
                current.textContent = 'Unavailable';
                retry.hidden = false;
            }
        } finally {
            busy = false;
            controls();
        }
    }
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        update({destination: destination.value.trim()});
    });
    cancel.addEventListener('click', () => update({cancel: true}));
    retry.addEventListener('click', () => update());
    update();
})();
