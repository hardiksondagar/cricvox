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

// === DOM refs ===
const liveBadge = document.getElementById('liveBadge');
const commentaryFeed = document.getElementById('commentaryFeed');
const ballIndicator = document.getElementById('ballIndicator');
const homeView = document.getElementById('homeView');
const matchView = document.getElementById('matchView');
const matchListContainer = document.getElementById('matchList');
const navMatchControls = document.getElementById('navMatchControls');
const playBtn = document.getElementById('playBtn');

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
    currentBatsman: document.getElementById('currentBatsman'),
    currentBowler: document.getElementById('currentBowler'),
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
    lastSeq = 0;
    allCommentaries = [];
    isPlaying = false;
    playbackIndex = -1;
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }

    try {
        const data = await fetch(`/api/matches/${matchId}/commentaries?after_seq=0&language=${selectedLang}`).then(r => r.json());
        const match = data.match;

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
            playBtn.classList.remove('hidden');
            playBtn.textContent = 'Play';
            startPolling(matchId);
        } else if (match.status === 'generated') {
            liveBadge.classList.add('hidden');
            playBtn.classList.remove('hidden');
            playBtn.textContent = 'Play';
        } else {
            liveBadge.classList.add('hidden');
            playBtn.classList.add('hidden');
            commentaryFeed.innerHTML = '<div class="feed-item feed-placeholder py-20 text-center"><p class="text-sm text-neutral-600">Commentary not generated yet</p></div>';
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
                stopPolling();
                liveBadge.classList.add('hidden');
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
        } else if (c.event_type === 'commentary') {
            addCommentary(c, allCommentaries.length - 1);
        } else if (c.event_type === 'match_end') {
            // Show match end
        }
    }
}


// === Language Switching ===
function switchLanguage(lang) {
    selectedLang = lang;
    lastSeq = 0;
    allCommentaries = [];
    clearCommentary();
    stopAudio();
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
            return;
        }
        // Completed match — stop
        isPlaying = false;
        playBtn.textContent = 'Play';
        highlightPlayingItem(-1);
        return;
    }

    const c = allCommentaries[playbackIndex];
    highlightPlayingItem(playbackIndex);
    scrollToPlayingItem(playbackIndex);

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

function highlightPlayingItem(idx) {
    commentaryFeed.querySelectorAll('.feed-item-playing').forEach(el =>
        el.classList.remove('feed-item-playing')
    );
    if (idx >= 0) {
        const el = commentaryFeed.querySelector(`[data-idx="${idx}"]`);
        if (el) el.classList.add('feed-item-playing');
    }
}

function scrollToPlayingItem(idx) {
    const el = commentaryFeed.querySelector(`[data-idx="${idx}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
    els.currentBatsman.textContent = d.batsman;
    els.currentBowler.textContent = d.bowler;
    els.matchPhase.textContent = d.match_phase;

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
        new_batsman: '🏃',
        phase_change: '⚡',
        milestone: '⭐',
    };
    return icons[type] || '✦';
}

function addCommentary(c, idx) {
    const placeholder = commentaryFeed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    const d = c.data || {};
    const item = document.createElement('div');
    item.setAttribute('data-idx', idx);
    const ballInfo = c.ball_info;

    if (d.is_narrative) {
        const narrType = d.narrative_type || 'general';
        item.className = `feed-item feed-narrative`;
        const labels = {
            first_innings_start: 'Match Start',
            first_innings_end: 'Innings Break',
            second_innings_start: 'Chase Begins',
            match_result: 'Result',
            end_of_over: 'Over Summary',
            new_batsman: 'New Batsman',
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
                    ${c.audio_url ? `<button class="audio-play-btn" onclick="playFrom(${idx})" title="Play from here">&#9654;</button>` : ''}
                </div>
                <div class="feed-text">${c.text || ''}</div>
            </div>
        `;
    } else {
        item.className = `feed-item`;
        if (d.is_pivot) item.classList.add('pivot');

        const over = ballInfo ? `${ballInfo.over}.${ballInfo.ball}` : '';
        const batsmanBowler = ballInfo ? `${ballInfo.batsman} vs ${ballInfo.bowler}` : '';
        const indicatorClass = getBallIndicatorClass(ballInfo, d);
        const indicatorLabel = getBallIndicatorLabel(ballInfo, d);

        item.innerHTML = `
            <div class="feed-ball-col">
                <div class="feed-ball-over">${over}</div>
                <div class="feed-ball-indicator ${indicatorClass}">${indicatorLabel}</div>
            </div>
            <div class="feed-content-col">
                <div class="feed-meta">
                    <span class="feed-over">${batsmanBowler}</span>
                    <div class="flex items-center gap-2">
                        ${c.audio_url ? `<button class="audio-play-btn" onclick="playFrom(${idx})" title="Play from here">&#9654;</button>` : ''}
                    </div>
                </div>
                <div class="feed-text">${c.text || ''}</div>
            </div>
        `;
    }

    commentaryFeed.insertBefore(item, commentaryFeed.firstChild);
    while (commentaryFeed.children.length > 50) {
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
            o.textContent = l.code === 'en' ? l.name : `${l.native_name} (${l.name})`;
            if (l.code === 'hi') o.selected = true;
            sel.appendChild(o);
        });
    } catch (e) { console.log('Languages:', e); }
}


// === Init ===
(async () => {
    await loadLanguages();
    routeFromUrl();
    if (!getMatchIdFromUrl()) {
        showHome();
    }
})();
