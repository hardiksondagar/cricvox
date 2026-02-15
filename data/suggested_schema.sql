-- 1. TEAM & PLAYER METADATA
CREATE TABLE teams (
    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT NOT NULL,
    short_name TEXT, -- 'IND', 'MI', 'CSK'
    team_type TEXT -- 'International', 'Franchise'
);

CREATE TABLE players (
    player_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    short_name TEXT, -- 'V Kohli'
    dob DATE,
    batting_style TEXT, -- 'Right-hand bat', 'Left-hand bat'
    bowling_style TEXT, -- 'Right-arm fast', 'Leg-break'
    playing_role TEXT, -- 'Batsman', 'Bowler', 'All-rounder', 'Wicketkeeper'
    country TEXT
);
-- 3. MATCH CORE
CREATE TABLE matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date DATETIME,
    team1_id INTEGER,
    team2_id INTEGER,
    toss_winner_id INTEGER,
    toss_decision TEXT, -- 'bat' or 'field'
    result_type TEXT, -- 'Normal', 'Tie', 'No Result', 'Abandoned'
    winner_id INTEGER,
    win_margin INTEGER,
    win_margin_type TEXT, -- 'runs', 'wickets'
    player_of_match INTEGER,
    match_status TEXT, -- 'Scheduled', 'Live', 'Completed'
    FOREIGN KEY (tournament_id) REFERENCES tournaments(tournament_id),
    FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
    FOREIGN KEY (team1_id) REFERENCES teams(team_id),
    FOREIGN KEY (team2_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_of_match) REFERENCES players(player_id)
);


-- 5. SQUAD & SUBSTITUTIONS (Impact Player / Concussion)
CREATE TABLE match_players (
    match_id INTEGER,
    player_id INTEGER,
    team_id INTEGER,
    is_captain INTEGER DEFAULT 0,
    is_keeper INTEGER DEFAULT 0,
    player_status TEXT DEFAULT 'Playing XI', -- 'Playing XI', 'Substitute', 'Impact Player'
    PRIMARY KEY (match_id, player_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);


-- 6. INNINGS & SCORECARD SUMMARIES
CREATE TABLE innings (
    innings_id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER,
    team_id INTEGER,
    innings_number INTEGER, -- 1, 2 (Standard); 3, 4 (Super Over)
    total_runs INTEGER DEFAULT 0,
    total_wickets INTEGER DEFAULT 0,
    total_overs REAL,
    extras_total INTEGER DEFAULT 0,
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);

-- 7. BALL-BY-BALL DATA (Detailed)
CREATE TABLE deliveries (
    delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
    innings_id INTEGER,
    over_num INTEGER, -- 0-19
    ball_num INTEGER, -- 1-6
    striker_id INTEGER,
    non_striker_id INTEGER,
    bowler_id INTEGER,
    runs_bat INTEGER DEFAULT 0,
    runs_extra INTEGER DEFAULT 0,
    extra_type TEXT, -- 'wide', 'noball', 'bye', 'legbye', 'penalty'
    is_wicket INTEGER DEFAULT 0,
    wicket_type TEXT, -- 'caught', 'bowled', 'lbw', 'run out', 'stumped', 'retired out'
    player_out_id INTEGER,
    fielder_id INTEGER,
    ball_speed REAL,
    shot_zone TEXT, -- 'Mid-wicket', 'Point', etc.
    commentary TEXT,
    FOREIGN KEY (innings_id) REFERENCES innings(innings_id)
);

-- 8. PLAYER PERFORMANCE STATE (The "Scorecard")
CREATE TABLE batsman_stats (
    match_id INTEGER,
    player_id INTEGER,
    innings_id INTEGER,
    batting_order INTEGER,
    runs_scored INTEGER DEFAULT 0,
    balls_faced INTEGER DEFAULT 0,
    fours INTEGER DEFAULT 0,
    sixes INTEGER DEFAULT 0,
    strike_rate REAL,
    out_status TEXT, -- 'Not Out', 'Bowled', etc.
    dismissal_info TEXT, -- 'c Kohli b Bumrah'
    PRIMARY KEY (match_id, player_id, innings_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE TABLE bowler_stats (
    match_id INTEGER,
    player_id INTEGER,
    innings_id INTEGER,
    overs_bowled REAL,
    maidens INTEGER DEFAULT 0,
    runs_conceded INTEGER DEFAULT 0,
    wickets INTEGER DEFAULT 0,
    economy REAL,
    dot_balls INTEGER DEFAULT 0,
    PRIMARY KEY (match_id, player_id, innings_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

-- 9. FLOW & EXTRAS
CREATE TABLE partnerships (
    partnership_id INTEGER PRIMARY KEY AUTOINCREMENT,
    innings_id INTEGER,
    player1_id INTEGER,
    player2_id INTEGER,
    runs INTEGER,
    balls INTEGER,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (innings_id) REFERENCES innings(innings_id)
);


-- 10. INDEXES FOR FAST RETRIEVAL
CREATE INDEX idx_match_date ON matches(match_date);
CREATE INDEX idx_deliveries_lookup ON deliveries(innings_id, over_num, ball_num);
CREATE INDEX idx_player_batting ON batsman_stats(player_id, runs_scored);
CREATE INDEX idx_player_bowling ON bowler_stats(player_id, wickets);