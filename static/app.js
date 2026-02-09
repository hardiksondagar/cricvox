// === State ===
let eventSource = null;
let isRunning = false;
let currentOverBalls = [];
let audioQueue = [];
let isPlayingAudio = false;
let currentOverRuns = 0;
let totalBoundaries = { fours: 0, sixes: 0 };
let totalExtras = 0;
let totalDotBalls = 0;
let totalBalls = 0;
let recentOversData = [];

// === DOM refs ===
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const liveBadge = document.getElementById('liveBadge');
const audioToggle = document.getElementById('audioToggle');
const commentaryFeed = document.getElementById('commentaryFeed');
const ballIndicator = document.getElementById('ballIndicator');

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

// === Match Control ===
async function startMatch() {
    try {
        const startOver = parseInt(document.getElementById('startOver').value) || 1;
        const language = document.getElementById('languageSelect').value || 'en';
        const resp = await fetch(`/api/start?start_over=${startOver}&language=${language}`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'started' || data.status === 'already_running') {
            isRunning = true;
            updateUI();
            connectSSE();
        }
    } catch (e) {
        console.error('Failed to start match:', e);
    }
}

async function stopMatch() {
    try {
        await fetch('/api/stop', { method: 'POST' });
        isRunning = false;
        updateUI();
        if (eventSource) { eventSource.close(); eventSource = null; }
    } catch (e) {
        console.error('Failed to stop match:', e);
    }
}

function updateUI() {
    startBtn.disabled = isRunning;
    stopBtn.disabled = !isRunning;
    if (isRunning) {
        liveBadge.classList.remove('hidden');
        liveBadge.classList.add('flex');
    } else {
        liveBadge.classList.add('hidden');
        liveBadge.classList.remove('flex');
    }
}

// === SSE ===
function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource('/api/stream');

    eventSource.addEventListener('match_start', (e) => {
        const d = JSON.parse(e.data);
        els.battingTeam.textContent = d.batting_team;
        els.bowlingTeam.textContent = d.bowling_team;
        els.target.textContent = d.target;
        clearCommentary();
    });

    eventSource.addEventListener('score_update', (e) => {
        const d = JSON.parse(e.data);
        updateScoreboard(d);
        addBallDot(d);
    });

    eventSource.addEventListener('commentary', (e) => {
        const d = JSON.parse(e.data);
        addCommentary(d);
        if (audioToggle.checked && d.audio_base64) queueAudio(d.audio_base64);
    });

    eventSource.addEventListener('match_end', (e) => {
        const d = JSON.parse(e.data);
        showMatchEnd(d);
        isRunning = false;
        updateUI();
    });

    eventSource.addEventListener('ping', () => {});
    eventSource.onerror = () => console.warn('SSE error, retrying...');
}

// === Scoreboard ===
function updateScoreboard(d) {
    els.totalRuns.textContent = d.total_runs;
    els.wickets.textContent = d.wickets;
    els.overs.textContent = d.overs;
    els.crr.textContent = d.crr.toFixed(2);
    els.rrr.textContent = d.rrr.toFixed(2);
    els.runsNeeded.textContent = d.runs_needed;
    els.ballsRemaining.textContent = d.balls_remaining;
    els.currentBatsman.textContent = d.batsman;
    els.currentBowler.textContent = d.bowler;
    els.matchPhase.textContent = d.match_phase;

    updateRunRateBars(d.crr, d.rrr);

    if (d.partnership_runs !== undefined) {
        els.partnershipRuns.textContent = d.partnership_runs;
        els.partnershipBalls.textContent = d.partnership_balls || 0;
    }

    // Track stats
    totalBalls++;
    if (d.ball_runs === 0 && !d.is_wicket) totalDotBalls++;
    if (d.is_six) totalBoundaries.sixes++;
    else if (d.is_boundary) totalBoundaries.fours++;

    updateStats();

    // Flash on big events
    if (d.is_six || d.is_boundary || d.is_wicket) {
        const el = els.totalRuns;
        el.classList.add('flash');
        setTimeout(() => el.classList.remove('flash'), 600);
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
    const ball = parseInt(parts[1] || 0);

    if ((ball === 1 || ball === 0) && currentOverBalls.length >= 6) {
        const overNum = parseInt(parts[0]);
        if (overNum > 0) addRecentOver(overNum, currentOverRuns);
        currentOverBalls = [];
        currentOverRuns = 0;
        ballIndicator.innerHTML = '';
    }

    const dot = document.createElement('div');
    dot.className = 'ball-dot';
    let runs = 0;

    if (d.is_wicket) {
        dot.classList.add('wicket');
        dot.textContent = 'W';
    } else if (d.ball_runs === 0) {
        dot.classList.add('runs-0');
        dot.textContent = '0';
    } else if (d.is_six) {
        dot.classList.add('runs-6');
        dot.textContent = '6';
        runs = 6;
    } else if (d.is_boundary) {
        dot.classList.add('runs-4');
        dot.textContent = '4';
        runs = 4;
    } else {
        const cls = d.ball_runs <= 3 ? `runs-${d.ball_runs}` : 'runs-4';
        dot.classList.add(cls);
        dot.textContent = d.ball_runs;
        runs = d.ball_runs;
    }

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
function addCommentary(d) {
    const placeholder = commentaryFeed.querySelector('.feed-placeholder');
    if (placeholder) placeholder.remove();

    const item = document.createElement('div');

    if (d.is_narrative) {
        item.className = `feed-item feed-narrative narrative-${d.narrative_type || 'general'}`;
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
        item.innerHTML = `
            <div class="feed-meta">
                <span class="feed-badge badge-narrative">${labels[d.narrative_type] || 'Narrative'}</span>
            </div>
            <div class="feed-text">${d.text}</div>
        `;
    } else {
        item.className = `feed-item branch-${d.branch}`;
        if (d.is_pivot) item.classList.add('pivot');

        const labels = {
            routine: 'Routine',
            boundary_momentum: 'Boundary',
            wicket_drama: 'Wicket',
            pressure_builder: 'Pressure',
            over_transition: 'Over',
            extra_gift: 'Extra',
        };

        const over = d.over != null ? `${d.over}.${d.ball}` : '';
        const meta = d.batsman ? `${over} · ${d.batsman} vs ${d.bowler}` : '';
        const pivot = d.is_pivot ? ' PIVOT' : '';

        item.innerHTML = `
            <div class="feed-meta">
                <span class="feed-over">${meta}</span>
                <span class="feed-badge badge-${d.branch}">${labels[d.branch] || d.branch}${pivot}</span>
            </div>
            <div class="feed-text">${d.text}</div>
            ${d.equation_shift ? `<div class="feed-score">${d.equation_shift}</div>` : ''}
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
    updateStats();
    renderRecentOvers();
    els.overRuns.textContent = '0';
}

// === Audio ===
function queueAudio(b64) {
    audioQueue.push(b64);
    if (!isPlayingAudio) playNext();
}

function playNext() {
    if (!audioQueue.length) { isPlayingAudio = false; return; }
    isPlayingAudio = true;
    const b64 = audioQueue.shift();
    try {
        const a = new Audio(`data:audio/mpeg;base64,${b64}`);
        a.volume = 0.8;
        a.onended = playNext;
        a.onerror = () => { console.warn('Audio error'); playNext(); };
        a.play().catch(() => { console.warn('Autoplay blocked'); playNext(); });
    } catch (e) { console.warn('Audio:', e); playNext(); }
}

// === Match End ===
function showMatchEnd(d) {
    const overlay = document.createElement('div');
    overlay.className = 'match-end-overlay';

    const winner = d.result === 'won'
        ? els.battingTeam.textContent
        : els.bowlingTeam.textContent;

    overlay.innerHTML = `
        <div class="match-end-card">
            <h2>${winner} Win!</h2>
            <p>${d.final_score} (${d.overs} overs)</p>
            <button onclick="this.closest('.match-end-overlay').remove()"
                class="mt-6 px-6 py-2 text-sm font-semibold bg-white/10 hover:bg-white/15 text-neutral-300 rounded-lg transition-colors">
                Close
            </button>
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
            sel.appendChild(o);
        });
    } catch (e) { console.log('Languages:', e); }
}

// === Init ===
(async () => {
    await loadLanguages();

    try {
        const r = await fetch('/api/match-info');
        const d = await r.json();
        els.battingTeam.textContent = d.match_info.batting_team;
        els.bowlingTeam.textContent = d.match_info.bowling_team;
        els.target.textContent = d.match_info.target;
        els.runsNeeded.textContent = d.match_info.target;
    } catch (e) { console.log('Match info:', e); }

    try {
        const r = await fetch('/api/status');
        const d = await r.json();
        if (d.running) { isRunning = true; updateUI(); connectSSE(); }
    } catch (e) { console.log('Status:', e); }
})();
