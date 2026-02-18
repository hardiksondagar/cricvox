// === Mode (static vs API) ===
const isStaticMode = !!window.CRICVOX_STATIC;
const BASE_PATH = window.CRICVOX_BASE_PATH || '';
const autoPlay = new URLSearchParams(window.location.search).get('autoplay') === 'true';

// === State ===
let currentMatchId = null;
let currentMatch = null;       // cached match object from GET /api/matches/{id}
let currentView = 'home';     // 'home' | 'match'
let selectedLang = 'hi';
let lastSeq = 0;
let pollTimer = null;
let allCommentaries = [];     // single source of truth — commentary + timeline
let isPlaying = false;
let playbackIndex = -1;       // cursor position in allCommentaries
let currentAudio = null;       // reference to currently playing Audio
let playbackTimer = null;      // pending setTimeout between tracks

// Scoreboard tracking
let currentOverBalls = [];
let currentOverRuns = 0;
let totalBoundaries = { fours: 0, sixes: 0 };
let totalExtras = 0;
let totalDotBalls = 0;
let totalBalls = 0;
let recentOversData = [];
let currentInnings = 1;

// Per-innings team info derived from match data
let inningsTeamInfo = {};           // { 1: { batting_team, bowling_team }, 2: { ... } }

// Timeline state — derived entirely from allCommentaries
let inningsSummary = [];            // From match.match_info.innings_summary
let timelineItems = [];             // Filtered from allCommentaries (excludes end_of_over)
let timelineIdxToCommIdx = {};      // timeline index -> allCommentaries index
let commIdxToTimelineIdx = {};      // allCommentaries index -> timeline index
let ballIdToTimelineIdx = {};       // ball_id -> first timeline index for that ball
let isLiveMode = false;             // Following live edge
let matchStatus = 'ready';          // 'ready' | 'generating' | 'generated'
let isDragging = false;             // Dragging the timeline cursor
let lastScrubCommentaryIdx = null;  // Set during scrub; used to play audio on mouseup/touchend only
let lastScrubHadCommentary = false;

// === DOM refs ===
const liveBadge = document.getElementById('liveBadge');
const commentaryFeed = document.getElementById('commentaryFeed');
const ballIndicator = document.getElementById('ballIndicator');
const homeView = document.getElementById('homeView');
const matchView = document.getElementById('matchView');
const matchListContainer = document.getElementById('matchList');
const navMatchControls = document.getElementById('navMatchControls');
const playBtn = document.getElementById('playBtn');

// Timeline DOM refs
const tlBar = document.getElementById('timelineBar');
const tlTrack = document.getElementById('timelineTrackContainer');
const tlFilled = document.getElementById('timelineFilled');
const tlBadges = document.getElementById('timelineBadges');
const tlInningsSep = document.getElementById('timelineInningsSep');
const tlCursor = document.getElementById('timelineCursor');
const tlCursorBadge = document.getElementById('timelineCursorBadge');
const tlTooltip = document.getElementById('timelineTooltip');
const tlTooltipOver = document.getElementById('tooltipOver');
const tlTooltipPlayers = document.getElementById('tooltipPlayers');
const tlTooltipEvent = document.getElementById('tooltipEvent');
const tlLiveBadge = document.getElementById('timelineLiveBadge');
const tlGoLive = document.getElementById('timelineGoLive');
const tlPlayIcon = document.getElementById('timelinePlayIcon');
const tlPauseIcon = document.getElementById('timelinePauseIcon');
const tlInningsLabels = document.getElementById('timelineInningsLabels');

// Match info strip DOM refs
const matchInfoStrip = document.getElementById('matchInfoStrip');
const matchSeriesEl = document.getElementById('matchSeries');
const matchDescEl = document.getElementById('matchDesc');
const matchDescSep = document.getElementById('matchDescSep');
const matchVenueEl = document.getElementById('matchVenue');
const matchVenueSep = document.getElementById('matchVenueSep');

const els = {
    battingTeam: document.getElementById('battingTeam'),
    bowlingTeam: document.getElementById('bowlingTeam'),
    totalRuns: document.getElementById('totalRuns'),
    wickets: document.getElementById('wickets'),
    overs: document.getElementById('overs'),
    target: document.getElementById('target'),
    crr: document.getElementById('crr'),
    rrr: document.getElementById('rrr'),
    runsNeeded: document.getElementById('runsNeeded'),
    ballsRemaining: document.getElementById('ballsRemaining'),
    currentBowler: document.getElementById('currentBowler'),
    batterName: document.getElementById('batterName'),
    batterRuns: document.getElementById('batterRuns'),
    batterBalls: document.getElementById('batterBalls'),
    nonBatterName: document.getElementById('nonBatterName'),
    nonBatterRuns: document.getElementById('nonBatterRuns'),
    nonBatterBalls: document.getElementById('nonBatterBalls'),
    bowlerRuns: document.getElementById('bowlerRuns'),
    bowlerWickets: document.getElementById('bowlerWickets'),
    matchPhase: document.getElementById('matchPhase'),
    overRuns: document.getElementById('overRuns'),
    fours: document.getElementById('fours'),
    sixes: document.getElementById('sixes'),
    extras: document.getElementById('extras'),
    dotBalls: document.getElementById('dotBalls'),
    dotPercent: document.getElementById('dotPercent'),
    crrBar: document.getElementById('crrBar'),
    rrrBar: document.getElementById('rrrBar'),
    crrBarValue: document.getElementById('crrBarValue'),
    rrrBarValue: document.getElementById('rrrBarValue'),
    rateDifference: document.getElementById('rateDifference'),
    partnershipRuns: document.getElementById('partnershipRuns'),
    partnershipBalls: document.getElementById('partnershipBalls'),
    partnershipWicket: document.getElementById('partnershipWicket'),
    recentOvers: document.getElementById('recentOvers'),
};


// === Data Abstraction (API vs static JSON) ===
async function fetchMatchList() {
    if (isStaticMode) {
        const resp = await fetch(`${BASE_PATH}/data/matches.json`);
        return resp.json();
    }
    const resp = await fetch('/api/matches');
    return resp.json();
}

async function fetchMatchDetail(matchId) {
    if (isStaticMode) {
        const resp = await fetch(`${BASE_PATH}/data/matches/${matchId}/match.json`);
        return resp.json();
    }
    const resp = await fetch(`/api/matches/${matchId}`);
    return resp.json();
}

async function fetchCommentaries(matchId, afterSeq, lang) {
    if (isStaticMode) {
        const resp = await fetch(`${BASE_PATH}/data/matches/${matchId}/commentaries/${lang}.json`);
        const data = await resp.json();
        return afterSeq > 0 ? data.filter((c) => c.seq > afterSeq) : data;
    }
    const resp = await fetch(`/api/matches/${matchId}/commentaries?after_seq=${afterSeq}&language=${lang}`);
    return resp.json();
}

function resolveAudioUrl(url) {
    if (!url) return url;
    if (isStaticMode && url.startsWith('/static/audio/')) {
        return BASE_PATH + url.replace(/^\/static\/audio/, '/audio');
    }
    return url;
}


// === URL Routing ===
function pushUrl(path) {
    if (isStaticMode) {
        const hash = path === '/' ? '#/' : `#${path}`;
        if (location.hash !== hash) {
            location.hash = hash;
        }
    } else {
        if (window.location.pathname !== path) {
            history.pushState(null, '', path);
        }
    }
}

function getMatchIdFromUrl() {
    if (isStaticMode) {
        const m = (location.hash || '#/').match(/#\/match\/(\d+)/);
        return m ? parseInt(m[1], 10) : null;
    }
    const m = window.location.pathname.match(/^\/match\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
}

if (isStaticMode) {
    window.addEventListener('hashchange', () => routeFromUrl());
} else {
    window.addEventListener('popstate', () => routeFromUrl());
}

function routeFromUrl() {
    const matchId = getMatchIdFromUrl();
    if (matchId) {
        openMatch(matchId);
    } else {
        showHome();
    }
}


// === View Management ===
function showHome() {
    currentView = 'home';
    pushUrl('/');
    homeView.classList.remove('hidden');
    matchView.classList.add('hidden');
    navMatchControls.classList.add('hidden');
    liveBadge.classList.add('hidden');
    stopPolling();
    stopAudio();
    resetTimeline();
    loadMatchList();
}

function showMatchView(matchId) {
    currentView = 'match';
    currentMatchId = matchId;
    pushUrl(`/match/${matchId}`);
    homeView.classList.add('hidden');
    matchView.classList.remove('hidden');
    navMatchControls.classList.remove('hidden');
    navMatchControls.classList.add('flex');
}


// === Match List ===
async function loadMatchList() {
    try {
        const matches = await fetchMatchList();
        renderMatchList(matches);
    } catch (e) {
        console.error('Failed to load matches:', e);
        matchListContainer.innerHTML = '<div class="text-sm text-neutral-600 text-center py-8">Failed to load matches</div>';
    }
}

function renderMatchList(matches) {
    if (!matches.length) {
        matchListContainer.innerHTML = '<div class="text-center py-12"><p class="text-sm text-neutral-600">No matches yet.</p></div>';
        return;
    }

    matchListContainer.innerHTML = matches.map(m => {
        const info = m.match_info || {};
        let teams = m.title;
        if (info.teams && info.teams.length >= 2) {
            teams = `${info.teams[0]} vs ${info.teams[1]}`;
        } else if (info.batting_team && info.bowling_team) {
            teams = `${info.batting_team} vs ${info.bowling_team}`;
        }

        let inningsSummaryText = '';
        if (info.innings_summary && info.innings_summary.length > 0) {
            inningsSummaryText = info.innings_summary.map(inn =>
                `${inn.batting_team} ${inn.total_runs}/${inn.total_wickets}`
            ).join(' · ');
        }

        const date = new Date(m.created_at).toLocaleDateString('en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        });

        const statusColors = {
            generating: 'text-red-400',
            generated: 'text-emerald-400',
            ready: 'text-neutral-500',
        };
        const statusBadge = m.status === 'generating'
            ? '<span class="inline-flex items-center gap-1 text-[10px] font-bold tracking-widest uppercase text-red-400"><span class="w-1.5 h-1.5 bg-red-400 rounded-full animate-pulse"></span>Generating</span>'
            : `<span class="text-[10px] font-bold tracking-widest uppercase ${statusColors[m.status] || 'text-neutral-600'}">${m.status}</span>`;

        let actions = '';
        if (m.status === 'generating') {
            actions = `<button onclick="openMatch(${m.match_id})" class="h-8 px-4 text-xs font-semibold bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-md transition-colors">Watch</button>`;
        } else if (m.status === 'generated') {
            actions = `<button onclick="openMatch(${m.match_id})" class="h-8 px-4 text-xs font-semibold bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 rounded-md transition-colors">View</button>`;
        } else {
            actions = `<span class="text-xs text-neutral-600">Not generated</span>`;
        }

        return `
            <div class="match-list-item p-4 rounded-lg bg-white/[0.03] border border-white/5 hover:bg-white/[0.05] transition-colors">
                <div class="flex items-center justify-between">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1">
                            ${statusBadge}
                            <span class="text-[10px] text-neutral-600">${date}</span>
                        </div>
                        <div class="text-sm font-semibold text-white truncate">${teams}</div>
                        <div class="text-xs text-neutral-500 mt-0.5">${m.title}</div>
                        ${inningsSummaryText ? `<div class="text-xs text-neutral-600 mt-1 font-mono">${inningsSummaryText}</div>` : ''}
                    </div>
                    <div class="flex items-center gap-2 ml-4">${actions}</div>
                </div>
            </div>
        `;
    }).join('');
}


// === Open Match ===
async function openMatch(matchId) {
    showMatchView(matchId);
    clearCommentary();
    resetTimeline();
    lastSeq = 0;
    allCommentaries = [];
    isPlaying = false;
    playbackIndex = -1;
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }

    try {
        // Fetch match data first
        const match = await fetchMatchDetail(matchId);
        currentMatch = match;
        matchStatus = isStaticMode ? 'generated' : match.status;

        // Store innings summary for timeline team name enrichment
        const matchInfo = match.match_info || {};
        inningsSummary = matchInfo.innings_summary || [];

        // Build per-innings team lookup from match data
        inningsTeamInfo = {};
        for (const inn of inningsSummary) {
            inningsTeamInfo[inn.innings_number] = {
                batting_team: inn.batting_team,
                bowling_team: inn.bowling_team,
                target: inn.target,
            };
        }
        if (matchInfo.first_innings) {
            inningsTeamInfo[1] = inningsTeamInfo[1] || {};
            inningsTeamInfo[1].batting_team = inningsTeamInfo[1].batting_team || matchInfo.first_innings.batting_team;
            inningsTeamInfo[1].bowling_team = inningsTeamInfo[1].bowling_team || matchInfo.first_innings.bowling_team;
        }
        if (matchInfo.second_innings) {
            inningsTeamInfo[2] = inningsTeamInfo[2] || {};
            inningsTeamInfo[2].batting_team = inningsTeamInfo[2].batting_team || matchInfo.second_innings.batting_team;
            inningsTeamInfo[2].bowling_team = inningsTeamInfo[2].bowling_team || matchInfo.second_innings.bowling_team;
            inningsTeamInfo[2].target = inningsTeamInfo[2].target || matchInfo.target;
        }
        if (!inningsTeamInfo[1]) {
            inningsTeamInfo[1] = { batting_team: matchInfo.team1 || match.team1, bowling_team: matchInfo.team2 || match.team2 };
        }

        // Populate match info strip (series, match type, venue)
        applyMatchInfoStrip(match);

        // Populate language dropdown from match languages
        applyMatchLanguages(match.languages || ['hi']);

        // Set match info in scoreboard (shows current/latest state)
        if (matchInfo.batting_team) els.battingTeam.textContent = matchInfo.batting_team;
        if (matchInfo.bowling_team) els.bowlingTeam.textContent = matchInfo.bowling_team;
        if (matchInfo.target) els.target.textContent = matchInfo.target;

        // Fetch commentaries
        const commentaries = await fetchCommentaries(matchId, 0, selectedLang);
        processCommentaries(commentaries);

        if (!isStaticMode && match.status === 'generating') {
            liveBadge.classList.remove('hidden');
            liveBadge.classList.add('flex');
            isLiveMode = true;
            updateLiveControls();
            startPolling(matchId);

            if (allCommentaries.length > 0) {
                playbackIndex = 0;
                if (autoPlay) resumePlayback();
            }
            updateTimelinePlayBtn();
        } else if (matchStatus === 'generated') {
            liveBadge.classList.add('hidden');
            isLiveMode = false;
            updateLiveControls();

            if (allCommentaries.length > 0) {
                playbackIndex = 0;
                if (autoPlay) resumePlayback();
            }
            updateTimelinePlayBtn();
        } else {
            liveBadge.classList.add('hidden');
            isLiveMode = false;
            updateLiveControls();
            if (allCommentaries.length === 0) {
                playBtn.classList.add('hidden');
                commentaryFeed.innerHTML = '<div class="feed-item feed-placeholder py-20 text-center"><p class="text-sm text-neutral-600">Commentary not generated yet</p></div>';
            } else {
                playBtn.classList.remove('hidden');
                playbackIndex = 0;
                updateTimelinePlayBtn();
            }
        }

        // Show timeline if we have data
        if (timelineItems.length > 0 && matchStatus !== 'ready') {
            showTimeline();
        }
    } catch (e) {
        console.error('Failed to open match:', e);
    }
}


// === Polling ===
function startPolling(matchId) {
    if (isStaticMode) return;
    stopPolling();
    pollTimer = setInterval(async () => {
        try {
            // Fetch match status and new commentaries in parallel
            const [match, commentaries] = await Promise.all([
                fetchMatchDetail(matchId),
                fetchCommentaries(matchId, lastSeq, selectedLang),
            ]);
            currentMatch = match;
            processCommentaries(commentaries);

            // If playing and waiting for more, continue playback
            if (isPlaying && !currentAudio && commentaries.length > 0) {
                playCurrentCommentary();
            }

            if (match.status !== 'generating') {
                matchStatus = match.status;
                stopPolling();
                liveBadge.classList.add('hidden');
                isLiveMode = false;
                updateLiveControls();
            }
        } catch (e) {
            console.error('Poll error:', e);
        }
    }, 3000);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}


// === Process Commentaries ===
function processCommentaries(commentaries) {
    for (const c of commentaries) {
        allCommentaries.push(c);
        if (c.seq > lastSeq) lastSeq = c.seq;

        if (c.event_type === 'first_innings_start') {
            const d = c.data || {};
            if (d.batting_team) els.battingTeam.textContent = d.batting_team;
            if (d.bowling_team) els.bowlingTeam.textContent = d.bowling_team;
            if (d.target) els.target.textContent = d.target;
            updateChaseDisplay(1);
            addCommentary(c, allCommentaries.length - 1);
        } else if (c.event_type === 'second_innings_start') {
            const d = c.data || {};
            if (d.batting_team) els.battingTeam.textContent = d.batting_team;
            if (d.bowling_team) els.bowlingTeam.textContent = d.bowling_team;
            if (d.target) els.target.textContent = d.target;
            updateChaseDisplay(2);
            addCommentary(c, allCommentaries.length - 1);
        } else if (c.event_type === 'delivery') {
            const d = c.data || {};
            const bi = c.ball_info;
            if (d.is_narrative) {
                addCommentary(c, allCommentaries.length - 1);
            } else if (bi) {
                try {
                    updateScoreboard(bi);
                    addBallDot(bi);
                } catch (e) {
                    console.warn('Scoreboard/ball-dot update failed:', e);
                }
                addBallFeedItem(c, bi);
            } else {
                addCommentary(c, allCommentaries.length - 1);
            }
        } else if (c.event_type === 'second_innings_end') {
            addCommentary(c, allCommentaries.length - 1);
        } else if ([
            'first_innings_end',
            'end_of_over', 'phase_change', 'milestone', 'new_batter'
        ].includes(c.event_type)) {
            addCommentary(c, allCommentaries.length - 1);
        }
    }

    // Rebuild timeline from commentaries whenever new data arrives
    if (commentaries.length > 0) {
        buildTimeline();
        if (timelineItems.length > 0) renderTimeline();
    }
}


// === Language Switching ===
async function switchLanguage(lang) {
    selectedLang = lang;
    lastSeq = 0;
    allCommentaries = [];
    clearCommentary();
    resetTimeline();
    stopAudio();
    if (!currentMatchId) return;

    try {
        const commentaries = await fetchCommentaries(currentMatchId, 0, selectedLang);
        processCommentaries(commentaries);

        if (allCommentaries.length > 0) {
            playbackIndex = 0;
            updateTimelinePlayBtn();
        }
        if (timelineItems.length > 0 && matchStatus !== 'ready') {
            showTimeline();
        }
    } catch (e) {
        console.error('Language switch failed:', e);
    }
}


// === Playback Controls ===
function clearPlaybackTimer() {
    if (playbackTimer) {
        clearTimeout(playbackTimer);
        playbackTimer = null;
    }
}

function scheduleNext() {
    clearPlaybackTimer();
    playbackTimer = setTimeout(() => {
        playbackTimer = null;
        playCurrentCommentary();
    }, 2000);
}

function togglePlay() {
    if (isPlaying) {
        pausePlayback();
    } else {
        resumePlayback();
    }
}

function resumePlayback() {
    if (!allCommentaries.length) return;
    clearPlaybackTimer();
    isPlaying = true;
    playBtn.textContent = 'Pause';
    updateTimelinePlayBtn();

    if (currentAudio && currentAudio.paused) {
        currentAudio.play().catch(() => {
            currentAudio = null;
            playbackIndex++;
            playCurrentCommentary();
        });
        return;
    }

    if (playbackIndex < 0 || playbackIndex >= allCommentaries.length) {
        playbackIndex = 0;
    }
    if (playbackIndex === 0) {
        resetScoreboard();
    }
    playCurrentCommentary();
}

function pausePlayback() {
    isPlaying = false;
    clearPlaybackTimer();
    playBtn.textContent = 'Play';
    updateTimelinePlayBtn();
    if (currentAudio) {
        currentAudio.pause();
    }
}

function playFrom(idx) {
    clearPlaybackTimer();
    stopAllAudioPlayback();
    playbackIndex = idx;
    if (idx === 0) resetScoreboard();
    isPlaying = true;
    playBtn.textContent = 'Pause';
    updateTimelinePlayBtn();
    playCurrentCommentary();
}

function playCurrentCommentary() {
    if (!isPlaying) return;

    while (playbackIndex < allCommentaries.length) {
        const c = allCommentaries[playbackIndex];
        if (c.audio_url) break;
        playbackIndex++;
    }

    if (playbackIndex >= allCommentaries.length) {
        if (pollTimer) {
            highlightPlayingItem(-1);
            updateTimelineCursor();
            return;
        }
        isPlaying = false;
        playBtn.textContent = 'Play';
        updateTimelinePlayBtn();
        highlightPlayingItem(-1);
        return;
    }

    const c = allCommentaries[playbackIndex];
    highlightPlayingItem(playbackIndex);
    scrollToPlayingItem(playbackIndex);
    updateTimelineCursor();

    // Determine current innings from this commentary and apply team names
    const innNum = (c.ball_info && c.ball_info.innings)
        || (c.data && c.data.innings)
        || (c.event_type === 'second_innings_start' ? 2
            : c.event_type === 'second_innings_end' ? 2 : null);
    if (innNum != null) {
        const teams = inningsTeamInfo[innNum] || {};
        if (teams.batting_team) els.battingTeam.textContent = teams.batting_team;
        if (teams.bowling_team) els.bowlingTeam.textContent = teams.bowling_team;
        if (innNum >= 2 && teams.target) els.target.textContent = teams.target;
        updateChaseDisplay(innNum);
    } else if (c.event_type === 'first_innings_start') {
        const teams = inningsTeamInfo[1] || {};
        if (teams.batting_team) els.battingTeam.textContent = teams.batting_team;
        if (teams.bowling_team) els.bowlingTeam.textContent = teams.bowling_team;
        updateChaseDisplay(1);
    }

    // Sync scoreboard to this commentary's ball_info (only for delivery events)
    if (c.event_type === 'delivery' && c.ball_info) {
        const tlIdx = commIdxToTimelineIdx[playbackIndex];
        if (tlIdx !== undefined) {
            applyScoreboardSnapshot(c.ball_info);
            rebuildCumulativeStatsForTimelineIdx(tlIdx);
        }
    }

    try {
        const a = new Audio(resolveAudioUrl(c.audio_url));
        a.volume = 0.8;
        currentAudio = a;
        a.onended = () => {
            currentAudio = null;
            playbackIndex++;
            scheduleNext();
        };
        a.onerror = () => {
            console.warn('Audio error');
            currentAudio = null;
            playbackIndex++;
            scheduleNext();
        };
        a.play().catch(() => {
            console.warn('Autoplay blocked');
            currentAudio = null;
            playbackIndex++;
            scheduleNext();
        });
    } catch (e) {
        console.warn('Audio:', e);
        currentAudio = null;
        playbackIndex++;
        scheduleNext();
    }
}

function findFeedItem(idx) {
    let el = commentaryFeed.querySelector(`[data-idx="${idx}"]`);
    if (!el && idx >= 0 && idx < allCommentaries.length) {
        const ballId = allCommentaries[idx].ball_id;
        if (ballId) el = commentaryFeed.querySelector(`[data-ball-id="${ballId}"]`);
    }
    return el;
}

function highlightPlayingItem(idx) {
    commentaryFeed.querySelectorAll('.feed-item-playing').forEach(el =>
        el.classList.remove('feed-item-playing')
    );
    if (idx >= 0) {
        const el = findFeedItem(idx);
        if (el) el.classList.add('feed-item-playing');
    }
}

function scrollToPlayingItem(idx) {
    const el = findFeedItem(idx);
    if (!el) return;
    const container = commentaryFeed;
    const elTop = el.offsetTop - container.offsetTop;
    const elHeight = el.offsetHeight;
    const containerHeight = container.clientHeight;
    const scrollTarget = elTop - (containerHeight / 2) + (elHeight / 2);
    container.scrollTo({ top: scrollTarget, behavior: 'smooth' });
}


// === Chase Display (Target/RRR/Need — 2nd innings only) ===
function updateChaseDisplay(innings) {
    currentInnings = innings;
    const show = innings >= 2;
    document.querySelectorAll('.chase-only').forEach(el => {
        el.classList.toggle('hidden', !show);
    });
}

// === Scoreboard ===
function resetScoreboard() {
    // Restore first innings team names from match data
    const firstInn = inningsTeamInfo[1] || {};
    els.battingTeam.textContent = firstInn.batting_team || '--';
    els.bowlingTeam.textContent = firstInn.bowling_team || '--';

    els.totalRuns.textContent = '0';
    els.wickets.textContent = '0';
    els.overs.textContent = '0.0';
    els.crr.textContent = '0.00';
    els.rrr.textContent = '0.00';
    els.runsNeeded.textContent = '0';
    els.ballsRemaining.textContent = '120';
    els.matchPhase.textContent = 'Powerplay';
    els.batterName.textContent = '--';
    els.nonBatterName.textContent = '--';
    els.batterRuns.textContent = '0';
    els.batterBalls.textContent = '0';
    els.nonBatterRuns.textContent = '0';
    els.nonBatterBalls.textContent = '0';
    els.currentBowler.textContent = '--';
    els.bowlerRuns.textContent = '0';
    els.bowlerWickets.textContent = '0';
    els.target.textContent = '';
    currentOverBalls = [];
    currentOverRuns = 0;
    totalBoundaries = { fours: 0, sixes: 0 };
    totalExtras = 0;
    totalDotBalls = 0;
    totalBalls = 0;
    recentOversData = [];
    els.overRuns.textContent = '0';
    updateStats();
    renderRecentOvers();
    renderBallIndicator();
    updateChaseDisplay(1);
}

function updateScoreboard(d) {
    if (d.innings != null) updateChaseDisplay(d.innings);
    if (d.total_runs != null) els.totalRuns.textContent = d.total_runs;
    if (d.total_wickets != null) els.wickets.textContent = d.total_wickets;
    if (d.overs) els.overs.textContent = d.overs;
    if (d.crr != null) els.crr.textContent = d.crr.toFixed(2);
    if (d.rrr != null) els.rrr.textContent = d.rrr.toFixed(2);
    if (d.runs_needed != null) els.runsNeeded.textContent = d.runs_needed;
    if (d.balls_remaining != null) els.ballsRemaining.textContent = d.balls_remaining;
    if (d.match_phase) els.matchPhase.textContent = d.match_phase;

    if (d.batter) els.batterName.textContent = d.batter;
    if (d.non_batter) els.nonBatterName.textContent = d.non_batter;
    if (d.bowler) els.currentBowler.textContent = d.bowler;

    if (d.batter_stats) {
        els.batterRuns.textContent = d.batter_stats.runs ?? 0;
        els.batterBalls.textContent = d.batter_stats.balls ?? 0;
    }
    if (d.non_batter_stats) {
        els.nonBatterRuns.textContent = d.non_batter_stats.runs ?? 0;
        els.nonBatterBalls.textContent = d.non_batter_stats.balls ?? 0;
    }
    if (d.bowler_stats) {
        els.bowlerWickets.textContent = d.bowler_stats.wickets ?? 0;
        els.bowlerRuns.textContent = d.bowler_stats.runs ?? 0;
    }

    if (d.crr != null && d.rrr != null) updateRunRateBars(d.crr, d.rrr);

    const ballRuns = d.ball_runs ?? ((d.runs || 0) + (d.extras || 0));
    totalBalls++;
    if (ballRuns === 0 && !d.is_wicket) totalDotBalls++;
    if (d.is_six) totalBoundaries.sixes++;
    else if (d.is_boundary) totalBoundaries.fours++;
    updateStats();

    if (d.is_six || d.is_boundary || d.is_wicket) {
        els.totalRuns.classList.add('flash');
        setTimeout(() => els.totalRuns.classList.remove('flash'), 600);
    }
}

/**
 * Apply score snapshot from a ball_info (e.g. when scrubbing).
 * Updates display only; does not increment cumulative stats.
 */
function applyScoreboardSnapshot(ball) {
    if (!ball) return;
    if (ball.innings != null) updateChaseDisplay(ball.innings);
    if (ball.batting_team != null) els.battingTeam.textContent = ball.batting_team;
    if (ball.bowling_team != null) els.bowlingTeam.textContent = ball.bowling_team;
    if (ball.total_runs != null) els.totalRuns.textContent = ball.total_runs;
    if (ball.total_wickets != null) els.wickets.textContent = ball.total_wickets;
    if (ball.overs != null) els.overs.textContent = ball.overs;
    if (ball.crr != null) els.crr.textContent = ball.crr.toFixed(2);
    if (ball.rrr != null) els.rrr.textContent = ball.rrr.toFixed(2);
    if (ball.runs_needed != null) els.runsNeeded.textContent = ball.runs_needed;
    if (ball.balls_remaining != null) els.ballsRemaining.textContent = ball.balls_remaining;
    if (ball.match_phase != null) els.matchPhase.textContent = ball.match_phase;
    els.batterName.textContent = ball.batter ?? '';
    els.nonBatterName.textContent = ball.non_batter ?? '';
    els.currentBowler.textContent = ball.bowler ?? '';
    if (ball.batter_stats) {
        els.batterRuns.textContent = ball.batter_stats.runs ?? 0;
        els.batterBalls.textContent = ball.batter_stats.balls ?? 0;
    } else {
        els.batterRuns.textContent = '0';
        els.batterBalls.textContent = '0';
    }
    if (ball.non_batter_stats) {
        els.nonBatterRuns.textContent = ball.non_batter_stats.runs ?? 0;
        els.nonBatterBalls.textContent = ball.non_batter_stats.balls ?? 0;
    } else {
        els.nonBatterRuns.textContent = '0';
        els.nonBatterBalls.textContent = '0';
    }
    if (ball.bowler_stats) {
        els.bowlerWickets.textContent = ball.bowler_stats.wickets ?? 0;
        els.bowlerRuns.textContent = ball.bowler_stats.runs ?? 0;
    } else {
        els.bowlerWickets.textContent = '0';
        els.bowlerRuns.textContent = '0';
    }
    if (ball.crr != null && ball.rrr != null) updateRunRateBars(ball.crr, ball.rrr);
}

/**
 * Get ball display config for a ball_info object.
 */
function getBallDisplay(b) {
    const ballRuns = b.ball_runs ?? ((b.runs || 0) + (b.extras || 0));
    const et = (b.extras_type || '').toLowerCase();
    if (b.is_wicket) return { text: 'W', className: 'wicket', runs: 0 };
    if (et === 'wide' || et === 'wides') return { text: ballRuns > 0 ? `${ballRuns}wd` : 'wd', className: 'extra', runs: ballRuns };
    if (et === 'noball' || et === 'no_ball') return { text: ballRuns > 0 ? `${ballRuns}nb` : 'nb', className: 'extra', runs: ballRuns };
    if (ballRuns === 0) return { text: '0', className: 'runs-0', runs: 0 };
    if (b.is_six) return { text: '6', className: 'runs-6', runs: 6 };
    if (b.is_boundary) return { text: '4', className: 'runs-4', runs: 4 };
    const r = Math.min(ballRuns, 4);
    return { text: String(ballRuns), className: `runs-${r}`, runs: ballRuns };
}

/**
 * Render This Over ball indicator: 6 fixed slots (with placeholders for empty), extras on new line.
 */
function renderBallIndicator() {
    const mainBalls = currentOverBalls.slice(0, 6);
    const extraBalls = currentOverBalls.slice(6);

    let html = '<div class="this-over-row">';
    for (let i = 0; i < 6; i++) {
        const ball = mainBalls[i];
        if (ball) {
            html += `<div class="ball-dot ${ball.className}">${ball.text}</div>`;
        } else {
            html += '<div class="ball-dot ball-placeholder" aria-hidden="true"></div>';
        }
    }
    html += '</div>';
    if (extraBalls.length) {
        html += '<div class="this-over-row this-over-extras">';
        for (const ball of extraBalls) {
            html += `<div class="ball-dot ${ball.className}">${ball.text}</div>`;
        }
        html += '</div>';
    }
    ballIndicator.innerHTML = html;
}

/**
 * Rebuild cumulative stats and ball indicator for balls 0..idx (when scrubbing).
 */
function rebuildCumulativeStatsForTimelineIdx(idx) {
    currentOverBalls = [];
    currentOverRuns = 0;
    totalBoundaries = { fours: 0, sixes: 0 };
    totalExtras = 0;
    totalDotBalls = 0;
    totalBalls = 0;
    recentOversData = [];
    ballIndicator.innerHTML = '';

    for (let i = 0; i <= idx && i < timelineItems.length; i++) {
        const item = timelineItems[i];
        if (item.type !== 'ball' || !item.ball_info) continue;
        const b = item.ball_info;
        const ballRuns = (b.runs || 0) + (b.extras || 0);
        const oversStr = b.overs || `${b.over}.${b.ball}`;
        const parts = oversStr.split('.');
        const ballNum = parseInt(parts[1] || 0, 10);

        if ((ballNum === 1 || ballNum === 0) && currentOverBalls.length >= 6) {
            const overNum = parseInt(parts[0] || 0, 10);
            if (overNum > 0) {
                recentOversData.unshift({ over: overNum, runs: currentOverRuns });
                if (recentOversData.length > 5) recentOversData.pop();
            }
            currentOverBalls = [];
            currentOverRuns = 0;
        }

        totalBalls++;
        if (ballRuns === 0 && !b.is_wicket) totalDotBalls++;
        if (b.is_six) totalBoundaries.sixes++;
        else if (b.is_boundary) totalBoundaries.fours++;
        totalExtras += b.extras || 0;

        currentOverBalls.push(getBallDisplay(b));
        currentOverRuns += currentOverBalls[currentOverBalls.length - 1].runs;
    }

    els.overRuns.textContent = currentOverRuns;
    updateStats();
    renderRecentOvers();
    renderBallIndicator();
}

function updateRunRateBars(crr, rrr) {
    const max = Math.max(crr, rrr, 12);
    els.crrBar.style.width = `${(crr / max) * 100}%`;
    els.rrrBar.style.width = `${(rrr / max) * 100}%`;
    els.crrBarValue.textContent = crr.toFixed(2);
    els.rrrBarValue.textContent = rrr.toFixed(2);

    const diff = crr - rrr;
    if (diff > 0) {
        els.rateDifference.textContent = `+${diff.toFixed(1)} ahead`;
        els.rateDifference.className = 'mt-3 text-center text-[11px] font-medium text-emerald-500';
    } else if (diff < 0) {
        els.rateDifference.textContent = `${diff.toFixed(1)} behind`;
        els.rateDifference.className = 'mt-3 text-center text-[11px] font-medium text-red-400';
    } else {
        els.rateDifference.textContent = 'On target';
        els.rateDifference.className = 'mt-3 text-center text-[11px] font-medium text-neutral-600';
    }
}

function updateStats() {
    els.fours.textContent = totalBoundaries.fours;
    els.sixes.textContent = totalBoundaries.sixes;
    els.extras.textContent = totalExtras;
    els.dotBalls.textContent = totalDotBalls;
    if (totalBalls > 0) {
        els.dotPercent.textContent = `(${((totalDotBalls / totalBalls) * 100).toFixed(0)}%)`;
    }
}


// === Ball Indicator ===
function addBallDot(d) {
    const parts = d.overs.split('.');
    const ballNum = parseInt(parts[1] || 0);

    if ((ballNum === 1 || ballNum === 0) && currentOverBalls.length >= 6) {
        const overNum = parseInt(parts[0]);
        if (overNum > 0) addRecentOver(overNum, currentOverRuns);
        currentOverBalls = [];
        currentOverRuns = 0;
    }

    const display = getBallDisplay(d);
    currentOverBalls.push(display);
    currentOverRuns += display.runs;
    els.overRuns.textContent = currentOverRuns;
    renderBallIndicator();
}

function addRecentOver(num, runs) {
    recentOversData.unshift({ over: num, runs });
    if (recentOversData.length > 5) recentOversData.pop();
    renderRecentOvers();
}

function renderRecentOvers() {
    if (!recentOversData.length) {
        els.recentOvers.innerHTML = '<div class="text-xs text-neutral-700 text-center py-3">No overs yet</div>';
        return;
    }
    els.recentOvers.innerHTML = recentOversData.map(o => `
        <div class="recent-over-item">
            <span class="recent-over-num">Ov ${o.over}</span>
            <span class="recent-over-runs">${o.runs}</span>
        </div>
    `).join('');
}


// === Commentary ===
function getBallIndicatorClass(ballInfo, data) {
    if (!ballInfo) return 'ball-0';
    if (ballInfo.is_wicket || data.is_wicket) return 'ball-W';
    if (ballInfo.is_six || data.is_six) return 'ball-6';
    if (ballInfo.is_boundary || data.is_boundary) return 'ball-4';
    const runs = ballInfo.ball_runs ?? data.ball_runs ?? (ballInfo.runs || 0) + (ballInfo.extras || 0);
    if (runs >= 1 && runs <= 3) return `ball-${runs}`;
    if (ballInfo.extras_type === 'wide') return 'ball-wd';
    if (ballInfo.extras_type === 'noball') return 'ball-nb';
    return 'ball-0';
}

function getBallIndicatorLabel(ballInfo, data) {
    if (!ballInfo) return '·';
    if (ballInfo.is_wicket || data.is_wicket) return 'W';
    if (ballInfo.is_six || data.is_six) return '6';
    if (ballInfo.is_boundary || data.is_boundary) return '4';
    const runs = ballInfo.ball_runs ?? data.ball_runs ?? (ballInfo.runs || 0) + (ballInfo.extras || 0);
    if (ballInfo.extras_type === 'wide') return 'wd';
    if (ballInfo.extras_type === 'noball') return 'nb';
    return `${runs}`;
}

function getNarrativeIcon(type) {
    const icons = {
        first_innings_start: '🏟️',
        first_innings_end: '📊',
        second_innings_start: '🎯',
        second_innings_end: '🏁',
        end_of_over: '↻',
        new_batter: '🏃',
        phase_change: '⚡',
        milestone: '⭐',
    };
    return icons[type] || '✦';
}

/**
 * Add a feed item for a ball from its commentary event.
 */
function addBallFeedItem(c, bi) {
    const placeholder = commentaryFeed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    const d = c.data || {};
    const ballId = c.ball_id;
    const idx = allCommentaries.length - 1;

    const item = document.createElement('div');
    item.className = 'feed-item';
    if (ballId) item.setAttribute('data-ball-id', ballId);
    item.setAttribute('data-idx', idx);
    if (d.is_pivot) item.classList.add('pivot');

    const over = bi ? `${bi.over}.${bi.ball}` : '';
    const batsmanBowler = bi ? `${(bi.batter || '')} vs ${(bi.bowler || '')}` : '';
    const indicatorClass = getBallIndicatorClass(bi, bi);
    const indicatorLabel = getBallIndicatorLabel(bi, bi);

    item.innerHTML = `
        <div class="feed-ball-col">
            <div class="feed-ball-over">${over}</div>
            <div class="feed-ball-indicator-wrap">
                <div class="feed-ball-indicator ${indicatorClass}">${indicatorLabel}</div>
                <button class="feed-play-overlay" title="Play from here"><svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><path d="M8 5v14l11-7z"/></svg></button>
            </div>
        </div>
        <div class="feed-content-col">
            <div class="feed-meta">
                <span class="feed-over">${batsmanBowler}</span>
            </div>
            <div class="feed-text"></div>
        </div>
    `;
    const textEl = item.querySelector('.feed-text');
    if (textEl) textEl.textContent = c.text || '';

    commentaryFeed.insertBefore(item, commentaryFeed.firstChild);
}

/**
 * Add a narrative commentary item to the feed.
 */
function addCommentary(c, idx) {
    const placeholder = commentaryFeed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    const d = c.data || {};
    const item = document.createElement('div');
    item.setAttribute('data-idx', idx);

    const narrType = d.narrative_type || c.event_type || 'general';
    item.className = `feed-item feed-narrative`;
    const labels = {
        first_innings_start: 'First Innings',
        first_innings_end: 'Innings Break',
        second_innings_start: 'Chase Begins',
        second_innings_end: 'Second Innings End',
        end_of_over: 'Over Summary',
        new_batter: 'New Batter',
        phase_change: 'Phase Change',
        milestone: 'Milestone',
    };
    const icon = getNarrativeIcon(narrType);

    item.innerHTML = `
        <div class="feed-ball-col">
            <div class="feed-ball-indicator-wrap">
                <div class="feed-narrative-icon narrative-icon-${narrType}">${icon}</div>
                <button class="feed-play-overlay" title="Play from here"><svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><path d="M8 5v14l11-7z"/></svg></button>
            </div>
        </div>
        <div class="feed-content-col">
            <div class="feed-meta">
                <span class="feed-badge badge-narrative">${labels[narrType] || 'Narrative'}</span>
            </div>
            <div class="feed-text">${c.text || ''}</div>
        </div>
    `;

    commentaryFeed.insertBefore(item, commentaryFeed.firstChild);
}

function clearCommentary() {
    commentaryFeed.innerHTML = '';
    updateChaseDisplay(1);
    currentOverBalls = [];
    currentOverRuns = 0;
    totalBoundaries = { fours: 0, sixes: 0 };
    totalExtras = 0;
    totalDotBalls = 0;
    totalBalls = 0;
    recentOversData = [];
    playbackIndex = -1;
    clearPlaybackTimer();
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    isPlaying = false;
    updateStats();
    renderRecentOvers();
    els.overRuns.textContent = '0';
    renderBallIndicator();
}


// === Audio ===
function stopAllAudioPlayback() {
    if (currentAudio) {
        try {
            currentAudio.pause();
            currentAudio.currentTime = 0;
        } catch (_) {}
        currentAudio = null;
    }
}

function stopAudio() {
    isPlaying = false;
    clearPlaybackTimer();
    playbackIndex = -1;
    stopAllAudioPlayback();
    highlightPlayingItem(-1);
    playBtn.textContent = 'Play';
    updateTimelinePlayBtn();
    updateTimelineCursor();
}


// === Timeline: Build from Commentaries ===

/**
 * Build timelineItems from allCommentaries.
 * Excludes end_of_over events (same as the old timeline API).
 * Enriches ball_info with team names from inningsSummary.
 */
function buildTimeline() {
    timelineItems = [];
    timelineIdxToCommIdx = {};
    commIdxToTimelineIdx = {};
    ballIdToTimelineIdx = {};

    allCommentaries.forEach((c, ci) => {
        // Exclude end_of_over from timeline (shown in feed but not on progress bar)
        if (c.event_type === 'end_of_over') return;

        const tlIdx = timelineItems.length;
        const type = c.event_type === 'delivery' ? 'ball' : 'event';

        // Enrich ball_info with team names from innings_summary
        if (c.ball_info && c.ball_info.innings != null) {
            const innMeta = inningsSummary.find(s => s.innings_number === c.ball_info.innings) || {};
            c.ball_info.batting_team = innMeta.batting_team || '';
            c.ball_info.bowling_team = innMeta.bowling_team || '';
        }

        timelineItems.push({
            id: c.id,
            seq: c.seq,
            type,
            event_type: c.event_type,
            ball_id: c.ball_id,
            ball_info: c.ball_info,
            data: c.data,
            is_generated: c.is_generated,
        });

        timelineIdxToCommIdx[tlIdx] = ci;
        commIdxToTimelineIdx[ci] = tlIdx;

        if (c.ball_id != null && ballIdToTimelineIdx[c.ball_id] === undefined) {
            ballIdToTimelineIdx[c.ball_id] = tlIdx;
        }
    });

    updateTimelineFilled();
    updateTimelineCursor();
}


// === Timeline: Render ===

function renderTimeline() {
    if (!timelineItems.length) return;

    tlBar.classList.remove('hidden');
    renderInningsLabels();
    renderTimelineBadges();
    renderInningsSeparators();
    tlCursor.classList.add('hidden');
}

function renderInningsLabels() {
    const total = timelineItems.length;
    if (!total || !inningsSummary.length) return;

    const innCounts = {};
    for (const item of timelineItems) {
        const innNum = (item.ball_info && item.ball_info.innings) || (item.data && item.data.innings) || 1;
        innCounts[innNum] = (innCounts[innNum] || 0) + 1;
    }

    tlInningsLabels.innerHTML = inningsSummary.map(inn => {
        const count = innCounts[inn.innings_number] || 0;
        const pct = (count / total) * 100;
        return `<div class="timeline-innings-label" style="width:${pct}%">${inn.batting_team || 'Inn ' + inn.innings_number}</div>`;
    }).join('');
}

function getTimelineBadgeLabel(item) {
    if (item.type === 'event') {
        const et = item.event_type || '';
        if (et === 'first_innings_start') {
            return { badge: 'badge-event badge-innings-start', text: '', title: 'First Innings' };
        }
        if (et === 'second_innings_start') {
            return { badge: 'badge-event badge-innings-start', text: '', title: 'Chase Begins' };
        }
        if (et === 'first_innings_end') {
            return { badge: 'badge-event badge-innings-end', text: '', title: 'Innings Break' };
        }
        return { badge: 'badge-event', text: '', title: et.replace(/_/g, ' ') };
    }

    const b = item.ball_info || item.data || {};
    if (b.is_wicket) return { badge: 'badge-wicket', text: 'W', title: 'Wicket' };
    if (b.is_six) return { badge: 'badge-six', text: '6', title: 'Six' };
    const et = (b.extras_type || '').toLowerCase();
    const runs = (b.runs || 0) + (b.extras || 0);
    if (et === 'wide' || et === 'wides') return { badge: 'badge-wide', text: String(runs), title: 'Wide' };
    if (et === 'noball' || et === 'no_ball') return { badge: 'badge-noball', text: String(runs), title: 'No ball' };
    return { badge: 'badge-dot', text: String(runs), title: `${runs} run${runs !== 1 ? 's' : ''}` };
}

function renderTimelineBadges() {
    const total = timelineItems.length;
    if (!total) return;

    let html = '';
    timelineItems.forEach((item, i) => {
        const pct = (i / (total - 1)) * 100;
        const { badge, text, title } = getTimelineBadgeLabel(item);
        const commentaryId = item.id;
        const dataCommentaryId = commentaryId != null ? ` data-commentary-id="${commentaryId}"` : '';
        html += `<div class="timeline-badge ${badge}" data-timeline-idx="${i}"${dataCommentaryId} style="left:${pct}%" title="${title}">${text}</div>`;
    });
    tlBadges.innerHTML = html;
}

function renderInningsSeparators() {
    const total = timelineItems.length;
    if (!total) {
        tlInningsSep.innerHTML = '';
        return;
    }

    let html = '';
    timelineItems.forEach((item, i) => {
        if (item.event_type === 'first_innings_end' || item.event_type === 'second_innings_start') {
            const pct = (i / (total - 1)) * 100;
            html += `<div class="timeline-innings-sep-dot" style="left:${pct}%" title="Innings Break"></div>`;
        }
    });
    tlInningsSep.innerHTML = html;
}


// === Timeline: Filled Region ===

function updateTimelineFilled() {
    const total = timelineItems.length;
    if (!total) { tlFilled.style.width = '0%'; return; }

    let maxIdx = -1;
    timelineItems.forEach((item, idx) => {
        if (item.is_generated && idx > maxIdx) maxIdx = idx;
    });

    if (maxIdx < 0) {
        tlFilled.style.width = '0%';
    } else {
        const pct = ((maxIdx + 1) / total) * 100;
        tlFilled.style.width = `${Math.min(pct, 100)}%`;
    }
}


// === Timeline: Cursor ===

function getCursorBadgeInfo(item) {
    if (!item) return { cls: '', text: '' };
    if (item.type === 'event') return { cls: 'cursor-badge-event', text: '' };

    const b = item.ball_info || item.data || {};
    if (b.is_wicket) return { cls: 'cursor-badge-wicket', text: 'W' };
    if (b.is_six) return { cls: 'cursor-badge-six', text: '6' };
    if (b.is_boundary) return { cls: 'cursor-badge-four', text: '4' };
    const et = (b.extras_type || '').toLowerCase();
    const runs = (b.runs || 0) + (b.extras || 0);
    if (et === 'wide' || et === 'wides' || et === 'noball' || et === 'no_ball') {
        return { cls: 'cursor-badge-extra', text: String(runs) };
    }
    if (runs === 0) return { cls: 'cursor-badge-dot', text: '0' };
    return { cls: 'cursor-badge-runs', text: String(runs) };
}

function updateCursorBadge(item) {
    const { cls, text } = getCursorBadgeInfo(item);
    tlCursorBadge.className = 'timeline-cursor-badge ' + cls;
    tlCursorBadge.textContent = text;
}

function getTimelinePositionFromPlayback() {
    if (playbackIndex < 0) return -1;
    if (commIdxToTimelineIdx[playbackIndex] !== undefined) {
        return commIdxToTimelineIdx[playbackIndex];
    }
    // Walk backward to find closest mapped commentary
    for (let i = playbackIndex; i >= 0; i--) {
        if (commIdxToTimelineIdx[i] !== undefined) {
            return commIdxToTimelineIdx[i];
        }
    }
    return -1;
}

function updateTimelineCursor() {
    const total = timelineItems.length;
    if (!total) return;

    const pos = getTimelinePositionFromPlayback();

    tlBadges.querySelectorAll('.timeline-badge.active').forEach(el => el.classList.remove('active'));

    if (pos < 0) {
        tlCursor.classList.add('hidden');
        return;
    }

    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${(pos / (total - 1)) * 100}%`;

    const activeBadge = tlBadges.querySelector(`.timeline-badge[data-timeline-idx="${pos}"]`);
    if (activeBadge) activeBadge.classList.add('active');

    updateCursorBadge(timelineItems[pos]);
}

function updateTimelinePlayBtn() {
    if (isPlaying) {
        tlPlayIcon.classList.add('hidden');
        tlPauseIcon.classList.remove('hidden');
    } else {
        tlPlayIcon.classList.remove('hidden');
        tlPauseIcon.classList.add('hidden');
    }
}


// === Timeline: Scrubbing (Click & Drag) ===

function initTimelineScrubbing() {
    tlTrack.addEventListener('mousedown', onTrackMouseDown);
    tlTrack.addEventListener('mousemove', onTrackMouseMove);
    tlTrack.addEventListener('mouseleave', onTrackMouseLeave);

    tlTrack.addEventListener('touchstart', onTrackTouchStart, { passive: false });

    document.addEventListener('mousemove', onDocMouseMove);
    document.addEventListener('mouseup', onDocMouseUp);
    document.addEventListener('touchmove', onDocTouchMove, { passive: false });
    document.addEventListener('touchend', onDocTouchEnd);
}

function getTimelineClickTarget(e) {
    const target = e.target;
    const badge = target && target.closest ? target.closest('.timeline-badge') : null;
    if (badge) {
        const idxStr = badge.getAttribute('data-timeline-idx');
        if (idxStr !== null && idxStr !== '') {
            const idx = parseInt(idxStr, 10);
            if (!isNaN(idx) && idx >= 0 && idx < timelineItems.length) {
                return { idx };
            }
        }
    }

    const rect = tlTrack.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const ratio = x / rect.width;
    const idx = Math.round(ratio * (timelineItems.length - 1));
    const safeIdx = Math.max(0, Math.min(idx, timelineItems.length - 1));
    return { idx: safeIdx };
}

function onTrackMouseDown(e) {
    if (!timelineItems.length) return;
    e.preventDefault();
    isDragging = true;
    tlTrack.classList.add('dragging');
    scrubToPosition(e);
}

function onTrackTouchStart(e) {
    if (!timelineItems.length) return;
    e.preventDefault();
    isDragging = true;
    tlTrack.classList.add('dragging');
    scrubToPosition(e);
}

function onDocMouseMove(e) {
    if (!isDragging) return;
    scrubToPosition(e);
}

function onDocTouchMove(e) {
    if (!isDragging) return;
    e.preventDefault();
    scrubToPosition(e);
}

function onDocMouseUp() {
    if (isDragging) {
        isDragging = false;
        tlTrack.classList.remove('dragging');
        if (lastScrubHadCommentary && lastScrubCommentaryIdx != null) {
            playFrom(lastScrubCommentaryIdx);
        }
    }
}

function onDocTouchEnd() {
    if (isDragging) {
        isDragging = false;
        tlTrack.classList.remove('dragging');
        if (lastScrubHadCommentary && lastScrubCommentaryIdx != null) {
            playFrom(lastScrubCommentaryIdx);
        }
    }
}

function scrubToPosition(e, shouldPlay = false) {
    const { idx } = getTimelineClickTarget(e);
    const item = timelineItems[idx];
    if (!item) return;

    // Move cursor visually
    const total = timelineItems.length;
    const pct = (total > 1 ? (idx / (total - 1)) * 100 : 0);
    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${pct}%`;
    updateCursorBadge(item);

    // Update scoreboard for ball items
    if (item.type === 'ball' && item.ball_info) {
        applyScoreboardSnapshot(item.ball_info);
        rebuildCumulativeStatsForTimelineIdx(idx);
    } else {
        // For events, find nearest preceding ball
        for (let i = idx - 1; i >= 0; i--) {
            if (timelineItems[i].type === 'ball' && timelineItems[i].ball_info) {
                applyScoreboardSnapshot(timelineItems[i].ball_info);
                rebuildCumulativeStatsForTimelineIdx(i);
                break;
            }
        }
    }

    // Direct mapping: timeline item -> commentary index
    const commIdx = timelineIdxToCommIdx[idx];

    if (commIdx !== undefined) {
        lastScrubCommentaryIdx = commIdx;
        lastScrubHadCommentary = true;
        if (shouldPlay) {
            playFrom(commIdx);
        }
        // Exit live mode if scrubbing backward
        if (matchStatus === 'generating') {
            const latestTimelineIdx = getLatestAvailableTimelineIdx();
            if (idx < latestTimelineIdx) {
                isLiveMode = false;
                updateLiveControls();
            }
        }
    } else {
        lastScrubCommentaryIdx = null;
        lastScrubHadCommentary = false;
        pausePlayback();
        playbackIndex = -1;
        highlightPlayingItem(-1);
        updateTimelinePlayBtn();
    }
}


// === Timeline: Hover Tooltip ===

function onTrackMouseMove(e) {
    if (isDragging) return;
    if (!timelineItems.length) return;

    const { idx } = getTimelineClickTarget(e);
    const item = timelineItems[idx];
    if (!item) {
        tlTooltip.classList.add('hidden');
        clearTimelineHover();
        return;
    }

    const total = timelineItems.length;
    const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${pct}%`;
    updateCursorBadge(item);

    tlBadges.querySelectorAll('.timeline-badge.hover').forEach(el => el.classList.remove('hover'));
    const hoverBadge = tlBadges.querySelector(`.timeline-badge[data-timeline-idx="${idx}"]`);
    if (hoverBadge) hoverBadge.classList.add('hover');

    // Position tooltip
    const rect = tlTrack.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const tooltipWidth = 180;
    let tooltipLeft = x;
    if (tooltipLeft < tooltipWidth / 2) tooltipLeft = tooltipWidth / 2;
    if (tooltipLeft > rect.width - tooltipWidth / 2) tooltipLeft = rect.width - tooltipWidth / 2;
    tlTooltip.style.left = `${tooltipLeft}px`;

    if (item.type === 'ball' && item.ball_info) {
        const ball = item.ball_info;
        const overStr = `Over ${ball.over}.${ball.ball}`;
        let badgeHtml = '';
        if (ball.is_wicket) badgeHtml = '<span class="tooltip-badge tb-wicket">W</span>';
        else if (ball.is_six) badgeHtml = '<span class="tooltip-badge tb-six">SIX</span>';
        else if (ball.is_boundary) badgeHtml = '<span class="tooltip-badge tb-four">FOUR</span>';
        tlTooltipOver.innerHTML = `${overStr} ${badgeHtml}`;

        tlTooltipPlayers.textContent = `${ball.batter} vs ${ball.bowler}`;

        const hasGenerated = item.is_generated;
        if (ball.is_wicket) {
            const wicketType = (item.data && item.data.wicket_type) ? item.data.wicket_type.toUpperCase() : 'OUT';
            tlTooltipEvent.textContent = wicketType;
            tlTooltipEvent.className = 'timeline-tooltip-event event-wicket';
        } else if (ball.is_six) {
            tlTooltipEvent.textContent = '6 runs';
            tlTooltipEvent.className = 'timeline-tooltip-event event-six';
        } else if (ball.is_boundary) {
            tlTooltipEvent.textContent = '4 runs';
            tlTooltipEvent.className = 'timeline-tooltip-event event-four';
        } else if (ball.runs === 0 && ball.extras === 0) {
            tlTooltipEvent.textContent = 'Dot ball';
            tlTooltipEvent.className = 'timeline-tooltip-event event-dot';
        } else {
            const totalRuns = (ball.runs || 0) + (ball.extras || 0);
            let label = `${totalRuns} run${totalRuns !== 1 ? 's' : ''}`;
            if (ball.extras > 0 && ball.extras_type) label += ` (${ball.extras_type})`;
            tlTooltipEvent.textContent = label;
            tlTooltipEvent.className = 'timeline-tooltip-event event-runs';
        }

        if (!hasGenerated) {
            tlTooltipEvent.textContent += ' — No commentary';
            tlTooltipEvent.className = 'timeline-tooltip-event event-unavail';
        }
    } else {
        const eventLabel = (item.event_type || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        tlTooltipOver.innerHTML = eventLabel;
        tlTooltipPlayers.textContent = '';
        tlTooltipEvent.textContent = '';
        tlTooltipEvent.className = 'timeline-tooltip-event event-narrative';
    }

    tlTooltip.classList.remove('hidden');
}

function onTrackMouseLeave() {
    if (!isDragging) {
        tlTooltip.classList.add('hidden');
        clearTimelineHover();
    }
}

function clearTimelineHover() {
    tlBadges.querySelectorAll('.timeline-badge.hover').forEach(el => el.classList.remove('hover'));
    updateTimelineCursor();
}


// === Timeline: Live Mode ===

function getLatestAvailableTimelineIdx() {
    let maxIdx = -1;
    timelineItems.forEach((item, idx) => {
        if (item.is_generated && idx > maxIdx) maxIdx = idx;
    });
    return maxIdx;
}

function updateLiveControls() {
    if (matchStatus === 'generating') {
        if (isLiveMode) {
            tlLiveBadge.classList.remove('hidden');
            tlGoLive.classList.add('hidden');
        } else {
            tlLiveBadge.classList.add('hidden');
            tlGoLive.classList.remove('hidden');
        }
    } else {
        tlLiveBadge.classList.add('hidden');
        tlGoLive.classList.add('hidden');
    }
}

function goLive() {
    isLiveMode = true;
    updateLiveControls();

    // Jump to the latest generated commentary
    const latestTlIdx = getLatestAvailableTimelineIdx();
    if (latestTlIdx >= 0) {
        const commIdx = timelineIdxToCommIdx[latestTlIdx];
        if (commIdx !== undefined) {
            playbackIndex = commIdx + 1;
            if (!isPlaying) {
                isPlaying = true;
                playBtn.textContent = 'Pause';
                updateTimelinePlayBtn();
            }
            updateTimelineCursor();
        }
    }
}


// === Timeline: Show / Hide ===

function showTimeline() {
    tlBar.classList.remove('hidden');
}

function hideTimeline() {
    tlBar.classList.add('hidden');
}

function resetTimeline() {
    inningsSummary = [];
    timelineItems = [];
    timelineIdxToCommIdx = {};
    commIdxToTimelineIdx = {};
    ballIdToTimelineIdx = {};
    isLiveMode = false;
    isDragging = false;
    tlBadges.innerHTML = '';
    tlInningsSep.innerHTML = '';
    tlInningsLabels.innerHTML = '';
    tlFilled.style.width = '0%';
    tlCursor.classList.add('hidden');
    tlTooltip.classList.add('hidden');
    hideTimeline();
}


// === Match End ===
function showMatchEnd(d) {
    const overlay = document.createElement('div');
    overlay.className = 'match-end-overlay';
    const winner = d.result === 'won' ? els.battingTeam.textContent : els.bowlingTeam.textContent;
    overlay.innerHTML = `
        <div class="match-end-card">
            <h2>${winner} Win!</h2>
            <p>${d.final_score} (${d.overs} overs)</p>
            <button onclick="this.closest('.match-end-overlay').remove()"
                class="mt-6 px-6 py-2 text-sm font-semibold bg-white/10 hover:bg-white/15 text-neutral-300 rounded-lg transition-colors">Close</button>
        </div>
    `;
    document.body.appendChild(overlay);
}


// === Match Info Strip ===

/**
 * Populate the match info strip (series, match descriptor, venue) from match data.
 * Derives the match descriptor (e.g. "Final", "27th Match, Group A") from
 * the title by stripping out the team names and series name.
 */
function applyMatchInfoStrip(match) {
    const info = match.match_info || {};
    const series = info.series || '';
    const venue = match.venue || info.venue || '';
    const title = info.title || match.title || '';

    // Derive match descriptor by removing "Team1 vs Team2, " prefix and ", Series" suffix
    let desc = '';
    if (title) {
        let remainder = title;
        const vsIdx = remainder.search(/\s+vs?\s+/i);
        if (vsIdx >= 0) {
            const afterVs = remainder.substring(vsIdx).search(/,\s*/);
            if (afterVs >= 0) {
                remainder = remainder.substring(vsIdx + afterVs).replace(/^,\s*/, '');
            }
        }
        if (series && remainder.endsWith(series)) {
            remainder = remainder.substring(0, remainder.length - series.length).replace(/,\s*$/, '');
        }
        if (remainder && remainder !== title) {
            desc = remainder.trim();
        }
    }

    matchSeriesEl.textContent = series;
    matchDescEl.textContent = desc;
    matchVenueEl.textContent = venue;

    // Show/hide separators based on content
    const hasDesc = !!desc;
    const hasVenue = !!venue;
    matchDescSep.classList.toggle('hidden', !series || !hasDesc);
    matchDescEl.classList.toggle('hidden', !hasDesc);
    matchVenueSep.classList.toggle('hidden', !hasDesc && !series || !hasVenue);
    matchVenueEl.classList.toggle('hidden', !hasVenue);

    matchInfoStrip.classList.toggle('hidden', !series && !hasDesc && !hasVenue);
}


// === Languages ===

/**
 * Populate the language dropdown from the match's languages array.
 * match.languages is enriched by the API: [{code, name, native_name}].
 */
function applyMatchLanguages(matchLangs) {
    const sel = document.getElementById('languageSelect');
    sel.innerHTML = '';

    const langs = matchLangs && matchLangs.length ? matchLangs : [{ code: 'hi', name: 'Hindi', native_name: 'हिन्दी' }];

    langs.forEach(l => {
        const o = document.createElement('option');
        o.value = l.code;
        o.textContent = l.native_name || l.name || l.code;
        if (l.code === selectedLang) o.selected = true;
        sel.appendChild(o);
    });

    // If current selectedLang isn't in this match's languages, switch to first available
    const codes = langs.map(l => l.code);
    if (!codes.includes(selectedLang)) {
        selectedLang = codes[0];
        sel.value = selectedLang;
    }
}


// === Init ===
(() => {
    initTimelineScrubbing();

    // Event delegation: play from clicked feed item
    commentaryFeed.addEventListener('click', (e) => {
        const overlay = e.target.closest('.feed-play-overlay');
        if (!overlay) return;
        const feedItem = overlay.closest('.feed-item');
        if (!feedItem) return;
        const idx = parseInt(feedItem.getAttribute('data-idx'), 10);
        if (!isNaN(idx)) {
            e.stopPropagation();
            playFrom(idx);
        }
    });

    routeFromUrl();
    if (!getMatchIdFromUrl()) {
        showHome();
    }
})();
