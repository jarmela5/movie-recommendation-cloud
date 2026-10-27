// Dependency-free state/HTTP tests; real browser layout is checked separately.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const source = ['common.js', 'recommendations.js'].map(name =>
    readFileSync(join(__dirname, '../static', name), 'utf8')).join('\n');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (status, data) => ({ ok: status < 400, status, json: async () => data });
const movie = { id: 1, title: 'Film', genres: ['Comedy'], rating: 4, reason: 'Matches Comedy.' };
const result = movies => response(200, { method: 'genre_rules', recommendations: movies });

const missing = () => response(200, { status: 'missing', current_profile_revision: 1, result: null });
const saved = (status = 'fresh', movies = [movie]) => response(200, {
    status, current_profile_revision: status === 'stale' ? 2 : 1, profile_revision: 1,
    generated_at: '2026-10-17T12:00:00+00:00', prompt: '',
    result: { method: 'local_llm', recommendations: movies },
});

async function page(initial = response(200, { username: 'test', likes_genres: 'Comedy' }), cached = missing()) {
    const nodes = new Map();
    function element(id) {
        if (!nodes.has(id)) {
            const classes = new Set();
            const listeners = {};
            nodes.set(id, {
                textContent: '', innerHTML: '', value: '', disabled: false, attrs: {},
                classList: {
                    add: c => classes.add(c), remove: c => classes.delete(c),
                    contains: c => classes.has(c),
                    toggle: (c, on) => on ? classes.add(c) : classes.delete(c),
                },
                setAttribute(k, v) { this.attrs[k] = v; },
                replaceChildren() { this.innerHTML = ''; },
                addEventListener(k, fn) { listeners[k] = fn; },
                requestSubmit() { return listeners.submit({ preventDefault() {} }); },
                click() { return listeners.click(); },
            });
        }
        return nodes.get(id);
    }
    let handler = async url => url.endsWith('/latest') ? cached : initial;
    const calls = [];
    const context = vm.createContext({
        document: { getElementById: element }, window: { location: {} }, TypeError, AbortSignal,
        fetch: async (url, options) => { calls.push({ url, options }); return handler(url, options); },
    });
    vm.runInContext(source, context);
    await tick();
    return { element, calls, reply(fn) { handler = fn; },
        submit: () => element('recommendationForm').requestSubmit() };
}

test('real request uses session and limit only; cards escape server text', async () => {
    const p = await page();
    p.reply(async () => result([{ ...movie, title: '<script>bad</script>', rating: null }]));
    await p.submit();
    const request = p.calls.at(-1);
    assert.equal(request.url, '/api/recommendations/generate');
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.credentials, 'same-origin');
    assert.deepEqual(JSON.parse(request.options.body), { limit: 5 });
    const html = p.element('recommendationResults').innerHTML;
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
    assert.match(html, /No rating available/);
});

test('loading clears old results and blocks duplicate submissions', async () => {
    const p = await page();
    let release;
    p.reply(() => new Promise(resolve => { release = resolve; }));
    p.element('recommendationResults').innerHTML = 'old';
    const pending = p.submit();
    await p.submit();
    assert.equal(p.calls.length, 3); // session, saved results, generation
    assert.equal(p.element('recommendationResults').innerHTML, '');
    assert.equal(p.element('recommendationSubmit').disabled, true);
    assert.equal(p.element('recommendationForm').attrs['aria-busy'], 'true');
    release(result([movie]));
    await pending;
    assert.equal(p.element('recommendationSubmit').disabled, false);
    assert.equal(p.element('recommendationResults').attrs['aria-busy'], 'false');
});

test('empty selection links to profile', async () => {
    const p = await page(); p.reply(async () => result([])); await p.submit();
    assert.match(p.element('recommendationResults').innerHTML, /No matching films/);
    assert.match(p.element('recommendationResults').innerHTML, /href="profile.html"/);
});

for (const [name, handler, message] of [
    ['network', async () => { throw new TypeError('offline'); }, /Check your connection/],
    ['unavailable', async () => response(503, {}), /temporarily unavailable/],
    ['timeout', async () => response(504, {}), /too long/],
    ['invalid response', async () => result([{ ...movie, genres: null }]), /invalid response/],
]) {
    test(`${name} permits a successful retry`, async () => {
        const p = await page(); p.reply(handler); await p.submit();
        assert.match(p.element('recommendationStatus').textContent, message);
        assert.equal(p.element('recommendationRetry').classList.contains('hidden'), false);
        assert.equal(p.element('recommendationSubmit').disabled, false);
        p.reply(async () => result([movie]));
        await p.element('recommendationRetry').click();
        assert.match(p.element('recommendationResults').innerHTML, /Film/);
        assert.equal(p.element('recommendationRetry').classList.contains('hidden'), true);
    });
}

test('missing session prevents generation', async () => {
    const p = await page(response(401, {})); await p.submit();
    assert.equal(p.calls.length, 1);
    assert.equal(p.element('sessionLogin').classList.contains('hidden'), false);
    assert.equal(p.element('recommendationsContent').classList.contains('hidden'), true);
});

test('expired session clears cards and updates navigation', async () => {
    const p = await page(); p.reply(async () => result([movie])); await p.submit();
    p.reply(async () => response(401, {})); await p.submit();
    assert.equal(p.element('recommendationResults').innerHTML, '');
    assert.equal(p.element('authBtn').textContent, 'Log in');
    assert.equal(p.element('navProfile').classList.contains('hidden'), true);
    assert.equal(p.element('recommendationsContent').classList.contains('hidden'), true);
    assert.equal(p.element('sessionTitle').textContent, 'Please log in again');
    const count = p.calls.length; await p.submit(); assert.equal(p.calls.length, count);
});

test('session check failure offers retry instead of claiming logout', async () => {
    const p = await page(response(503, {}));
    assert.equal(p.element('sessionRetry').classList.contains('hidden'), false);
    p.reply(async () => response(200, { username: 'test' }));
    await p.element('sessionRetry').click();
    assert.equal(p.element('recommendationsContent').classList.contains('hidden'), false);
    assert.equal(p.element('preferencesHint').classList.contains('hidden'), false);
});


test('free text is sent without identity or profile fields', async () => {
    const p = await page(); p.reply(async () => result([movie]));
    p.element('recommendationPrompt').value = '  Comédias dos anos 90  ';
    await p.submit();
    assert.deepEqual(JSON.parse(p.calls.at(-1).options.body), { limit: 5, prompt: 'Comédias dos anos 90' });
    assert.equal(p.element('recommendationPrompt').disabled, false);
});


test('entry loads saved results without generating and escapes saved text', async () => {
    const p = await page(undefined, saved('fresh', [{ ...movie, title: '<img onerror=bad>' }]));
    assert.deepEqual(p.calls.map(c => c.url), ['/api/user/me', '/api/recommendations/latest']);
    assert.equal(p.calls[1].options.credentials, 'same-origin');
    assert.match(p.element('recommendationResults').innerHTML, /&lt;img/);
    assert.match(p.element('recommendationStatus').textContent, /ready for you/);
    assert.match(p.element('savedDetails').textContent, /Based on your profile/);
});

test('missing, empty and stale caches have distinct messages', async () => {
    const a = await page();
    assert.match(a.element('recommendationStatus').textContent, /No saved recommendations yet/);
    const b = await page(undefined, saved('fresh', []));
    assert.match(b.element('recommendationStatus').textContent, /no matching films/);
    assert.match(b.element('recommendationResults').innerHTML, /profile.html/);
    const c = await page(undefined, saved('stale'));
    assert.match(c.element('recommendationStatus').textContent, /older version of your profile/);
    assert.match(c.element('recommendationResults').innerHTML, /Film/);
});

test('saved failure retries GET, not generation; manual generation still works', async () => {
    const p = await page(undefined, response(503, {}));
    assert.match(p.element('recommendationStatus').textContent, /temporarily unavailable/);
    p.reply(async () => saved());
    await p.element('recommendationRetry').click();
    assert.equal(p.calls.at(-1).url, '/api/recommendations/latest');
    assert.match(p.element('recommendationResults').innerHTML, /Film/);
    p.reply(async () => result([movie]));
    await p.submit();
    assert.equal(p.calls.at(-1).url, '/api/recommendations/generate');
    assert.equal(p.element('savedDetails').textContent, '');
});

test('saved loading serializes manual and refresh requests', async () => {
    const p = await page();
    let release;
    p.reply(() => new Promise(resolve => { release = resolve; }));
    const pending = p.element('savedRefresh').click();
    await p.submit();
    await p.element('savedRefresh').click();
    assert.equal(p.calls.length, 3);
    assert.equal(p.element('recommendationSubmit').disabled, true);
    release(saved());
    await pending;
    assert.equal(p.element('recommendationSubmit').disabled, false);
    assert.equal(p.element('savedRefresh').disabled, false);
});

test('saved 401 hides content and invalid payloads offer retry', async () => {
    const p = await page(undefined, response(401, {}));
    assert.equal(p.element('recommendationsContent').classList.contains('hidden'), true);
    assert.equal(p.element('authBtn').textContent, 'Log in');
    for (const data of [{status:'fresh'}, {status:'missing', current_profile_revision:1, result:{}},
                        {status:'unknown', current_profile_revision:1}]) {
        const q = await page(undefined, response(200, data));
        assert.match(q.element('recommendationStatus').textContent, /invalid response/);
        assert.equal(q.element('recommendationResults').innerHTML, '');
        assert.equal(q.element('recommendationSubmit').disabled, false);
    }
});
