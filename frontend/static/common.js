// Helpers shared by every page.

// Every call goes through here. `credentials` makes the browser send the
// session cookie, and a failed request becomes an exception with the
// message the API sent back.
async function api(path, options = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...options });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const error = new Error(data.error || data.detail || `HTTP ${res.status}`);
        error.status = res.status;
        throw error;
    }
    return data;
}

function sendJSON(path, body, method = 'POST') {
    return api(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
}

// Users control their bio, tags and display name, so escape before any of it
// goes into innerHTML.
function esc(text) {
    const escapes = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
    return String(text ?? '').replace(/[&<>"]/g, c => escapes[c]);
}

// Ask the server who we are -- the cookie is the only source of truth -- and
// set up the nav accordingly. Returns the logged-in user's profile, or null.
async function loadNav({ strict = false } = {}) {
    let me = null;
    try {
        me = await api('/api/user/me');
    } catch (error) {
        if (strict && error.status !== 401) throw error;
        // Not logged in; leave `me` null.
    }

    updateNav(me);
    return me;
}

// Also used when an API request reports that the current session has expired.
function updateNav(me) {
    const authBtn = document.getElementById('authBtn');
    const profileLink = document.getElementById('navProfile');

    const recommendationsLink = document.getElementById('navRecommendations');
    recommendationsLink?.classList.toggle('hidden', !me);

    if (me) {
        profileLink.classList.remove('hidden');
        authBtn.textContent = `Log out (${me.username})`;
        authBtn.onclick = async () => {
            await sendJSON('/api/user/logout', {});
            window.location.href = 'index.html';
        };
    } else {
        profileLink.classList.add('hidden');
        authBtn.textContent = 'Log in';
        authBtn.onclick = () => {
            window.location.href = 'login.html';
        };
    }
}
