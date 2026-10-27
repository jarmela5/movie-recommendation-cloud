// Requests AI recommendations from the backend using the current session.
const recommendationPrompt = document.getElementById('recommendationPrompt');
const recommendationForm = document.getElementById('recommendationForm');
const recommendationSubmit = document.getElementById('recommendationSubmit');
const recommendationRetry = document.getElementById('recommendationRetry');
const recommendationResults = document.getElementById('recommendationResults');
const recommendationStatus = document.getElementById('recommendationStatus');
const sessionPanel = document.getElementById('sessionPanel');
const sessionTitle = document.getElementById('sessionTitle');
const sessionMessage = document.getElementById('sessionMessage');
const sessionLogin = document.getElementById('sessionLogin');
const sessionRetry = document.getElementById('sessionRetry');
const savedRefresh = document.getElementById('savedRefresh');
const savedDetails = document.getElementById('savedDetails');
let loadingSaved = false;
let retrySaved = false;
let submitting = false;
let sessionReady = false;
let checkingSession = false;

function requestErrorMessage(error) {
    if (error?.status === 504 || error?.name === 'TimeoutError' || error?.name === 'AbortError') {
        return 'The service took too long to respond. Please try again.';
    }
    if (error instanceof TypeError) {
        return 'Could not connect to the service. Check your connection and try again.';
    }
    if (error?.status === 502 || error?.status === 503) {
        return 'The service is temporarily unavailable. Please try again shortly.';
    }
    if (error?.code === 'INVALID_RECOMMENDATIONS') {
        return 'The recommendation service returned an invalid response. Please try again.';
    }
    if (error?.status === 422) {
        return 'Please use a request of up to 500 characters about genres or years. Runtime, cast, director and plot constraints are not supported.';
    }
    return 'Something went wrong while contacting the service. Please try again.';
}

function showLoginRequired(expired = false) {
    sessionReady = false;
    savedDetails.textContent = '';
    recommendationResults.replaceChildren();
    recommendationStatus.textContent = '';
    recommendationStatus.classList.remove('recommendation-error');
    recommendationRetry.classList.add('hidden');
    document.getElementById('recommendationsContent').classList.add('hidden');
    sessionPanel.classList.remove('hidden');
    sessionRetry.classList.add('hidden');
    sessionLogin.classList.remove('hidden');
    sessionTitle.textContent = expired ? 'Please log in again' : 'Your movie night starts here.';
    sessionMessage.textContent = expired
        ? 'Your session is no longer valid. Log in again to get recommendations.'
        : 'Log in to get recommendations based on your profile genres.';
    updateNav(null);
}

function readRecommendations(response) {
    const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const isText = value => typeof value === 'string' && value.trim().length > 0;
    const isMovie = movie => isObject(movie)
        && Number.isSafeInteger(movie.id) && movie.id > 0
        && isText(movie.title)
        && Array.isArray(movie.genres) && movie.genres.every(isText)
        && isText(movie.reason)
        && (movie.rating == null || (
            typeof movie.rating === 'number' && Number.isFinite(movie.rating)
            && movie.rating >= 0 && movie.rating <= 5
        ));

    // Validate the whole list before rendering, so a bad item cannot leave partial results.
    if (!isObject(response) || !isText(response.method)
        || !Array.isArray(response.recommendations) || !response.recommendations.every(isMovie)) {
        const error = new Error('Invalid recommendation response');
        error.code = 'INVALID_RECOMMENDATIONS';
        throw error;
    }
    return response.recommendations;
}

function recommendationCard(movie) {
    const rating = Number(movie.rating);
    const ratingText = movie.rating != null && Number.isFinite(rating) && rating > 0
        ? `${rating.toFixed(1)}/5 · Dataset average`
        : 'No rating available';
    return `
        <article class="card recommendation-card">
            <h3>${esc(movie.title)}</h3>
            <p class="genre-list">${movie.genres.length
                ? movie.genres.map(genre => `<span>${esc(genre)}</span>`).join('')
                : 'Genres unavailable'}</p>
            <p class="movie-rating">${esc(ratingText)}</p>
            <p class="recommendation-reason">${esc(movie.reason)}</p>
        </article>
    `;
}

function renderRecommendations(movies) {
    if (!movies.length) {
        recommendationStatus.textContent = 'No films match your request and profile restrictions.';
        recommendationResults.innerHTML = `
            <div class="recommendations-empty">
                <h3>No matching films</h3>
                <p>No films matched the request and profile restrictions used for this selection.</p>
                <p><a href="profile.html">Adjust your preferred or excluded genres</a> or try another request.</p>
            </div>`;
        return;
    }
    recommendationStatus.textContent = `${movies.length} recommendation${movies.length === 1 ? '' : 's'} selected by AI from the movie catalogue.`;
    recommendationResults.innerHTML = `<div class="grid recommendations-grid">${movies.map(recommendationCard).join('')}</div>`;
}

function readSaved(response) {
    const validRevision = value => Number.isSafeInteger(value) && value >= 0;
    const invalid = () => {
        const error = new Error('Invalid saved recommendations');
        error.code = 'INVALID_RECOMMENDATIONS';
        throw error;
    };
    if (!response || !validRevision(response.current_profile_revision)) invalid();
    if (response.status === 'missing') {
        if (response.result !== null) invalid();
        return null;
    }
    if (!['fresh', 'stale'].includes(response.status)
        || !validRevision(response.profile_revision)
        || (response.status === 'fresh') !== (response.profile_revision === response.current_profile_revision)
        || typeof response.generated_at !== 'string' || !Number.isFinite(Date.parse(response.generated_at))
        || typeof response.prompt !== 'string') invalid();
    return readRecommendations(response.result);
}

async function loadSavedRecommendations() {
    if (!sessionReady || submitting || loadingSaved) return;
    loadingSaved = true;
    retrySaved = true;
    savedRefresh.disabled = true;
    recommendationSubmit.disabled = true;
    recommendationRetry.disabled = true;
    recommendationRetry.classList.add('hidden');
    recommendationResults.replaceChildren();
    recommendationResults.setAttribute('aria-busy', 'true');
    recommendationStatus.classList.remove('recommendation-error');
    recommendationStatus.textContent = 'Loading your saved recommendations…';
    savedDetails.textContent = '';
    try {
        const response = await api('/api/recommendations/latest', {
            signal: AbortSignal.timeout(15000),
        });
        const movies = readSaved(response);
        if (movies === null) {
            recommendationStatus.textContent = 'No saved recommendations yet. The background agent will prepare them, or you can generate them now.';
            return;
        }
        renderRecommendations(movies);
        const date = new Date(response.generated_at).toLocaleString();
        savedDetails.textContent = `Saved on ${date}. ` + (response.prompt.trim()
            ? `Previous request: ${response.prompt}` : 'Based on your profile.');
        if (response.status === 'stale') {
            recommendationStatus.textContent = 'These saved results use an older version of your profile and may not match your current preferences. The agent will update them; you can also generate new recommendations now.';
        } else {
            recommendationStatus.textContent = movies.length
                ? `${movies.length} saved recommendation${movies.length === 1 ? '' : 's'} ready for you.`
                : 'Your saved selection contains no matching films. Adjust your preferences or try another request.';
        }
    } catch (error) {
        if (error.status === 401) {
            showLoginRequired(true);
            return;
        }
        recommendationStatus.textContent = requestErrorMessage(error);
        recommendationStatus.classList.add('recommendation-error');
        recommendationRetry.classList.remove('hidden');
    } finally {
        loadingSaved = false;
        savedRefresh.disabled = false;
        recommendationSubmit.disabled = false;
        recommendationRetry.disabled = false;
        recommendationResults.setAttribute('aria-busy', 'false');
    }
}

async function showRecommendations(event) {
    event.preventDefault();
    if (submitting || loadingSaved || !sessionReady) return;
    submitting = true;
    retrySaved = false;
    savedRefresh.disabled = true;
    savedDetails.textContent = '';
    recommendationSubmit.disabled = true;
    recommendationPrompt.disabled = true;
    recommendationRetry.disabled = true;
    recommendationForm.setAttribute('aria-busy', 'true');
    recommendationSubmit.textContent = 'Loading recommendations…';
    recommendationRetry.classList.add('hidden');
    recommendationResults.replaceChildren();
    recommendationResults.setAttribute('aria-busy', 'true');
    recommendationStatus.classList.remove('recommendation-error');
    recommendationStatus.textContent = 'The AI is selecting films… This can take a little while.';

    try {
        const body = { limit: 5 };
        const prompt = recommendationPrompt.value.trim();
        if (prompt) body.prompt = prompt;
        const response = await sendJSON('/api/recommendations/generate', body);
        const movies = readRecommendations(response);
        renderRecommendations(movies);
    } catch (error) {
        if (error.status === 401) {
            showLoginRequired(true);
            return;
        }
        recommendationStatus.textContent = requestErrorMessage(error);
        recommendationStatus.classList.add('recommendation-error');
        recommendationRetry.classList.remove('hidden');
    } finally {
        submitting = false;
        savedRefresh.disabled = false;
        recommendationSubmit.disabled = false;
        recommendationPrompt.disabled = false;
        recommendationRetry.disabled = false;
        recommendationForm.setAttribute('aria-busy', 'false');
        recommendationSubmit.textContent = 'Get recommendations';
        recommendationResults.setAttribute('aria-busy', 'false');
    }
}

async function startRecommendations() {
    if (checkingSession) return;
    checkingSession = true;
    sessionReady = false;
    sessionRetry.disabled = true;
    recommendationResults.replaceChildren();
    document.getElementById('recommendationsContent').classList.add('hidden');
    sessionPanel.classList.remove('hidden');
    sessionRetry.classList.add('hidden');
    sessionLogin.classList.add('hidden');
    sessionTitle.textContent = 'Checking your session';
    sessionMessage.textContent = 'One moment…';
    try {
        const me = await loadNav({ strict: true });
        if (!me) {
            showLoginRequired();
            return;
        }
        document.getElementById('preferencesHint').classList.toggle(
            'hidden', Boolean(me.likes_genres || me.avoids_genres),
        );
        sessionReady = true;
        sessionPanel.classList.add('hidden');
        document.getElementById('recommendationsContent').classList.remove('hidden');
        await loadSavedRecommendations();
    } catch (error) {
        sessionTitle.textContent = 'Could not check your session';
        sessionMessage.textContent = requestErrorMessage(error);
        sessionRetry.classList.remove('hidden');
    } finally {
        checkingSession = false;
        sessionRetry.disabled = false;
    }
}

recommendationForm.addEventListener('submit', showRecommendations);
recommendationRetry.addEventListener('click', () => retrySaved
    ? loadSavedRecommendations() : recommendationForm.requestSubmit());
savedRefresh.addEventListener('click', loadSavedRecommendations);
sessionRetry.addEventListener('click', startRecommendations);
startRecommendations();
