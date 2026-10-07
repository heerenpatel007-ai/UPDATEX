const API_BASE  = window.location.origin;
const WS_URL    = `${API_BASE.replace(/^http/, 'ws')}/ws`;
const container = document.getElementById("data-container");
const statusEl  = document.getElementById("backend-status");

let ws = null;
let allItems = [];
const notifySound = new Audio('https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3');
notifySound.volume = 0.5;

// ── Time Formatting Helper ──────────────────────────────────────────────────
function formatTime12Hour(timeStr) {
    if (!timeStr || !timeStr.includes(':')) return timeStr;
    const parts = timeStr.split(':');
    let hours = parseInt(parts[0], 10);
    const mins = parts[1];
    const secs = parts[2] ? ':' + parts[2] : '';
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12;
    const padHours = hours < 10 ? '0' + hours : hours;
    return `${padHours}:${mins}${secs} ${ampm}`;
}

// ── Normalize NSE / BSE to a single display format ─────────
function normalize(item) {
    const src = item.source || '';
    
    if (src === 'BSE') {
        const t = item.NEWS_DT || '';
        let pdf = item.pdf_link || '';
        if (!pdf && item.ATTACHMENTNAME) {
            pdf = `https://www.bseindia.com/xml-data/corpfiling/AttachLive/${item.ATTACHMENTNAME}`;
        }
        let parsedTime = t.includes('T') ? t.split('T')[1].split('.')[0] : t;
        return {
            source:      'BSE',
            name:        item.SLONGNAME  || item.COMPANY_NAME || item.SLNAME || '-',
            symbol:      item.SCRIP_NAME || String(item.SCRIP_CD || '-'),
            headline:    item.HEADLINE   || item.SUBCATNAME || '-',
            time:        formatTime12Hour(parsedTime),
            category:    item.CATEGORYNAME || item.SUBCATNAME || '-',
            pdf_link:    pdf,
        };
    } 
    
    if (src === 'NSE') {
        const t = item.an_dt || '';
        let pdf = item.pdf_link || '';
        if (!pdf && item.attchmntFile) {
            pdf = `https://nsearchives.nseindia.com/corporate/${item.attchmntFile}`;
        }
        let parsedTime = t.includes(' ') ? t.split(' ')[1] : t;
        return {
            source:      'NSE',
            name:        item.sm_name  || item.companyName || '-',
            symbol:      item.symbol   || '-',
            headline:    item.desc     || item.headline || '-',
            time:        formatTime12Hour(parsedTime),
            category:    item.attchmntText || '-',
            pdf_link:    pdf,
        };
    }


    return { source: 'ALERT', name: '-', symbol: '-', headline: '-', time: '-', category: '-', pdf_link: '' };
}

let seenItems = new Set();

// ── Build card HTML ─────────────────────────────────────────────────────────
function createCard(item) {
    const tagColor = item.source === 'NSE' ? '#3d8aff' : (item.source === 'BSE' ? '#a855f7' : '#3fb950');
    const pdfBtn = item.pdf_link
        ? `<a href="${item.pdf_link}" target="_blank" class="pdf-link"><span>📄</span> View PDF</a>`
        : '';

    const isLong = (item.headline || '').length > 200;

    const html = `
        <div class="card slide-in" data-symbol="${item.symbol.toLowerCase()}" data-name="${item.name.toLowerCase()}">
            <div class="card-meta">
                <span class="source-tag" style="background: ${tagColor}; color: white;">${item.source}</span>
                <span style="font-size: 0.85rem; color: var(--text-secondary); font-weight: 600;">${item.time}</span>
            </div>
            <div class="card-title" style="color: var(--text-primary); font-weight: 700; margin-top: 2px;">${item.name}</div>
            <div class="card-symbol" style="color: var(--accent-blue); font-weight: 600; font-size: 0.75rem;">${item.symbol}</div>
            <div class="card-category">${item.category}</div>
            <div class="card-body" style="color: var(--text-secondary); margin-top: 4px;">${item.headline}</div>
            ${isLong ? `<button class="read-more-btn" style="display:block;">Read More</button>` : ''}
            <div style="margin-top:auto; padding-top:12px; display: flex; justify-content: space-between; align-items: center;">
                <button class="analyze-btn" data-symbol="${item.symbol}" style="background: rgba(37, 99, 235, 0.1); border: 1px solid rgba(37,99,235,0.2); color: var(--accent-blue); font-weight: 600; cursor: pointer; font-size: 0.75rem; padding: 6px 12px; border-radius: 6px; display: flex; align-items: center; gap: 5px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                    Check Script
                </button>
                ${pdfBtn}
            </div>
        </div>
    `;
    return html;
}

// ── Event Delegation for "Read More" ────────────────────────────────────────
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('read-more-btn')) {
        const btn = e.target;
        const body = btn.previousElementSibling;
        const isExpanded = body.classList.toggle('expanded');
        btn.innerText = isExpanded ? 'Show Less' : 'Read More';
    }
});

// ── Event Delegation for "Check Script" button ──────────────────────────────
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.analyze-btn');
    if (btn) {
        const symbol = btn.getAttribute('data-symbol');
        if (!symbol || symbol === '-' || symbol === 'NSE' || symbol === 'BSE') {
            alert('No valid script symbol found for this announcement.');
            return;
        }
        
        // 1. Switch to the Check Script tab
        const checkTab = document.querySelector('.main-tab[data-target="check-script"]');
        if (checkTab) checkTab.click();
        
        // 2. Set the search input value
        const searchInput = document.getElementById('script-search');
        if (searchInput) searchInput.value = symbol;
        
        // 3. Trigger the analysis fetch
        if (typeof fetchAnalysis === 'function') {
            fetchAnalysis(symbol);
        }
        
        // Scroll to top
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
});

// ── Prepend a single card to the correct column ─────────────────────────────
function prependCard(item) {
    // Deduplication Key: Source + Symbol + First 50 chars of headline
    const itemKey = `${item.source}-${item.symbol}-${(item.headline || '').substring(0, 50)}`;
    if (seenItems.has(itemKey)) return;
    seenItems.add(itemKey);

    const source = (item.source || '').toLowerCase();
    let colId = 'feed-nse';
    if (source === 'bse') colId = 'feed-bse';
    
    const container = document.getElementById(colId);
    if (!container) return;

    const wrap = document.createElement('div');
    wrap.innerHTML = createCard(item);
    const card = wrap.firstElementChild;
    
    container.insertBefore(card, container.firstChild);

    // Keep only latest 50 items per column for performance
    if (container.children.length > 50) {
        container.removeChild(container.lastChild);
    }
    
    // Play subtle notification sound for new items if not initial load
    if (ws && ws.readyState === WebSocket.OPEN) {
        notifySound.play().catch(e => console.log('Audio autoplay blocked:', e));
    }
}

// ── Initial data load ───────────────────────────────────────────────────────
async function loadFeed() {
    const cols = ['nse', 'bse'];
    cols.forEach(c => {
        const el = document.getElementById(`feed-${c}`);
        if(el) el.innerHTML = `<p style="font-size:0.7rem;color:var(--text-secondary);text-align:center;padding:20px;">Loading...</p>`;
    });

    try {
        // Cache-busting query param
        const res  = await fetch(`${API_BASE}/api/feed?nocache=${Date.now()}`);
        const data = await res.json();
        
        // Clear columns
        cols.forEach(c => { 
            const el = document.getElementById(`feed-${c}`);
            if (el) el.innerHTML = ''; 
        });

        if (Array.isArray(data) && data.length > 0) {
            data.forEach(raw => {
                const item = normalize(raw);
                prependCard(item);
            });
        }
        
        // After populating, add "No data" message to any column that remained empty
        cols.forEach(c => {
            const el = document.getElementById(`feed-${c}`);
            if (el && el.children.length === 0) {
                el.innerHTML = `<p style="font-size:0.7rem;color:var(--text-secondary);text-align:center;padding:20px;">No data for today yet.</p>`;
            }
        });
        
    } catch (err) {
        console.error("Failed to load feed", err);
        cols.forEach(c => {
            const el = document.getElementById(`feed-${c}`);
            if(el) el.innerHTML = `<p style="font-size:0.7rem;color:#f85149;text-align:center;padding:20px;">Connection Error</p>`;
        });
    }
}

// ── WebSocket — real-time push ──────────────────────────────────────────────
function connectWS() {
    if (ws) {
        ws.onopen = ws.onmessage = ws.onclose = null;
        ws.close();
    }

    ws = new WebSocket(WS_URL);
    
    ws.onopen = () => {
        console.log("WebSocket connected");
        statusEl.innerText = 'Connected';
        statusEl.parentElement.style.opacity = '1';
        statusEl.className = ''; // Remove any error classes
    };

    ws.onmessage = (e) => {
        try {
            const raw  = JSON.parse(e.data);
            const item = normalize(raw);
            prependCard(item);
        } catch (err) {
            console.error("WS Message Error:", err);
        }
    };

    ws.onclose = () => {
        statusEl.innerText = 'Reconnecting...';
        statusEl.style.color = 'var(--warning)';
        setTimeout(connectWS, 5000); // 5 second retry
    };

    ws.onerror = (err) => {
        console.error("WebSocket Error:", err);
        ws.close();
    };
}

// ── Main Tabs Logic ─────────────────────────────────────────────────────────
document.querySelectorAll('.main-tab').forEach(tab => {
    tab.addEventListener('click', (e) => {
        // Remove active class from all tabs and panes
        document.querySelectorAll('.main-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.main-tab-pane').forEach(p => p.classList.remove('active'));
        
        // Add active class to clicked tab and corresponding pane
        e.target.classList.add('active');
        const targetId = 'tab-' + e.target.getAttribute('data-target');
        const targetPane = document.getElementById(targetId);
        if (targetPane) targetPane.classList.add('active');
    });
});

// ── Boot ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadFeed();
    connectWS();
    loadScriptsList();
});

// ── Script Analysis Logic ───────────────────────────────────────────────────
let availableScripts = [];
const searchInput = document.getElementById('script-search');
const autocompleteList = document.getElementById('autocomplete-list');
const analysisResults = document.getElementById('analysis-results');
const analysisLoading = document.getElementById('analysis-loading');

async function loadScriptsList() {
    try {
        const res = await fetch(`${API_BASE}/api/scripts`);
        const data = await res.json();
        if (data.scripts) {
            availableScripts = data.scripts;
        }
    } catch (e) {
        console.error("Failed to load scripts list", e);
    }
}

// Autocomplete Input Handler
if (searchInput) {
    searchInput.addEventListener('input', function() {
        const val = this.value;
        autocompleteList.innerHTML = '';
        if (!val) {
            autocompleteList.style.display = 'none';
            return;
        }
        
        const matches = availableScripts.filter(s => s.toLowerCase().includes(val.toLowerCase())).slice(0, 50);
        
        if (matches.length > 0) {
            autocompleteList.style.display = 'block';
            matches.forEach(match => {
                const item = document.createElement('div');
                // Highlight matching part
                const regex = new RegExp(`(${val})`, "gi");
                item.innerHTML = match.replace(regex, "<strong>$1</strong>");
                
                item.addEventListener('click', () => {
                    searchInput.value = match;
                    autocompleteList.innerHTML = '';
                    autocompleteList.style.display = 'none';
                    fetchAnalysis(match);
                });
                autocompleteList.appendChild(item);
            });
        } else {
            autocompleteList.style.display = 'none';
        }
    });

    // Close autocomplete when clicking outside
    document.addEventListener('click', (e) => {
        if (e.target !== searchInput) {
            autocompleteList.innerHTML = '';
            autocompleteList.style.display = 'none';
        }
    });
}

async function fetchAnalysis(symbol) {
    analysisResults.style.display = 'none';
    analysisLoading.style.display = 'block';
    
    try {
        const res = await fetch(`${API_BASE}/api/analyze/${symbol}`);
        const data = await res.json();
        
        analysisLoading.style.display = 'none';
        
        if (data.status === 'success') {
            document.getElementById('script-name').innerText = data.symbol;
            document.getElementById('script-ltp').innerText = data.ltp ? `₹${parseFloat(data.ltp).toFixed(2)}` : 'N/A';
            
            // Populate Analysis Dashboard
            if (data.analysis) {
                document.getElementById('trend-val').innerText = data.analysis.trend || '--';
                
                // Color code the trend text
                const trendText = data.analysis.trend || '';
                if (trendText.includes('Bullish')) document.getElementById('trend-val').style.color = 'var(--success)';
                else if (trendText.includes('Bearish')) document.getElementById('trend-val').style.color = '#ef4444';
                else document.getElementById('trend-val').style.color = 'var(--text-primary)';
                
                document.getElementById('sma50-val').innerText = data.analysis.sma_50 ? `₹${data.analysis.sma_50}` : '--';
                document.getElementById('sma200-val').innerText = data.analysis.sma_200 ? `₹${data.analysis.sma_200}` : '--';
                document.getElementById('res-val').innerText = data.analysis.resistance ? `₹${data.analysis.resistance}` : '--';
                document.getElementById('sup-val').innerText = data.analysis.support ? `₹${data.analysis.support}` : '--';
                
                // Populate Momentum Factors
                if (data.analysis.rsi !== undefined) {
                    const rsi = data.analysis.rsi;
                    let rsiColor = 'var(--text-primary)';
                    if (rsi > 70) rsiColor = '#ef4444'; // Overbought
                    else if (rsi < 30) rsiColor = 'var(--success)'; // Oversold
                    document.getElementById('rsi-val').innerHTML = `<span style="color:${rsiColor}">${rsi}</span>`;
                }
                
                if (data.analysis.vol_spike !== undefined) {
                    const spike = data.analysis.vol_spike;
                    let spikeColor = spike > 2.0 ? 'var(--success)' : 'var(--text-primary)';
                    document.getElementById('vol-spike-val').innerHTML = `<span style="color:${spikeColor}">${spike}x</span>`;
                }
                
                if (data.analysis.macd_status) {
                    const macd = data.analysis.macd_status;
                    let macdColor = macd.includes('Bullish') ? 'var(--success)' : (macd.includes('Bearish') ? '#ef4444' : 'var(--text-primary)');
                    document.getElementById('macd-val').innerHTML = `<span style="color:${macdColor}">${macd}</span>`;
                }
                
                if (data.analysis.pct_change !== undefined) {
                    const pct = data.analysis.pct_change;
                    let pctColor = pct > 0 ? 'var(--success)' : (pct < 0 ? '#ef4444' : 'var(--text-primary)');
                    const sign = pct > 0 ? '+' : '';
                    document.getElementById('pct-change-val').innerHTML = `<span style="color:${pctColor}">${sign}${pct}%</span>`;
                }
                
                if (data.analysis.dist_from_high !== undefined) {
                    document.getElementById('dist-high-val').innerText = `${data.analysis.dist_from_high}% down`;
                }
            }
            
            const tbody = document.getElementById('historical-body');
            tbody.innerHTML = '';
            
            if (data.historical && data.historical.length > 0) {
                // Reverse to show newest first
                const recent = [...data.historical].reverse();
                
                recent.forEach((day, index) => {
                    const row = document.createElement('tr');
                    
                    // Determine if it was an up or down day for coloring the close price
                    let colorClass = '';
                    if (index < recent.length - 1) {
                        const prevClose = recent[index+1].Close;
                        if (day.Close > prevClose) colorClass = 'up-day';
                        else if (day.Close < prevClose) colorClass = 'down-day';
                    }
                    
                    row.innerHTML = `
                        <td>${day.Date}</td>
                        <td>${parseFloat(day.Open).toFixed(2)}</td>
                        <td>${parseFloat(day.High).toFixed(2)}</td>
                        <td>${parseFloat(day.Low).toFixed(2)}</td>
                        <td class="${colorClass}">${parseFloat(day.Close).toFixed(2)}</td>
                        <td>${day.Volume.toLocaleString()}</td>
                    `;
                    tbody.appendChild(row);
                });
            } else {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-secondary);">No historical data available.</td></tr>';
            }
            
            analysisResults.style.display = 'flex';
        } else {
            alert('Could not fetch data for this script: ' + data.message);
        }
    } catch (e) {
        analysisLoading.style.display = 'none';
        console.error("Analysis fetch failed", e);
        alert('Failed to connect to the server.');
    }
}
