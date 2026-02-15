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
let timelineBalls = [];             // Flat array: all balls across both innings (ordered)
let ballIdToTimelineIdx = {};       // ball_id -> index in timelineBalls
let ballIdToCommentaryIndices = {}; // ball_id -> [indices in allCommentaries]
let commentaryIdxToTimelineIdx = {};// playbackIndex -> timeline position
let isLiveMode = false;             // Following live edge
let matchStatus = 'ready';          // 'ready' | 'generating' | 'generated'
let isDragging = false;             // Dragging the timeline cursor

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
            playBtn.classList.add('hidden');
            isLiveMode = false;
            updateLiveControls();
            commentaryFeed.innerHTML = '<div class="feed-item feed-placeholder py-20 text-center"><p class="text-sm text-neutral-600">Commentary not generated yet</p></div>';
        }

        // Show timeline if we have data
        if (timelineBalls.length > 0 && match.status !== 'ready') {
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

        if (c.event_type === 'match_start') {
            const d = c.data;
            if (d.batting_team) els.battingTeam.textContent = d.batting_team;
            if (d.bowling_team) els.bowlingTeam.textContent = d.bowling_team;
            if (d.target) els.target.textContent = d.target;
        } else if (c.event_type === 'score_update') {
            updateScoreboard(c.data);
            addBallDot(c.data);
            addBallFeedItem(c);
        } else if (c.event_type === 'commentary') {
            const d = c.data || {};
            if (d.is_narrative) {
                addCommentary(c, allCommentaries.length - 1);
            } else {
                updateBallFeedCommentary(c, allCommentaries.length - 1);
            }
        } else if (c.event_type === 'match_end') {
            // Show match end
        }
    }

    // Rebuild timeline maps if timeline is loaded
    if (timelineBalls.length > 0 && commentaries.length > 0) {
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
    // Stop current audio without preserving
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
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
        if (c.audio_url && c.event_type === 'commentary') break;
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
    els.totalRuns.textContent = d.total_runs;
    els.wickets.textContent = d.wickets;
    els.overs.textContent = d.overs;
    if (d.crr != null) els.crr.textContent = d.crr.toFixed(2);
    if (d.rrr != null) els.rrr.textContent = d.rrr.toFixed(2);
    els.runsNeeded.textContent = d.runs_needed;
    els.ballsRemaining.textContent = d.balls_remaining;
    els.matchPhase.textContent = d.match_phase;

    // Batter (on-strike) stats
    if (d.batter) {
        els.batterName.textContent = d.batter.name;
        els.batterRuns.textContent = d.batter.runs;
        els.batterBalls.textContent = d.batter.balls;
    }

    // Non-batter stats
    if (d.non_batter) {
        els.nonBatterName.textContent = d.non_batter.name;
        els.nonBatterRuns.textContent = d.non_batter.runs;
        els.nonBatterBalls.textContent = d.non_batter.balls;
    }

    // Bowler stats
    if (d.bowler_stats) {
        els.currentBowler.textContent = d.bowler_stats.name;
        els.bowlerRuns.textContent = d.bowler_stats.runs;
        els.bowlerWickets.textContent = d.bowler_stats.wickets;
    } else {
        els.currentBowler.textContent = d.bowler || '--';
    }

    if (d.crr != null && d.rrr != null) updateRunRateBars(d.crr, d.rrr);

    if (d.partnership_runs !== undefined) {
        els.partnershipRuns.textContent = d.partnership_runs;
        els.partnershipBalls.textContent = d.partnership_balls || 0;
    }

    totalBalls++;
    if (d.ball_runs === 0 && !d.is_wicket) totalDotBalls++;
    if (d.is_six) totalBoundaries.sixes++;
    else if (d.is_boundary) totalBoundaries.fours++;
    updateStats();

    if (d.is_six || d.is_boundary || d.is_wicket) {
        els.totalRuns.classList.add('flash');
        setTimeout(() => els.totalRuns.classList.remove('flash'), 600);
    }
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
        ballIndicator.innerHTML = '';
    }

    const dot = document.createElement('div');
    dot.className = 'ball-dot';
    let runs = 0;

    if (d.is_wicket) { dot.classList.add('wicket'); dot.textContent = 'W'; }
    else if (d.ball_runs === 0) { dot.classList.add('runs-0'); dot.textContent = '0'; }
    else if (d.is_six) { dot.classList.add('runs-6'); dot.textContent = '6'; runs = 6; }
    else if (d.is_boundary) { dot.classList.add('runs-4'); dot.textContent = '4'; runs = 4; }
    else { const cls = d.ball_runs <= 3 ? `runs-${d.ball_runs}` : 'runs-4'; dot.classList.add(cls); dot.textContent = d.ball_runs; runs = d.ball_runs; }

    currentOverRuns += runs;
    els.overRuns.textContent = currentOverRuns;
    ballIndicator.appendChild(dot);
    currentOverBalls.push(d.ball_runs);
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
    const bd = ballInfo.data || {};
    if (data.is_wicket || bd.is_wicket) return 'ball-W';
    if (data.is_six || bd.is_six) return 'ball-6';
    if (data.is_boundary || bd.is_boundary) return 'ball-4';
    const runs = data.ball_runs ?? (bd.runs || 0) + (bd.extras || 0);
    if (runs >= 1 && runs <= 3) return `ball-${runs}`;
    if (bd.extras_type === 'wide') return 'ball-wd';
    if (bd.extras_type === 'noball') return 'ball-nb';
    return 'ball-0';
}

function getBallIndicatorLabel(ballInfo, data) {
    if (!ballInfo) return '·';
    const bd = ballInfo.data || {};
    if (data.is_wicket || bd.is_wicket) return 'W';
    if (data.is_six || bd.is_six) return '6';
    if (data.is_boundary || bd.is_boundary) return '4';
    const runs = data.ball_runs ?? (bd.runs || 0) + (bd.extras || 0);
    if (bd.extras_type === 'wide') return 'wd';
    if (bd.extras_type === 'noball') return 'nb';
    return `${runs}`;
}

function getNarrativeIcon(type) {
    const icons = {
        first_innings_start: '🏏',
        first_innings_end: '📊',
        second_innings_start: '🎯',
        match_result: '🏆',
        end_of_over: '↻',
        new_batter: '🏃',
        phase_change: '⚡',
        milestone: '⭐',
    };
    return icons[type] || '✦';
}

/**
 * Add a feed item for a ball from its score_update event.
 * Shows ball info immediately; commentary text fills in later via updateBallFeedCommentary.
 */
function addBallFeedItem(c) {
    const placeholder = commentaryFeed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    const d = c.data || {};
    const ballInfo = c.ball_info;
    const ballId = c.ball_id;

    const item = document.createElement('div');
    item.className = 'feed-item feed-ball-pending';
    if (ballId) item.setAttribute('data-ball-id', ballId);

    const over = ballInfo ? `${ballInfo.over}.${ballInfo.ball}` : '';
    const batsmanBowler = ballInfo ? `${ballInfo.batter} vs ${ballInfo.bowler}` : '';
    const indicatorClass = getBallIndicatorClass(ballInfo, d);
    const indicatorLabel = getBallIndicatorLabel(ballInfo, d);

    // Short result label shown until commentary text arrives
    let resultText = '';
    if (d.is_wicket) resultText = 'WICKET';
    else if (d.is_six) resultText = '6 runs';
    else if (d.is_boundary) resultText = '4 runs';
    else {
        const runs = d.ball_runs ?? 0;
        resultText = runs === 0 ? 'Dot ball' : `${runs} run${runs !== 1 ? 's' : ''}`;
    }

    item.innerHTML = `
        <div class="feed-ball-col">
            <div class="feed-ball-over">${over}</div>
            <div class="feed-ball-indicator ${indicatorClass}">${indicatorLabel}</div>
        </div>
        <div class="feed-content-col">
            <div class="feed-meta">
                <span class="feed-over">${batsmanBowler}</span>
            </div>
            <div class="feed-text feed-text-pending">${resultText}</div>
        </div>
    `;

    commentaryFeed.insertBefore(item, commentaryFeed.firstChild);
    while (commentaryFeed.children.length > 100) {
        commentaryFeed.removeChild(commentaryFeed.lastChild);
    }
}

/**
 * Update an existing ball feed item with commentary text.
 * Finds the feed item by ball_id and replaces the pending text.
 */
function updateBallFeedCommentary(c, idx) {
    const d = c.data || {};
    const ballId = c.ball_id;

    // Find the feed item created by addBallFeedItem for this ball
    const existing = ballId ? commentaryFeed.querySelector(`[data-ball-id="${ballId}"]`) : null;

    if (existing) {
        existing.classList.remove('feed-ball-pending');
        existing.setAttribute('data-idx', idx);
        if (d.is_pivot) existing.classList.add('pivot');

        const textEl = existing.querySelector('.feed-text');
        if (textEl && c.text) {
            textEl.classList.remove('feed-text-pending');
            textEl.textContent = c.text;
        }
    } else {
        // Fallback: no matching score_update item (shouldn't happen normally)
        addCommentary(c, idx);
    }
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
    const ballInfo = c.ball_info;

    const narrType = d.narrative_type || 'general';
    item.className = `feed-item feed-narrative`;
    const labels = {
        first_innings_start: 'Match Start',
        first_innings_end: 'Innings Break',
        second_innings_start: 'Chase Begins',
        match_result: 'Result',
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
    while (commentaryFeed.children.length > 100) {
        commentaryFeed.removeChild(commentaryFeed.lastChild);
    }
}

function clearCommentary() {
    commentaryFeed.innerHTML = '';
    ballIndicator.innerHTML = '';
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
}


// === Audio ===
function stopAudio() {
    isPlaying = false;
    clearPlaybackTimer();
    playbackIndex = -1;
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
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

        // Flatten balls across innings in order
        timelineBalls = [];
        ballIdToTimelineIdx = {};
        for (const inn of (timelineData.innings || [])) {
            for (const b of (inn.deliveries || inn.balls || [])) {
                ballIdToTimelineIdx[b.ball_id] = timelineBalls.length;
                timelineBalls.push({
                    ...b,
                    innings: inn.innings_number,
                    batting_team: inn.batting_team,
                });
            }
        }

        renderTimeline();
        buildTimelineMaps();
    } catch (e) {
        console.error('Failed to fetch timeline:', e);
    }
}

function buildTimelineMaps() {
    ballIdToCommentaryIndices = {};
    commentaryIdxToTimelineIdx = {};

    allCommentaries.forEach((c, idx) => {
        const ballId = c.ball_id;
        if (ballId != null && ballIdToTimelineIdx[ballId] !== undefined) {
            if (!ballIdToCommentaryIndices[ballId]) ballIdToCommentaryIndices[ballId] = [];
            ballIdToCommentaryIndices[ballId].push(idx);
            commentaryIdxToTimelineIdx[idx] = ballIdToTimelineIdx[ballId];
        }
    });

    updateTimelineFilled();
    updateTimelineCursor();
}


// === Timeline: Render ===

function renderTimeline() {
    if (!timelineData || !timelineBalls.length) return;

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
    const innings = timelineData.innings || [];
    const total = timelineBalls.length;
    if (!total) return;

    tlInningsLabels.innerHTML = innings.map(inn => {
        const pct = ((inn.deliveries || inn.balls || []).length / total) * 100;
        return `<div class="timeline-innings-label" style="width:${pct}%">${inn.batting_team || 'Inn ' + inn.innings_number}</div>`;
    }).join('');
}

function renderTimelineBadges() {
    const total = timelineBalls.length;
    if (!total) return;

    // Only show wickets and sixes — fours are too frequent and clutter the bar
    let html = '';
    timelineBalls.forEach((b, i) => {
        const pct = (i / (total - 1)) * 100;
        if (b.is_wicket) {
            html += `<div class="timeline-badge badge-wicket" style="left:${pct}%" title="Wicket"></div>`;
        } else if (b.is_six) {
            html += `<div class="timeline-badge badge-six" style="left:${pct}%" title="Six"></div>`;
        }
    });
    tlBadges.innerHTML = html;
}

function renderInningsSeparators() {
    const innings = timelineData.innings || [];
    const total = timelineBalls.length;
    if (!total || innings.length <= 1) {
        tlInningsSep.innerHTML = '';
        return;
    }

    let cumulative = 0;
    let html = '';
    for (let i = 0; i < innings.length - 1; i++) {
        cumulative += (innings[i].deliveries || innings[i].balls || []).length;
        const pct = (cumulative / total) * 100;
        html += `<div class="timeline-innings-sep-dot" style="left:${pct}%" title="Innings Break"></div>`;
    }
    tlInningsSep.innerHTML = html;
}


// === Timeline: Filled Region ===

function updateTimelineFilled() {
    const total = timelineBalls.length;
    if (!total) { tlFilled.style.width = '0%'; return; }

    // Find the furthest ball that has commentary
    let maxIdx = -1;
    for (const ballId of Object.keys(ballIdToCommentaryIndices)) {
        const tlIdx = ballIdToTimelineIdx[ballId];
        if (tlIdx !== undefined && tlIdx > maxIdx) maxIdx = tlIdx;
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
    const total = timelineBalls.length;
    if (!total) return;

    const pos = getTimelinePositionFromPlayback();
    if (pos < 0) {
        tlCursor.classList.add('hidden');
        return;
    }

    tlCursor.classList.remove('hidden');
    const pct = (pos / (total - 1)) * 100;
    tlCursor.style.left = `${pct}%`;
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

function getTimelineIdxFromEvent(e) {
    const rect = tlTrack.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const ratio = x / rect.width;
    const idx = Math.round(ratio * (timelineBalls.length - 1));
    return Math.max(0, Math.min(idx, timelineBalls.length - 1));
}

function onTrackMouseDown(e) {
    if (!timelineBalls.length) return;
    e.preventDefault();
    isDragging = true;
    tlTrack.classList.add('dragging');
    scrubToPosition(e);
}

function onTrackTouchStart(e) {
    if (!timelineBalls.length) return;
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
    }
}

function onDocTouchEnd() {
    if (isDragging) {
        isDragging = false;
        tlTrack.classList.remove('dragging');
    }
}

function scrubToPosition(e) {
    const idx = getTimelineIdxFromEvent(e);
    const ball = timelineBalls[idx];
    if (!ball) return;

    // Move cursor visually immediately
    const total = timelineBalls.length;
    const pct = (idx / (total - 1)) * 100;
    tlCursor.classList.remove('hidden');
    tlCursor.style.left = `${pct}%`;

    // Find commentary for this ball
    const commentaryIndices = ballIdToCommentaryIndices[ball.ball_id];
    if (commentaryIndices && commentaryIndices.length) {
        // Find first commentary event (not score_update) for this ball
        let targetIdx = commentaryIndices[0];
        for (const ci of commentaryIndices) {
            if (allCommentaries[ci] && allCommentaries[ci].event_type === 'commentary') {
                targetIdx = ci;
                break;
            }
        }
        playFrom(targetIdx);

        // Exit live mode if scrubbing backward
        if (matchStatus === 'generating') {
            const latestTimelineIdx = getLatestAvailableTimelineIdx();
            if (idx < latestTimelineIdx) {
                isLiveMode = false;
                updateLiveControls();
            }
        }
    }
    // If no commentary, just update visual cursor (don't play)
}


// === Timeline: Hover Tooltip ===

function onTrackMouseMove(e) {
    if (isDragging) return; // Don't show tooltip while dragging
    if (!timelineBalls.length) return;

    const idx = getTimelineIdxFromEvent(e);
    const ball = timelineBalls[idx];
    if (!ball) { tlTooltip.classList.add('hidden'); return; }

    // Position tooltip
    const rect = tlTrack.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const tooltipWidth = 180; // approximate
    let tooltipLeft = x;
    // Clamp so tooltip doesn't overflow
    if (tooltipLeft < tooltipWidth / 2) tooltipLeft = tooltipWidth / 2;
    if (tooltipLeft > rect.width - tooltipWidth / 2) tooltipLeft = rect.width - tooltipWidth / 2;
    tlTooltip.style.left = `${tooltipLeft}px`;

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
    const hasCommentary = !!(ballIdToCommentaryIndices[ball.ball_id] && ballIdToCommentaryIndices[ball.ball_id].length);
    if (ball.is_wicket) {
        const wicketType = ball.wicket_type ? ball.wicket_type.toUpperCase() : 'OUT';
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

    tlTooltip.classList.remove('hidden');
}

function onTrackMouseLeave() {
    if (!isDragging) {
        tlTooltip.classList.add('hidden');
    }
}


// === Timeline: Live Mode ===

function getLatestAvailableTimelineIdx() {
    let maxIdx = -1;
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
        const ball = timelineBalls[latestTlIdx];
        const commentaryIndices = ballIdToCommentaryIndices[ball.ball_id];
        if (commentaryIndices && commentaryIndices.length) {
            // Jump to the last commentary for the latest ball
            const lastCommentaryIdx = commentaryIndices[commentaryIndices.length - 1];
            // Set playbackIndex to the next one so we wait for new
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
    timelineBalls = [];
    ballIdToTimelineIdx = {};
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
