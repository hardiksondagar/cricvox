// === State ===
let currentMatchId = null;
let currentView = 'home';     // 'home' | 'match'
let selectedLang = 'hi';
let lastSeq = 0;
let pollTimer = null;
let allCommentaries = [];     // cache for language switch re-render
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

// Timeline state
let timelineData = null;            // Raw timeline API response
let timelineItems = [];             // Flat array: all timeline items (balls + events) ordered by seq
let ballIdToTimelineIdx = {};       // ball_id -> index in timelineItems
let seqToTimelineIdx = {};          // seq -> index in timelineItems
let ballIdToCommentaryIndices = {}; // ball_id -> [indices in allCommentaries]
let commentaryIdxToTimelineIdx = {};// playbackIndex -> timeline position
let timelineIdxToCommentaryId = {}; // timeline idx -> commentary id (selected lang)
let commentaryIdToIdx = {};         // commentary id -> index in allCommentaries
let isLiveMode = false;             // Following live edge
let matchStatus = 'ready';          // 'ready' | 'generating' | 'generated'
let isDragging = false;             // Dragging the timeline cursor
let lastScrubCommentaryIdx = null; // Set during scrub; used to play audio on mouseup/touchend only
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
const tlTooltip = document.getElementById('timelineTooltip');
const tlTooltipOver = document.getElementById('tooltipOver');
const tlTooltipPlayers = document.getElementById('tooltipPlayers');
const tlTooltipEvent = document.getElementById('tooltipEvent');
const tlLiveBadge = document.getElementById('timelineLiveBadge');
const tlGoLive = document.getElementById('timelineGoLive');
const tlPlayIcon = document.getElementById('timelinePlayIcon');
const tlPauseIcon = document.getElementById('timelinePauseIcon');
const tlInningsLabels = document.getElementById('timelineInningsLabels');

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


// === URL Routing ===
function pushUrl(path) {
    if (window.location.pathname !== path) {
        history.pushState(null, '', path);
    }
}

function getMatchIdFromUrl() {
    const m = window.location.pathname.match(/^\/match\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
}

window.addEventListener('popstate', () => routeFromUrl());

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
        const resp = await fetch('/api/matches');
        const matches = await resp.json();
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

        let inningsSummary = '';
        if (info.innings_summary && info.innings_summary.length > 0) {
            inningsSummary = info.innings_summary.map(inn =>
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
                        ${inningsSummary ? `<div class="text-xs text-neutral-600 mt-1 font-mono">${inningsSummary}</div>` : ''}
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
        // Fetch commentaries and timeline in parallel
        const [data, _] = await Promise.all([
            fetch(`/api/matches/${matchId}/commentaries?after_seq=0&language=${selectedLang}`).then(r => r.json()),
            fetchTimeline(matchId),
        ]);
        const match = data.match;
        matchStatus = match.status;

        // Set match info in scoreboard
        if (match.match_info) {
            const info = match.match_info;
            if (info.batting_team) els.battingTeam.textContent = info.batting_team;
            if (info.bowling_team) els.bowlingTeam.textContent = info.bowling_team;
            if (info.target) els.target.textContent = info.target;
        }

        processCommentaries(data.commentaries);
        buildTimelineMaps();
        if (timelineItems.length > 0) renderTimeline();

        if (match.status === 'generating') {
            liveBadge.classList.remove('hidden');
            liveBadge.classList.add('flex');
            isLiveMode = true;
            updateLiveControls();
            startPolling(matchId);

            // Ready to play from start — wait for user to press play
            if (allCommentaries.length > 0) {
                playbackIndex = 0;
            }
            updateTimelinePlayBtn();
        } else if (match.status === 'generated') {
            liveBadge.classList.add('hidden');
            isLiveMode = false;
            updateLiveControls();

            // Ready to play from beginning — wait for user to press play
            if (allCommentaries.length > 0) {
                playbackIndex = 0;
            }
            updateTimelinePlayBtn();
        } else {
            liveBadge.classList.add('hidden');
            isLiveMode = false;
            updateLiveControls();
            // Only show placeholder when we have no commentaries (avoid overwriting items from processCommentaries)
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
        if (timelineItems.length > 0 && match.status !== 'ready') {
            showTimeline();
        }
    } catch (e) {
        console.error('Failed to open match:', e);
    }
}


// === Polling ===
function startPolling(matchId) {
    stopPolling();
    pollTimer = setInterval(async () => {
        try {
            const data = await fetch(`/api/matches/${matchId}/commentaries?after_seq=${lastSeq}&language=${selectedLang}`).then(r => r.json());
            processCommentaries(data.commentaries);
            buildTimelineMaps();
            if (timelineItems.length > 0) renderTimeline();

            // If playing and waiting for more, continue playback
            if (isPlaying && !currentAudio && data.commentaries.length > 0) {
                playCurrentCommentary();
            }

            if (data.match.status !== 'generating') {
                matchStatus = data.match.status;
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
            addCommentary(c, allCommentaries.length - 1);
        } else if (c.event_type === 'delivery') {
            const d = c.data || {};
            const bi = c.ball_info;
            if (d.is_narrative) {
                addCommentary(c, allCommentaries.length - 1);
            } else if (bi) {
                // Use delivery data from ball_info for scoreboard + ball dot
                try {
                    updateScoreboard(bi);
                    addBallDot(bi);
                } catch (e) {
                    console.warn('Scoreboard/ball-dot update failed:', e);
                }
                addBallFeedItem(c, bi);
            } else {
                // Fallback for commentary without ball_info
                addCommentary(c, allCommentaries.length - 1);
            }
        } else if (c.event_type === 'second_innings_end') {
            addCommentary(c, allCommentaries.length - 1);
        } else if ([
            'first_innings_end', 'second_innings_start',
            'end_of_over', 'phase_change', 'milestone', 'new_batter'
        ].includes(c.event_type)) {
            addCommentary(c, allCommentaries.length - 1);
        }
    }

    // Rebuild timeline maps if timeline is loaded
    if (timelineItems.length > 0 && commentaries.length > 0) {
        buildTimelineMaps();
    }
}


// === Language Switching ===
function switchLanguage(lang) {
    selectedLang = lang;
    lastSeq = 0;
    allCommentaries = [];
    clearCommentary();
    stopAudio();
    ballIdToCommentaryIndices = {};
    commentaryIdxToTimelineIdx = {};
    timelineIdxToCommentaryId = {};
    commentaryIdToIdx = {};
    if (currentMatchId) openMatch(currentMatchId);
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

    // If we have a paused audio, resume it
    if (currentAudio && currentAudio.paused) {
        currentAudio.play().catch(() => {
            currentAudio = null;
            playbackIndex++;
            playCurrentCommentary();
        });
        return;
    }

    // Start from beginning if cursor not set or past end
    if (playbackIndex < 0 || playbackIndex >= allCommentaries.length) {
        playbackIndex = 0;
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
    // Stop all previous audio before playing new
    stopAllAudioPlayback();
    playbackIndex = idx;
    isPlaying = true;
    playBtn.textContent = 'Pause';
    updateTimelinePlayBtn();
    playCurrentCommentary();
}

function playCurrentCommentary() {
    if (!isPlaying) return;

    // Advance to next commentary with audio
    while (playbackIndex < allCommentaries.length) {
        const c = allCommentaries[playbackIndex];
        if (c.audio_url) break;
        playbackIndex++;
    }

    if (playbackIndex >= allCommentaries.length) {
        // In live mode, keep playing state and wait for more
        if (pollTimer) {
            highlightPlayingItem(-1);
            updateTimelineCursor();
            return;
        }
        // Completed match — stop
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

    // Sync scoreboard to this commentary's ball (updates when each commentary plays)
    if (c.ball_id != null && ballIdToTimelineIdx[c.ball_id] !== undefined) {
        const tlIdx = ballIdToTimelineIdx[c.ball_id];
        const item = timelineItems[tlIdx];
        if (item && item.ball_info) {
            applyScoreboardSnapshot(item.ball_info);
            rebuildCumulativeStatsForTimelineIdx(tlIdx);
        }
    }

    try {
        const a = new Audio(c.audio_url);
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
    // Try data-idx first, then fall back to data-ball-id
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


// === Scoreboard ===
function updateScoreboard(d) {
    if (d.total_runs != null) els.totalRuns.textContent = d.total_runs;
    if (d.total_wickets != null) els.wickets.textContent = d.total_wickets;
    if (d.overs) els.overs.textContent = d.overs;
    if (d.crr != null) els.crr.textContent = d.crr.toFixed(2);
    if (d.rrr != null) els.rrr.textContent = d.rrr.toFixed(2);
    if (d.runs_needed != null) els.runsNeeded.textContent = d.runs_needed;
    if (d.balls_remaining != null) els.ballsRemaining.textContent = d.balls_remaining;
    if (d.match_phase) els.matchPhase.textContent = d.match_phase;

    // Player names from delivery data
    if (d.batter) els.batterName.textContent = d.batter;
    if (d.non_batter) els.nonBatterName.textContent = d.non_batter;
    if (d.bowler) els.currentBowler.textContent = d.bowler;

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
 * Apply score snapshot from a timeline ball (e.g. when scrubbing).
 * Updates display only; does not increment cumulative stats.
 */
function applyScoreboardSnapshot(ball) {
    if (!ball) return;
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
    if (ball.crr != null && ball.rrr != null) updateRunRateBars(ball.crr, ball.rrr);
}

/**
 * Get ball display config for timeline ball b.
 */
function getBallDisplayFromTimeline(b) {
    const ballRuns = (b.runs || 0) + (b.extras || 0);
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
 * Get ball display config for commentary delivery d.
 */
function getBallDisplayFromDelivery(d) {
    const ballRuns = d.ball_runs ?? ((d.runs || 0) + (d.extras || 0));
    const et = (d.extras_type || '').toLowerCase();
    if (d.is_wicket) return { text: 'W', className: 'wicket', runs: 0 };
    if (et === 'wide' || et === 'wides') return { text: ballRuns > 0 ? `${ballRuns}wd` : 'wd', className: 'extra', runs: ballRuns };
    if (et === 'noball' || et === 'no_ball') return { text: ballRuns > 0 ? `${ballRuns}nb` : 'nb', className: 'extra', runs: ballRuns };
    if (ballRuns === 0) return { text: '0', className: 'runs-0', runs: 0 };
    if (d.is_six) return { text: '6', className: 'runs-6', runs: 6 };
    if (d.is_boundary) return { text: '4', className: 'runs-4', runs: 4 };
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
        if (item.type !== 'ball' || !item.ball_info) continue; // Skip event items
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

        currentOverBalls.push(getBallDisplayFromTimeline(b));
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

    const display = getBallDisplayFromDelivery(d);
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
 * Uses ball_info for ball details and commentary text directly.
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
            <div class="feed-ball-indicator ${indicatorClass}">${indicatorLabel}</div>
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
 * Add a narrative commentary item to the feed (innings start, end of over, milestone, etc.)
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
            <div class="feed-narrative-icon narrative-icon-${narrType}">${icon}</div>
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
/** Stops all audio playback without changing playback state. Call before starting new audio. */
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


// === Timeline: Fetch & Build ===

async function fetchTimeline(matchId) {
    try {
        const resp = await fetch(`/api/matches/${matchId}/timeline`);
        timelineData = await resp.json();

        // Build flat items array from API response
        timelineItems = [];
        ballIdToTimelineIdx = {};
        seqToTimelineIdx = {};

        // Enrich items with innings team metadata from innings_summary
        const inningsSummary = timelineData.innings_summary || [];

        for (const item of (timelineData.items || [])) {
            const idx = timelineItems.length;
            seqToTimelineIdx[item.seq] = idx;
            if (item.ball_id != null) {
                ballIdToTimelineIdx[item.ball_id] = idx;
            }
            // Attach team names for ball items
            if (item.ball_info) {
                const innNum = item.ball_info.innings;
                const innMeta = inningsSummary.find(s => s.innings_number === innNum) || {};
                item.ball_info.batting_team = innMeta.batting_team || '';
                item.ball_info.bowling_team = innMeta.bowling_team || '';
            }
            timelineItems.push(item);
        }

        // Maps and render happen after processCommentaries (needs allCommentaries)
    } catch (e) {
        console.error('Failed to fetch timeline:', e);
    }
}

function buildTimelineMaps() {
    ballIdToCommentaryIndices = {};
    commentaryIdxToTimelineIdx = {};
    timelineIdxToCommentaryId = {};
    commentaryIdToIdx = {};

    allCommentaries.forEach((c, idx) => {
        if (c.id != null) commentaryIdToIdx[c.id] = idx;
        const ballId = c.ball_id;
        // Map by ball_id (for ball-linked commentaries)
        if (ballId != null && ballIdToTimelineIdx[ballId] !== undefined) {
            if (!ballIdToCommentaryIndices[ballId]) ballIdToCommentaryIndices[ballId] = [];
            ballIdToCommentaryIndices[ballId].push(idx);
            commentaryIdxToTimelineIdx[idx] = ballIdToTimelineIdx[ballId];
        }
        // Map non-ball commentaries (narratives) by seq proximity
        else if (ballId == null && c.seq != null && seqToTimelineIdx[c.seq] !== undefined) {
            commentaryIdxToTimelineIdx[idx] = seqToTimelineIdx[c.seq];
        }
    });

    // Build timeline idx -> commentary id (for selected lang) for ID-based badge clicks
    timelineItems.forEach((item, tlIdx) => {
        let indices = null;
        if (item.ball_id != null) indices = ballIdToCommentaryIndices[item.ball_id];
        if (!indices || !indices.length) {
            for (let ci = 0; ci < allCommentaries.length; ci++) {
                if (commentaryIdxToTimelineIdx[ci] === tlIdx) {
                    indices = [ci];
                    break;
                }
            }
        }
        if (indices && indices.length && allCommentaries[indices[0]].id != null) {
            timelineIdxToCommentaryId[tlIdx] = allCommentaries[indices[0]].id;
        }
    });

    updateTimelineFilled();
    updateTimelineCursor();
}


// === Timeline: Render ===

function renderTimeline() {
    if (!timelineData || !timelineItems.length) return;

    // Show timeline bar
    tlBar.classList.remove('hidden');

    // Render innings labels
    renderInningsLabels();

    // Render badges (key moments)
    renderTimelineBadges();

    // Render innings separator(s)
    renderInningsSeparators();

    // Initial cursor position
    tlCursor.classList.add('hidden');
}

function renderInningsLabels() {
    const inningsSummary = timelineData.innings_summary || [];
    const total = timelineItems.length;
    if (!total || !inningsSummary.length) return;

    // Count items per innings (by the ball_info.innings or data.innings)
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
    // Event items (non-ball)
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

    // Ball items — use ball_info
    const b = item.ball_info || item.data || {};
    if (b.is_wicket) return { badge: 'badge-wicket', text: 'W', title: 'Wicket' };
    if (b.is_six) return { badge: 'badge-six', text: '6', title: 'Six' };
    if (b.is_boundary) return { badge: 'badge-four', text: '4', title: 'Four' };
    const et = (b.extras_type || '').toLowerCase();
    const runs = (b.runs || 0) + (b.extras || 0);
    if (et === 'wide' || et === 'wides') return { badge: 'badge-wide', text: String(runs), title: 'Wide' };
    if (et === 'noball' || et === 'no_ball') return { badge: 'badge-noball', text: String(runs), title: 'No ball' };
    return { badge: 'badge-dot', text: String(runs), title: `${runs} run${runs !== 1 ? 's' : ''}` };
}

function renderTimelineBadges() {
    debugger;
    const total = timelineItems.length;
    if (!total) return;

    let html = '';
    timelineItems.forEach((item, i) => {
        const pct = (i / (total - 1)) * 100;
        const { badge, text, title } = getTimelineBadgeLabel(item);
        // const commentaryId = timelineIdxToCommentaryId[i];
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

    // Find innings break positions (first_innings_end or second_innings_start events)
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

    // Find the furthest item that is generated (has LLM content)
    let maxIdx = -1;
    timelineItems.forEach((item, idx) => {
        if (item.is_generated && idx > maxIdx) maxIdx = idx;
    });

    // Fallback: also check if any ball has commentary in allCommentaries
    if (maxIdx < 0) {
        for (const ballId of Object.keys(ballIdToCommentaryIndices)) {
            const tlIdx = ballIdToTimelineIdx[ballId];
            if (tlIdx !== undefined && tlIdx > maxIdx) maxIdx = tlIdx;
        }
    }

    if (maxIdx < 0) {
        tlFilled.style.width = '0%';
    } else {
        const pct = ((maxIdx + 1) / total) * 100;
        tlFilled.style.width = `${Math.min(pct, 100)}%`;
    }
}


// === Timeline: Cursor ===

function getTimelinePositionFromPlayback() {
    if (playbackIndex < 0) return -1;
    // Exact match
    if (commentaryIdxToTimelineIdx[playbackIndex] !== undefined) {
        return commentaryIdxToTimelineIdx[playbackIndex];
    }
    // Walk backward to find closest ball
    for (let i = playbackIndex; i >= 0; i--) {
        if (commentaryIdxToTimelineIdx[i] !== undefined) {
            return commentaryIdxToTimelineIdx[i];
        }
    }
    return -1;
}

function updateTimelineCursor() {
    const total = timelineItems.length;
    if (!total) return;

    const pos = getTimelinePositionFromPlayback();

    // Remove active state from all badges
    tlBadges.querySelectorAll('.timeline-badge.active').forEach(el => el.classList.remove('active'));

    if (pos < 0) {
        tlCursor.classList.add('hidden');
        return;
    }

    // Highlight badge at current position instead of showing default cursor
    const activeBadge = tlBadges.querySelector(`.timeline-badge[data-timeline-idx="${pos}"]`);
    if (activeBadge) {
        activeBadge.classList.add('active');
        tlCursor.classList.add('hidden');
    } else {
        tlCursor.classList.remove('hidden');
        tlCursor.style.left = `${(pos / (total - 1)) * 100}%`;
    }
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

    // Touch support
    tlTrack.addEventListener('touchstart', onTrackTouchStart, { passive: false });

    document.addEventListener('mousemove', onDocMouseMove);
    document.addEventListener('mouseup', onDocMouseUp);
    document.addEventListener('touchmove', onDocTouchMove, { passive: false });
    document.addEventListener('touchend', onDocTouchEnd);
}

function getTimelineClickTarget(e) {
    // If click/touch was on a badge, use its data for precise selection (ID-based)
    const target = e.target;
    const badge = target && target.closest ? target.closest('.timeline-badge') : null;
    if (badge) {
        const idxStr = badge.getAttribute('data-timeline-idx');
        const commentaryIdStr = badge.getAttribute('data-commentary-id');
        if (idxStr !== null && idxStr !== '') {
            const idx = parseInt(idxStr, 10);
            if (!isNaN(idx) && idx >= 0 && idx < timelineItems.length) {
                const commentaryId = commentaryIdStr ? parseInt(commentaryIdStr, 10) : null;
                return { idx, commentaryId: commentaryId && !isNaN(commentaryId) ? commentaryId : null };
            }
        }
    }

    // Fallback: position-based (for track clicks, drag)
    const rect = tlTrack.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const ratio = x / rect.width;
    const idx = Math.round(ratio * (timelineItems.length - 1));
    const safeIdx = Math.max(0, Math.min(idx, timelineItems.length - 1));
    return { idx: safeIdx, commentaryId: timelineIdxToCommentaryId[safeIdx] || null };
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
        // Play audio only when cursor is set (on release), not during drag
        if (lastScrubHadCommentary && lastScrubCommentaryIdx != null) {
            playFrom(lastScrubCommentaryIdx);
        }
    }
}

function onDocTouchEnd() {
    if (isDragging) {
        isDragging = false;
        tlTrack.classList.remove('dragging');
        // Play audio only when cursor is set (on release), not during drag
        if (lastScrubHadCommentary && lastScrubCommentaryIdx != null) {
            playFrom(lastScrubCommentaryIdx);
        }
    }
}

function scrubToPosition(e, shouldPlay = false) {
    const { idx, commentaryId } = getTimelineClickTarget(e);
    const item = timelineItems[idx];
    if (!item) return;

    // Move cursor visually immediately
    const total = timelineItems.length;
    const pct = (total > 1 ? (idx / (total - 1)) * 100 : 0);
    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${pct}%`;

    // Update scoreboard for ball items
    if (item.type === 'ball' && item.ball_info) {
        applyScoreboardSnapshot(item.ball_info);
        rebuildCumulativeStatsForTimelineIdx(idx);
    } else {
        // For events, find nearest preceding ball and apply its snapshot
        for (let i = idx - 1; i >= 0; i--) {
            if (timelineItems[i].type === 'ball' && timelineItems[i].ball_info) {
                applyScoreboardSnapshot(timelineItems[i].ball_info);
                rebuildCumulativeStatsForTimelineIdx(i);
                break;
            }
        }
    }

    // Find commentary for this item: prefer ID-based (exact match) when clicking a badge
    let playbackIdx = null;
    if (commentaryId != null && commentaryIdToIdx[commentaryId] !== undefined) {
        playbackIdx = commentaryIdToIdx[commentaryId];
    }
    if (playbackIdx == null) {
        let commentaryIndices = null;
        if (item.ball_id != null) commentaryIndices = ballIdToCommentaryIndices[item.ball_id];
        if (!commentaryIndices || !commentaryIndices.length) {
            for (let ci = 0; ci < allCommentaries.length; ci++) {
                if (commentaryIdxToTimelineIdx[ci] === idx) {
                    commentaryIndices = [ci];
                    break;
                }
            }
        }
        if (commentaryIndices && commentaryIndices.length) playbackIdx = commentaryIndices[0];
    }

    if (playbackIdx != null) {
        lastScrubCommentaryIdx = playbackIdx;
        lastScrubHadCommentary = true;
        if (shouldPlay) {
            playFrom(playbackIdx);
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
        // No commentary — stop playback, keep scoreboard at scrubbed position
        pausePlayback();
        playbackIndex = -1;
        highlightPlayingItem(-1);
        updateTimelinePlayBtn();
    }
}


// === Timeline: Hover Tooltip ===

function onTrackMouseMove(e) {
    if (isDragging) return; // Don't show tooltip while dragging
    if (!timelineItems.length) return;

    const { idx } = getTimelineClickTarget(e);
    const item = timelineItems[idx];
    if (!item) {
        tlTooltip.classList.add('hidden');
        clearTimelineHover();
        return;
    }

    // Show cursor and highlight badge at hover position (like when dragging)
    const total = timelineItems.length;
    const pct = total > 1 ? (idx / (total - 1)) * 100 : 0;
    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${pct}%`;

    tlBadges.querySelectorAll('.timeline-badge.hover').forEach(el => el.classList.remove('hover'));
    const hoverBadge = tlBadges.querySelector(`.timeline-badge[data-timeline-idx="${idx}"]`);
    if (hoverBadge) hoverBadge.classList.add('hover');

    // Position tooltip
    const rect = tlTrack.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const tooltipWidth = 180; // approximate
    let tooltipLeft = x;
    if (tooltipLeft < tooltipWidth / 2) tooltipLeft = tooltipWidth / 2;
    if (tooltipLeft > rect.width - tooltipWidth / 2) tooltipLeft = rect.width - tooltipWidth / 2;
    tlTooltip.style.left = `${tooltipLeft}px`;

    if (item.type === 'ball' && item.ball_info) {
        const ball = item.ball_info;
        // Over info
        const overStr = `Over ${ball.over}.${ball.ball}`;
        let badgeHtml = '';
        if (ball.is_wicket) badgeHtml = '<span class="tooltip-badge tb-wicket">W</span>';
        else if (ball.is_six) badgeHtml = '<span class="tooltip-badge tb-six">SIX</span>';
        else if (ball.is_boundary) badgeHtml = '<span class="tooltip-badge tb-four">FOUR</span>';
        tlTooltipOver.innerHTML = `${overStr} ${badgeHtml}`;

        // Players
        tlTooltipPlayers.textContent = `${ball.batter} vs ${ball.bowler}`;

        // Event / runs info
        const hasCommentary = !!(ballIdToCommentaryIndices[item.ball_id] && ballIdToCommentaryIndices[item.ball_id].length);
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

        if (!hasCommentary) {
            tlTooltipEvent.textContent += ' — No commentary';
            tlTooltipEvent.className = 'timeline-tooltip-event event-unavail';
        }
    } else {
        // Event item tooltip — show event label only, no commentary text for structural points
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
    updateTimelineCursor(); // Restore cursor/badge to playback position
}


// === Timeline: Live Mode ===

function getLatestAvailableTimelineIdx() {
    let maxIdx = -1;
    // Check items with is_generated flag
    timelineItems.forEach((item, idx) => {
        if (item.is_generated && idx > maxIdx) maxIdx = idx;
    });
    // Also check via commentary mapping
    for (const ballId of Object.keys(ballIdToCommentaryIndices)) {
        const tlIdx = ballIdToTimelineIdx[ballId];
        if (tlIdx !== undefined && tlIdx > maxIdx) maxIdx = tlIdx;
    }
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

    // Jump to the latest available commentary
    const latestTlIdx = getLatestAvailableTimelineIdx();
    if (latestTlIdx >= 0) {
        const item = timelineItems[latestTlIdx];
        if (!item) return;

        let commentaryIndices = null;
        if (item.ball_id != null) {
            commentaryIndices = ballIdToCommentaryIndices[item.ball_id];
        }
        if (commentaryIndices && commentaryIndices.length) {
            const lastCommentaryIdx = commentaryIndices[commentaryIndices.length - 1];
            playbackIndex = lastCommentaryIdx + 1;
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
    timelineData = null;
    timelineItems = [];
    ballIdToTimelineIdx = {};
    seqToTimelineIdx = {};
    ballIdToCommentaryIndices = {};
    commentaryIdxToTimelineIdx = {};
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


// === Languages ===
async function loadLanguages() {
    try {
        const resp = await fetch('/api/languages');
        const langs = await resp.json();
        const sel = document.getElementById('languageSelect');
        sel.innerHTML = '';
        langs.forEach(l => {
            const o = document.createElement('option');
            o.value = l.code;
            o.textContent = l.native_name || l.name;
            if (l.code === 'hi') o.selected = true;
            sel.appendChild(o);
        });
    } catch (e) { console.log('Languages:', e); }
}


// === Init ===
(async () => {
    await loadLanguages();
    initTimelineScrubbing();
    routeFromUrl();
    if (!getMatchIdFromUrl()) {
        showHome();
    }
})();
